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
    """Converte uma linha do SQLite local para payload do Supabase.

    PostgREST exige TODAS as linhas do batch com as MESMAS chaves — por isso
    sempre incluimos metadata (mesmo None) no payload.
    """
    metadata = None
    raw_meta = row.get("metadata")
    if raw_meta:
        try:
            metadata = json.loads(raw_meta)
        except (json.JSONDecodeError, TypeError):
            metadata = None
    return {
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
        "metadata": metadata,
        "local_id": row["id"],
    }


def push_batch(rows: list[dict], url: str, key: str, max_retries: int = 3) -> tuple[int, int]:
    """POST batch para Supabase REST API com retry exponencial. Retorna (success_count, error_count).

    Usa Prefer: resolution=ignore-duplicates que funciona com qualquer constraint
    UNIQUE existente (incluindo expressional). Linhas ja sincronizadas sao
    silenciosamente ignoradas pelo Postgres via ON CONFLICT DO NOTHING.

    Retry com backoff exponencial em 4xx (exceto 401/403/422) e 5xx + erros de rede.
    """
    import time
    if not rows:
        return 0, 0
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates,return=minimal",
    }
    payload = [_row_to_payload(r) for r in rows]
    endpoint = f"{url.rstrip('/')}/rest/v1/finops_calls"

    last_error = ""
    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(timeout=60) as c:
                r = c.post(endpoint, headers=headers, json=payload)
            if r.status_code in (200, 201, 204):
                return len(rows), 0
            # 409 Conflict = todas as rows do batch ja existem (dedup do Postgres).
            # Eh um sucesso silencioso: idempotencia funcionando, marcar como synced.
            if r.status_code == 409 and "23505" in r.text:
                logger.debug("Supabase 409 (todas duplicatas, dedup OK): %d rows", len(rows))
                return len(rows), 0
            # 4xx fatais (auth/permissoes/validacao) — nao adianta retry
            if r.status_code in (401, 403, 422):
                logger.error("Supabase HTTP %d (fatal): %s", r.status_code, r.text[:300])
                return 0, len(rows)
            # 5xx ou 4xx transitorio — retry com backoff
            last_error = f"HTTP {r.status_code}: {r.text[:200]}"
            logger.warning("Supabase %s, retry %d/%d", last_error, attempt + 1, max_retries)
        except httpx.RequestError as exc:
            last_error = f"RequestError: {exc}"
            logger.warning("Supabase rede, retry %d/%d: %s", attempt + 1, max_retries, exc)

        if attempt < max_retries:
            backoff = (2 ** attempt) + (attempt * 0.5)
            time.sleep(backoff)

    logger.error("Supabase batch falhou apos %d retries: %s", max_retries, last_error)
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
