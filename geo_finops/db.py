"""Banco SQLite local centralizado em ``~/.config/geo-finops/calls.db``.

Schema unico, indexes para queries rapidas, dedup garantido por
``(timestamp, project, run_id, model_id)`` — UNIQUE constraint.

A resolucao do path agora vive em ``geo_finops.config.get_db_path`` para
permitir override consistente via ``GEO_FINOPS_DB_PATH`` ou
``GEO_FINOPS_CONFIG_DIR`` em todo o pacote.
"""

from __future__ import annotations

import sqlite3

from .config import get_db_path

__all__ = ["SCHEMA", "get_connection", "get_db_path", "init_db"]


SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,
    project      TEXT    NOT NULL,
    run_id       TEXT,
    task_type    TEXT,
    model_id     TEXT    NOT NULL,
    provider     TEXT    NOT NULL,
    tokens_in    INTEGER NOT NULL DEFAULT 0,
    tokens_out   INTEGER NOT NULL DEFAULT 0,
    cost_usd     REAL    NOT NULL DEFAULT 0.0,
    success      INTEGER NOT NULL DEFAULT 1,
    metadata     TEXT,
    sync_status  TEXT    NOT NULL DEFAULT 'pending',
    synced_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_timestamp ON llm_calls(timestamp);
CREATE INDEX IF NOT EXISTS idx_llm_calls_project   ON llm_calls(project);
CREATE INDEX IF NOT EXISTS idx_llm_calls_provider  ON llm_calls(provider);
CREATE INDEX IF NOT EXISTS idx_llm_calls_model     ON llm_calls(model_id);
CREATE INDEX IF NOT EXISTS idx_llm_calls_sync      ON llm_calls(sync_status);
CREATE INDEX IF NOT EXISTS idx_llm_calls_run       ON llm_calls(run_id);

-- Dedup: mesma tarefa/run nao grava 2x
CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_calls_dedup
    ON llm_calls(timestamp, project, COALESCE(run_id, ''), model_id);

-- Tabela de migracoes ja aplicadas
CREATE TABLE IF NOT EXISTS migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    source_file TEXT,
    rows_added  INTEGER DEFAULT 0
);
"""


def get_connection() -> sqlite3.Connection:
    """Retorna conexao SQLite com WAL mode (concorrencia leitura/escrita).

    Autocommit (``isolation_level=None``) porque os callers gerenciam
    transacoes via PRAGMA/INSERT direto. ``busy_timeout=10000`` (10s)
    tolera sync worker + CLI ativos simultaneamente.
    """
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Cria schema se nao existe (idempotente)."""
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
