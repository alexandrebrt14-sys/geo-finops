"""Funcoes de agregacao reusaveis sobre ``llm_calls``.

Consolida queries que antes eram repetidas em ``tracker.aggregate_by``,
``scripts/export_snapshot.py`` e ``scripts/aggregate_dashboard.py``. Cada
copia tinha pequenas variacoes de GROUP BY / ORDER BY, o que dificultava
cruzar dashboards e introduzia bugs sutis (ex: snapshot contava
``providers`` excluindo ``unknown`` mas o digest nao).

Todas as funcoes aceitam:

- Uma conexao SQLite opcional (``conn``). Se ``None``, abre/fecha
  automaticamente via ``get_connection``.
- Janela temporal opcional (``start``, ``end``) como ISO 8601 strings.

Retornam sempre ``list[dict]`` com colunas normalizadas:
``{"key", "calls", "tokens_in", "tokens_out", "cost_usd"}`` (com campos
extras quando pertinente, como ``provider`` em ``top_models``).

Usar sempre este modulo ao inves de abrir cursores diretos — para manter
o formato dos dados consistente entre CLI, snapshot, dashboard e digest.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .db import get_connection, init_db

_VALID_GROUP_FIELDS = frozenset({"provider", "project", "model_id", "task_type"})


@contextmanager
def _managed_conn(
    conn: sqlite3.Connection | None,
) -> Iterator[sqlite3.Connection]:
    """Reutiliza conn se fornecida; senao abre uma e fecha no fim."""
    if conn is not None:
        yield conn
        return
    owned = get_connection()
    try:
        yield owned
    finally:
        owned.close()


def _where_time(start: str | None, end: str | None, params: list) -> str:
    """Constroi clause WHERE temporal e acumula params in-place."""
    parts: list[str] = []
    if start:
        parts.append("timestamp >= ?")
        params.append(start)
    if end:
        parts.append("timestamp <= ?")
        params.append(end)
    return (" AND " + " AND ".join(parts)) if parts else ""


# ---------------------------------------------------------------------------
# Totais globais
# ---------------------------------------------------------------------------


def totals(
    *,
    start: str | None = None,
    end: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Totais agregados (calls, cost, tokens, periodo minmax)."""
    init_db()
    params: list = []
    where = _where_time(start, end, params)
    sql = (
        "SELECT COUNT(*) AS calls,"
        "       COALESCE(SUM(cost_usd), 0) AS cost_usd,"
        "       COALESCE(SUM(tokens_in), 0) AS tokens_in,"
        "       COALESCE(SUM(tokens_out), 0) AS tokens_out,"
        "       MIN(timestamp) AS period_start,"
        "       MAX(timestamp) AS period_end"
        f" FROM llm_calls WHERE 1=1{where}"
    )
    with _managed_conn(conn) as c:
        row = c.execute(sql, params).fetchone()
    return (
        dict(row)
        if row
        else {
            "calls": 0,
            "cost_usd": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "period_start": None,
            "period_end": None,
        }
    )


# ---------------------------------------------------------------------------
# Agregacao por dimensao
# ---------------------------------------------------------------------------


def aggregate_by(
    field: str,
    *,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict]:
    """Agrega ``llm_calls`` por uma das dimensoes permitidas.

    Args:
        field: Uma de ``provider``, ``project``, ``model_id``, ``task_type``.
        start/end: Janela temporal ISO 8601 (opcional).
        limit: Limite opcional de linhas retornadas (``None`` = todas).
        conn: Conexao SQLite existente (opcional, usada em batches).

    Returns:
        Lista de dicts ordenada por ``cost_usd DESC``.

    Raises:
        ValueError: se ``field`` nao esta na lista permitida.
    """
    if field not in _VALID_GROUP_FIELDS:
        raise ValueError(f"field invalido: {field}. Opcoes: {sorted(_VALID_GROUP_FIELDS)}")
    init_db()
    params: list = []
    where = _where_time(start, end, params)
    sql = (
        f"SELECT {field} AS key,"
        "       COUNT(*) AS calls,"
        "       COALESCE(SUM(tokens_in), 0) AS tokens_in,"
        "       COALESCE(SUM(tokens_out), 0) AS tokens_out,"
        "       COALESCE(SUM(cost_usd), 0) AS cost_usd"
        f" FROM llm_calls WHERE 1=1{where}"
        f" GROUP BY {field} ORDER BY cost_usd DESC"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with _managed_conn(conn) as c:
        rows = c.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Top modelos (inclui provider associado)
# ---------------------------------------------------------------------------


def top_models(
    *,
    start: str | None = None,
    end: str | None = None,
    limit: int = 15,
    conn: sqlite3.Connection | None = None,
) -> list[dict]:
    """Top ``limit`` modelos por custo com o provider correspondente."""
    init_db()
    params: list = []
    where = _where_time(start, end, params)
    sql = (
        "SELECT model_id AS key, provider,"
        "       COUNT(*) AS calls,"
        "       COALESCE(SUM(tokens_in), 0) AS tokens_in,"
        "       COALESCE(SUM(tokens_out), 0) AS tokens_out,"
        "       COALESCE(SUM(cost_usd), 0) AS cost_usd"
        f" FROM llm_calls WHERE 1=1{where}"
        " GROUP BY model_id, provider"
        " ORDER BY cost_usd DESC"
        " LIMIT ?"
    )
    params.append(int(limit))
    with _managed_conn(conn) as c:
        rows = c.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Serie diaria
# ---------------------------------------------------------------------------


def daily_timeseries(
    *,
    days: int = 30,
    conn: sqlite3.Connection | None = None,
) -> list[dict]:
    """Serie diaria de custo/calls dos ultimos ``days`` dias.

    Usa ``substr(timestamp, 1, 10)`` para extrair a data. Isso evita
    dependencia da funcao ``date()`` do SQLite (que respeita TZ local).
    Como os timestamps sao todos UTC ISO 8601, a comparacao lexical e
    estavel.
    """
    init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql = (
        "SELECT substr(timestamp, 1, 10) AS date,"
        "       COUNT(*) AS calls,"
        "       COALESCE(SUM(cost_usd), 0) AS cost_usd"
        " FROM llm_calls"
        " WHERE timestamp >= ?"
        " GROUP BY date"
        " ORDER BY date"
    )
    with _managed_conn(conn) as c:
        rows = c.execute(sql, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Top hotspots (project x model x task_type) — usado pelo digest
# ---------------------------------------------------------------------------


def top_hotspots(
    *,
    start: str | None = None,
    end: str | None = None,
    limit: int = 3,
    conn: sqlite3.Connection | None = None,
) -> list[dict]:
    """Top-N combinacoes (project, model_id, task_type) por custo."""
    init_db()
    params: list = []
    where = _where_time(start, end, params)
    sql = (
        "SELECT project, model_id,"
        "       COALESCE(task_type, '?') AS task_type,"
        "       COUNT(*) AS calls,"
        "       ROUND(SUM(cost_usd), 4) AS cost_usd"
        f" FROM llm_calls WHERE 1=1{where}"
        " GROUP BY project, model_id, task_type"
        " ORDER BY cost_usd DESC"
        " LIMIT ?"
    )
    params.append(int(limit))
    with _managed_conn(conn) as c:
        rows = c.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Sync status counters (usado pelo health_check e pelo CLI status)
# ---------------------------------------------------------------------------


def sync_status_counts(
    *,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Contagem de linhas por ``sync_status`` (pending/synced/error)."""
    init_db()
    sql = "SELECT sync_status AS status, COUNT(*) AS n FROM llm_calls GROUP BY sync_status"
    with _managed_conn(conn) as c:
        rows = c.execute(sql).fetchall()
    result = {"pending": 0, "synced": 0, "error": 0}
    for r in rows:
        result[r["status"]] = r["n"]
    return result
