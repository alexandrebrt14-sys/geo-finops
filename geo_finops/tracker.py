"""API publica de tracking — usada pelos adapters dos 4 projetos.

Uso minimo:
    from geo_finops import track_call
    track_call(
        project="geo-orchestrator",
        model_id="claude-opus-4-6",
        tokens_in=500,
        tokens_out=200,
        cost_usd=0.022,
        run_id="20260407_180740",
        task_type="architecture",
        success=True,
    )
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from .db import get_connection, init_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inferencia de provider a partir do model_id
# ---------------------------------------------------------------------------

def _infer_provider(model_id: str) -> str:
    if not model_id:
        return "unknown"
    m = model_id.lower()
    if "claude" in m or "anthropic" in m:
        return "anthropic"
    if "gpt" in m or m.startswith(("o1", "o3", "o4")) or "openai" in m:
        return "openai"
    if "gemini" in m or "gemma" in m:
        return "google"
    if "sonar" in m or "perplex" in m:
        return "perplexity"
    if "llama" in m or "kimi" in m or "qwen" in m or "mixtral" in m:
        return "groq"
    return "unknown"


# ---------------------------------------------------------------------------
# run_id helper para sessoes
# ---------------------------------------------------------------------------

def run_id_for_session(project: str | None = None) -> str:
    """Gera um run_id timestamp-based agrupando calls de uma mesma sessao."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]


# ---------------------------------------------------------------------------
# Track call (API principal)
# ---------------------------------------------------------------------------

def _normalize_timestamp(ts: str | None) -> str:
    """Normaliza timestamp para ISO 8601 UTC com microsegundos.

    Garante que track_call de diferentes paths (Python local, server-side
    Next.js, etc.) sempre gere o mesmo formato — evita falha do dedup
    quando o mesmo logico-call eh registrado por dois caminhos.
    """
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        # Aceita timestamps com ou sem microseg, com Z ou +00:00
        clean = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return datetime.now(timezone.utc).isoformat()


def track_call(
    project: str,
    model_id: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    run_id: str | None = None,
    task_type: str | None = None,
    success: bool = True,
    provider: str | None = None,
    timestamp: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int | None:
    """Grava uma chamada LLM no banco centralizado.

    Args:
        project: identificador do projeto (ex: "geo-orchestrator", "papers")
        model_id: id real do modelo (ex: "claude-opus-4-6", "gpt-4o-mini")
        tokens_in: tokens de input
        tokens_out: tokens de output
        cost_usd: custo em USD
        run_id: agrupa calls da mesma execucao (opcional)
        task_type: tipo da tarefa (research, code, etc) (opcional)
        success: True se a call foi bem sucedida
        provider: opcional, infere de model_id se nao informado
        timestamp: ISO 8601 UTC; default = now
        metadata: dict opcional, serializado como JSON

    Returns:
        ID da linha inserida, ou None em caso de duplicata (dedup) ou erro.
    """
    init_db()  # idempotente

    timestamp = _normalize_timestamp(timestamp)
    if provider is None:
        provider = _infer_provider(model_id)

    metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO llm_calls
                (timestamp, project, run_id, task_type, model_id, provider,
                 tokens_in, tokens_out, cost_usd, success, metadata, sync_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                timestamp, project, run_id, task_type, model_id, provider,
                int(tokens_in or 0), int(tokens_out or 0), float(cost_usd or 0),
                1 if success else 0, metadata_json,
            ),
        )
        if cur.rowcount == 0:
            logger.debug(
                "geo_finops: dedup hit (project=%s run_id=%s model=%s ts=%s)",
                project, run_id, model_id, timestamp,
            )
            return None
        return cur.lastrowid
    except sqlite3.Error as exc:
        logger.error("geo_finops: track_call falhou: %s", exc)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def query_calls(
    project: str | None = None,
    provider: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    """Query calls com filtros opcionais."""
    init_db()
    sql = "SELECT * FROM llm_calls WHERE 1=1"
    params: list = []
    if project:
        sql += " AND project = ?"
        params.append(project)
    if provider:
        sql += " AND provider = ?"
        params.append(provider)
    if start:
        sql += " AND timestamp >= ?"
        params.append(start)
    if end:
        sql += " AND timestamp <= ?"
        params.append(end)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(int(limit))

    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def aggregate_by(
    field: str,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """Agrega cost_usd por field (provider/project/model_id/task_type)."""
    if field not in {"provider", "project", "model_id", "task_type"}:
        raise ValueError(f"field invalido: {field}")
    init_db()
    sql = f"""
        SELECT {field} as key,
               COUNT(*) as calls,
               SUM(tokens_in) as tokens_in,
               SUM(tokens_out) as tokens_out,
               SUM(cost_usd) as cost_usd
        FROM llm_calls
        WHERE 1=1
    """
    params: list = []
    if start:
        sql += " AND timestamp >= ?"
        params.append(start)
    if end:
        sql += " AND timestamp <= ?"
        params.append(end)
    sql += f" GROUP BY {field} ORDER BY cost_usd DESC"

    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
