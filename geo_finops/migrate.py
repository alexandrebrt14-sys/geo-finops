"""Migracao dos historicos legados para o calls.db unificado.

Le:
- geo-orchestrator/output/cost_history.jsonl
- geo-orchestrator/output/execution_*.json (mais detalhado, preferido)
- curso-factory/output/costs.json
- papers/data/papers.db::finops_usage
- caramaschi/src/scripts/ac_core/finops (se existir)

Idempotente via UNIQUE constraint (timestamp, project, run_id, model_id).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .db import get_connection, init_db
from .tracker import track_call, _infer_provider

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]  # C:/Sandyboxclaude


def _record_migration(name: str, source: str, rows: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO migrations (name, applied_at, source_file, rows_added) VALUES (?, ?, ?, ?)",
            (name, datetime.now(timezone.utc).isoformat(), source, rows),
        )
    finally:
        conn.close()


def migrate_orchestrator() -> int:
    """Migra geo-orchestrator/output/execution_*.json (detalhe por task).

    run_id inclui task_id para evitar dedup colapsar tasks com mesmo modelo
    (todas as tasks da mesma execucao compartilham timestamp).
    """
    out_dir = ROOT / "geo-orchestrator" / "output"
    files = sorted(out_dir.glob("execution_*.json"))
    inserted = 0
    for fp in files:
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        base_run_id = fp.stem.replace("execution_", "")
        base_ts = d.get("timestamp", datetime.now(timezone.utc).isoformat())
        for r in d.get("results", []):
            task_id = r.get("task_id") or "T?"
            ok = track_call(
                project="geo-orchestrator",
                model_id=r.get("model_used") or "unknown",
                tokens_in=r.get("tokens_input") or 0,
                tokens_out=r.get("tokens_output") or 0,
                cost_usd=r.get("cost_usd") or 0,
                run_id=f"{base_run_id}:{task_id}",
                task_type=r.get("task_type"),
                success=bool(r.get("success", True)),
                timestamp=base_ts,
                metadata={"agent": r.get("agent_name"), "task_id": task_id, "execution_run": base_run_id},
            )
            if ok:
                inserted += 1
    _record_migration("orchestrator", str(out_dir), inserted)
    return inserted


def migrate_curso_factory() -> int:
    """Migra curso-factory/output/costs.json."""
    fp = ROOT / "curso-factory" / "output" / "costs.json"
    if not fp.exists():
        return 0
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    inserted = 0
    for d in data:
        ok = track_call(
            project="curso-factory",
            model_id=d.get("model") or "unknown",
            tokens_in=d.get("tokens_in") or 0,
            tokens_out=d.get("tokens_out") or 0,
            cost_usd=d.get("custo_usd") or 0,
            run_id=d.get("sessao"),
            task_type=d.get("course_id") or "course_generation",
            success=True,
            provider=d.get("provider"),
            timestamp=d.get("timestamp"),
        )
        if ok:
            inserted += 1
    _record_migration("curso-factory", str(fp), inserted)
    return inserted


def migrate_papers() -> int:
    """Migra papers/data/papers.db::finops_usage."""
    fp = ROOT / "papers" / "data" / "papers.db"
    if not fp.exists():
        return 0
    inserted = 0
    try:
        src = sqlite3.connect(str(fp))
        src.row_factory = sqlite3.Row
        for r in src.execute("SELECT * FROM finops_usage"):
            ok = track_call(
                project="papers",
                model_id=r["model"] or "unknown",
                tokens_in=r["input_tokens"] or 0,
                tokens_out=r["output_tokens"] or 0,
                cost_usd=r["cost_usd"] or 0,
                run_id=r["run_id"],
                task_type=r["operation"] or r["vertical"],
                success=True,
                provider=r["platform"],
                timestamp=r["timestamp"],
                metadata={"vertical": r["vertical"], "query": (r["query"] or "")[:200]},
            )
            if ok:
                inserted += 1
        src.close()
    except sqlite3.Error as exc:
        logger.error("migrate_papers falhou: %s", exc)
    _record_migration("papers", str(fp), inserted)
    return inserted


def migrate_caramaschi() -> int:
    """Migra caramaschi se houver tracker estruturado."""
    candidates = [
        ROOT / "caramaschi" / "src" / "scripts" / "ac_core" / "finops_log.jsonl",
        ROOT / "caramaschi" / "src" / "data" / "finops_log.jsonl",
        ROOT / "caramaschi" / "data" / "finops_log.jsonl",
    ]
    inserted = 0
    for fp in candidates:
        if not fp.exists():
            continue
        for line in fp.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ok = track_call(
                project="caramaschi",
                model_id=d.get("model") or "unknown",
                tokens_in=d.get("tokens_in") or 0,
                tokens_out=d.get("tokens_out") or 0,
                cost_usd=d.get("cost_usd") or d.get("custo_usd") or 0,
                run_id=d.get("run_id") or d.get("sessao"),
                task_type=d.get("task_type") or d.get("operation"),
                success=bool(d.get("success", True)),
                timestamp=d.get("timestamp"),
            )
            if ok:
                inserted += 1
        _record_migration(f"caramaschi:{fp.name}", str(fp), inserted)
    return inserted


def migrate_all() -> dict:
    """Roda todas as migracoes e retorna sumario."""
    init_db()
    return {
        "orchestrator": migrate_orchestrator(),
        "papers": migrate_papers(),
        "curso-factory": migrate_curso_factory(),
        "caramaschi": migrate_caramaschi(),
    }
