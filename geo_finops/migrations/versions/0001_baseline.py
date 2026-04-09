"""baseline — schema atual do calls.db (B-014)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-04-09

Esta migration eh o BASELINE da versionagem Alembic. Documenta o estado
do schema apos o achado B-014 da auditoria de ecossistema 2026-04-08.

NAO recria a tabela do zero — usa CREATE TABLE IF NOT EXISTS para ser
idempotente com bancos existentes que ja foram criados via init_db().
A funcao init_db() em geo_finops/db.py continua sendo o caminho primario
para criar o schema; Alembic eh apenas para migrations futuras.

Como rodar (depois desta baseline ja estar marcada como aplicada):
    alembic -c alembic.ini current
    alembic -c alembic.ini revision -m "add column foo"
    alembic -c alembic.ini upgrade head

Para marcar um banco existente como ja-no-baseline (sem rodar SQL):
    alembic -c alembic.ini stamp 0001_baseline
"""
from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


SCHEMA_LLM_CALLS = """
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
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_timestamp ON llm_calls(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_project   ON llm_calls(project);",
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_provider  ON llm_calls(provider);",
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_model     ON llm_calls(model_id);",
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_sync      ON llm_calls(sync_status);",
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_run       ON llm_calls(run_id);",
    # Dedup expressional UNIQUE
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_calls_dedup "
    "ON llm_calls(timestamp, project, COALESCE(run_id, ''), model_id);",
]

SCHEMA_MIGRATIONS_LEGACY = """
CREATE TABLE IF NOT EXISTS migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    source_file TEXT,
    rows_added  INTEGER DEFAULT 0
);
"""


def upgrade() -> None:
    """Cria o schema baseline. Idempotente via IF NOT EXISTS."""
    op.execute(SCHEMA_LLM_CALLS)
    for stmt in INDEXES:
        op.execute(stmt)
    # Tabela legada de tracking de bulk imports — preservada por compat
    op.execute(SCHEMA_MIGRATIONS_LEGACY)


def downgrade() -> None:
    """Downgrade do baseline destruiria todos os dados — bloqueado."""
    raise RuntimeError(
        "Downgrade do baseline geo-finops eh DESTRUTIVO. "
        "Para realmente fazer isso, edite esta migration manualmente."
    )
