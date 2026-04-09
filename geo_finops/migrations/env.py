"""Alembic environment — resolve URL dinamicamente via geo_finops.db.

Achado B-014 da auditoria de ecossistema 2026-04-08. Configuracao
minimal que respeita o caminho dinamico do banco (GEO_FINOPS_DB_PATH
+ XDG default ~/.config/geo-finops/calls.db).

Por que sem SQLAlchemy ORM:
- O geo-finops nao usa SQLAlchemy. O schema eh definido como SQL
  raw em geo_finops/db.py (SCHEMA constant). Migrations sao escritas
  como SQL raw via op.execute(), nao com autogenerate.
- Isso mantem a dependencia minima (so alembic + sqlalchemy core que
  eh transitivo) e evita refactor invasivo.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Garantir que geo_finops eh importavel a partir do CWD
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from geo_finops.db import get_db_path  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve URL do banco em runtime — respeita GEO_FINOPS_DB_PATH
# Mas TAMBEM respeita uma URL passada externamente via
# config.set_main_option (utilizado por testes que injetam DBs isolados).
existing_url = config.get_main_option("sqlalchemy.url")
if not existing_url:
    db_path = get_db_path()
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")

# Sem ORM, target_metadata = None (usamos op.execute com SQL raw)
target_metadata = None


def run_migrations_offline() -> None:
    """Modo offline: emite SQL para stdout sem conectar ao DB."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Modo online: conecta ao DB e aplica migrations."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
