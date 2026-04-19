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

As funcoes de agregacao foram movidas para ``geo_finops.aggregates`` a
partir da refatoracao 2026-04-19. ``aggregate_by`` permanece aqui como
re-export (deprecation-proof) para nao quebrar callers externos.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from .db import get_connection, init_db

logger = logging.getLogger(__name__)

__all__ = [
    "aggregate_by",
    "query_calls",
    "run_id_for_session",
    "track_call",
]


# ---------------------------------------------------------------------------
# Inferencia de provider a partir do model_id
# ---------------------------------------------------------------------------


def _infer_provider(model_id: str | None) -> str:
    """Deriva o provider pelo substring do model_id.

    Mantem match por keywords (e nao por prefixo exato) porque os callers
    usam ids com sufixos de versao (ex: ``claude-opus-4-6-20250415``).
    Retorna ``"unknown"`` quando nada bate — jamais levanta.
    """
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


def run_id_for_session(project: str | None = None) -> str:
    """Gera um run_id timestamp-based agrupando calls de uma mesma sessao.

    Formato: ``YYYYMMDDTHHMMSS_<6hex>``. O sufixo aleatorio evita colisao
    de sessoes iniciadas no mesmo segundo (raro, mas possivel em CI).
    O parametro ``project`` eh aceito por compat — nao afeta o resultado.
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]


# ---------------------------------------------------------------------------
# Normalizacao de timestamps
# ---------------------------------------------------------------------------


def _normalize_timestamp(ts: str | None) -> str:
    """Normaliza timestamp para ISO 8601 UTC.

    Garante que ``track_call`` de diferentes paths (Python local,
    server-side Next.js, cron) sempre gere o mesmo formato — evita falha
    do dedup quando o mesmo logico-call eh registrado por dois caminhos.

    Aceita:
    - ``None`` → now(UTC)
    - String ISO com ``Z`` ou ``+00:00``
    - String naive (assume UTC)
    - Qualquer string ilegivel → now(UTC) com warning silenciado
    """
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        clean = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Track call (API principal)
# ---------------------------------------------------------------------------


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
        project: Identificador do projeto (ex: ``geo-orchestrator``, ``papers``).
        model_id: ID real do modelo (ex: ``claude-opus-4-6``, ``gpt-4o-mini``).
        tokens_in: Tokens de input.
        tokens_out: Tokens de output.
        cost_usd: Custo em USD.
        run_id: Agrupa calls da mesma execucao (opcional).
        task_type: Tipo da tarefa (``research``, ``code``, etc). Opcional.
        success: ``True`` se a call foi bem sucedida.
        provider: Opcional; infere de ``model_id`` se nao informado.
        timestamp: ISO 8601 UTC. Default = ``now``.
        metadata: Dict opcional, serializado como JSON.

    Returns:
        ID da linha inserida, ou ``None`` em caso de duplicata (dedup)
        ou erro de IO. Callers devem tratar ``None`` como no-op.
    """
    init_db()

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
                timestamp,
                project,
                run_id,
                task_type,
                model_id,
                provider,
                int(tokens_in or 0),
                int(tokens_out or 0),
                float(cost_usd or 0),
                1 if success else 0,
                metadata_json,
            ),
        )
        if cur.rowcount == 0:
            logger.debug(
                "geo_finops: dedup hit (project=%s run_id=%s model=%s ts=%s)",
                project,
                run_id,
                model_id,
                timestamp,
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
    """Query calls com filtros opcionais.

    Ordena por timestamp DESC para facilitar listagens "ultimas N".
    """
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
    """Re-export de ``geo_finops.aggregates.aggregate_by`` (compat 1.x).

    A logica foi movida para ``aggregates.py`` para permitir reuso fora
    do tracker. Esta stub-wrapper mantem a API existente intacta.
    """
    from .aggregates import aggregate_by as _agg_by

    return _agg_by(field, start=start, end=end)
