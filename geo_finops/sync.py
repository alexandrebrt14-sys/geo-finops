"""Worker noturno: sincroniza calls pending para Supabase.

Schema Supabase esperado (criar via dashboard ou migration SQL):

    CREATE TABLE IF NOT EXISTS finops_calls (
        id           BIGSERIAL PRIMARY KEY,
        timestamp    TIMESTAMPTZ NOT NULL,
        project      TEXT NOT NULL,
        run_id       TEXT,
        task_type    TEXT,
        model_id     TEXT NOT NULL,
        provider     TEXT NOT NULL,
        tokens_in    INTEGER NOT NULL DEFAULT 0,
        tokens_out   INTEGER NOT NULL DEFAULT 0,
        cost_usd     NUMERIC(10,6) NOT NULL DEFAULT 0,
        success      BOOLEAN NOT NULL DEFAULT TRUE,
        metadata     JSONB,
        local_id     INTEGER,
        synced_at    TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(timestamp, project, run_id, model_id)
    );

Uso:
    python -m geo_finops.sync                    # roda sync
    python -m geo_finops.sync --dry-run          # so mostra o que faria
    python -m geo_finops.sync --batch-size 200   # custom batch size
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

import httpx

from .db import get_connection, init_db

logger = logging.getLogger(__name__)


def _load_supabase_creds() -> tuple[str | None, str | None]:
    """Le SUPABASE_URL e SUPABASE_KEY do .env do orchestrator (fonte canonica)."""
    # Tenta env primeiro
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if url and key:
        return url, key
    # Fallback: le do .env do orchestrator
    from pathlib import Path
    candidates = [
        Path(__file__).resolve().parents[2] / "geo-orchestrator" / ".env",
        Path("C:/Sandyboxclaude/geo-orchestrator/.env"),
    ]
    for env_file in candidates:
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("SUPABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("SUPABASE_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return url, key


def fetch_pending(limit: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM llm_calls WHERE sync_status = 'pending' ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_synced(ids: list[int]) -> None:
    if not ids:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE llm_calls SET sync_status='synced', synced_at=? WHERE id IN ({placeholders})",
            [now, *ids],
        )
    finally:
        conn.close()


def mark_error(ids: list[int]) -> None:
    if not ids:
        return
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE llm_calls SET sync_status='error' WHERE id IN ({placeholders})",
            ids,
        )
    finally:
        conn.close()


def _row_to_payload(row: dict) -> dict:
    payload = {
        "timestamp": row["timestamp"],
        "project": row["project"],
        "run_id": row["run_id"],
        "task_type": row["task_type"],
        "model_id": row["model_id"],
        "provider": row["provider"],
        "tokens_in": row["tokens_in"],
        "tokens_out": row["tokens_out"],
        "cost_usd": row["cost_usd"],
        "success": bool(row["success"]),
        "local_id": row["id"],
    }
    if row.get("metadata"):
        try:
            payload["metadata"] = json.loads(row["metadata"])
        except (json.JSONDecodeError, TypeError):
            payload["metadata"] = None
    return payload


def push_batch(rows: list[dict], url: str, key: str) -> tuple[int, int]:
    """POST batch para Supabase REST API. Retorna (success_count, error_count)."""
    if not rows:
        return 0, 0
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # on_conflict garante upsert idempotente
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    payload = [_row_to_payload(r) for r in rows]
    endpoint = f"{url.rstrip('/')}/rest/v1/finops_calls?on_conflict=timestamp,project,run_id,model_id"
    try:
        with httpx.Client(timeout=30) as c:
            r = c.post(endpoint, headers=headers, json=payload)
        if r.status_code in (200, 201, 204):
            return len(rows), 0
        logger.error("Supabase HTTP %d: %s", r.status_code, r.text[:300])
        return 0, len(rows)
    except httpx.RequestError as exc:
        logger.error("Supabase erro de rede: %s", exc)
        return 0, len(rows)


def sync(batch_size: int = 500, dry_run: bool = False) -> dict:
    """Roda sync ate esgotar pending ou bater limite de seguranca."""
    init_db()
    url, key = _load_supabase_creds()
    if not url or not key:
        return {"status": "no_credentials", "synced": 0, "errors": 0}

    total_synced = 0
    total_error = 0
    safety_limit = 50  # max iteracoes (= 25k linhas se batch=500)
    iterations = 0

    while iterations < safety_limit:
        rows = fetch_pending(batch_size)
        if not rows:
            break
        iterations += 1
        ids = [r["id"] for r in rows]
        if dry_run:
            print(f"[DRY] iteracao {iterations}: {len(rows)} linhas pending")
            total_synced += len(rows)
            break  # nao loop em dry-run
        ok, err = push_batch(rows, url, key)
        if ok > 0:
            mark_synced(ids[:ok])
            total_synced += ok
        if err > 0:
            mark_error(ids[ok:])
            total_error += err
            break  # para se houve erro

    return {
        "status": "ok",
        "synced": total_synced,
        "errors": total_error,
        "iterations": iterations,
        "dry_run": dry_run,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = sync(batch_size=args.batch_size, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
