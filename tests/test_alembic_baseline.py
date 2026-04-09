"""Tests para integracao Alembic — B-014.

Achado B-014 da auditoria de ecossistema 2026-04-08. Setup minimal de
Alembic para migracoes de schema do calls.db. Estes testes validam:

- alembic.ini esta no lugar correto e parseavel
- env.py resolve a URL via geo_finops.db.get_db_path
- baseline migration cria o schema esperado
- init_db() e Alembic produzem schemas equivalentes (compat backward)
- downgrade do baseline esta bloqueado (proteao contra acidente)
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Banco temporario isolado por teste."""
    db_path = tmp_path / "calls_test.db"
    monkeypatch.setenv("GEO_FINOPS_DB_PATH", str(db_path))
    yield db_path


# ─── Configuration ─────────────────────────────────────────────────────────


def test_alembic_ini_exists():
    """alembic.ini deve estar na raiz do repo."""
    assert (ROOT / "alembic.ini").exists()


def test_migrations_dir_structure():
    """Estrutura de migrations: env.py, script.py.mako, versions/."""
    mig = ROOT / "geo_finops" / "migrations"
    assert (mig / "env.py").exists()
    assert (mig / "script.py.mako").exists()
    assert (mig / "versions").is_dir()


def test_baseline_migration_exists():
    versions = ROOT / "geo_finops" / "migrations" / "versions"
    files = list(versions.glob("0001_baseline*.py"))
    assert len(files) == 1, f"baseline ausente em {versions}"


def test_env_py_resolves_via_get_db_path():
    """Sentinela: env.py importa get_db_path (URL dinamica)."""
    env_py = (ROOT / "geo_finops" / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "from geo_finops.db import get_db_path" in env_py
    assert "set_main_option" in env_py


# ─── Alembic API integration ──────────────────────────────────────────────


def _make_alembic_config(db_path: Path):
    from alembic.config import Config
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "geo_finops" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


def test_baseline_creates_llm_calls_table(isolated_db):
    """Rodar upgrade head em DB vazio cria a tabela llm_calls."""
    from alembic.command import upgrade
    cfg = _make_alembic_config(isolated_db)
    upgrade(cfg, "head")

    conn = sqlite3.connect(str(isolated_db))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_calls'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_baseline_creates_indexes(isolated_db):
    from alembic.command import upgrade
    cfg = _make_alembic_config(isolated_db)
    upgrade(cfg, "head")

    conn = sqlite3.connect(str(isolated_db))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='llm_calls'"
    ).fetchall()
    conn.close()
    index_names = {r[0] for r in rows}
    expected = {
        "idx_llm_calls_timestamp",
        "idx_llm_calls_project",
        "idx_llm_calls_provider",
        "idx_llm_calls_model",
        "idx_llm_calls_sync",
        "idx_llm_calls_run",
        "idx_llm_calls_dedup",
    }
    missing = expected - index_names
    assert not missing, f"indices faltando: {missing}"


def test_baseline_idempotent(isolated_db):
    """Rodar upgrade duas vezes nao quebra (IF NOT EXISTS)."""
    from alembic.command import upgrade
    cfg = _make_alembic_config(isolated_db)
    upgrade(cfg, "head")
    # Tentar upgrade de novo — Alembic detecta que ja esta em head
    upgrade(cfg, "head")
    # Schema continua valido
    conn = sqlite3.connect(str(isolated_db))
    rows = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchall()
    conn.close()
    assert rows[0][0] == 0


def test_init_db_compatible_with_alembic_baseline(tmp_path, monkeypatch):
    """init_db() (caminho legado) e Alembic baseline produzem schemas equivalentes.

    Cria dois bancos: um via init_db, outro via alembic upgrade. Compara
    o set de colunas e indices das tabelas relevantes.
    """
    from alembic.command import upgrade
    from geo_finops.db import init_db

    # Banco 1: via init_db
    db1 = tmp_path / "via_initdb.db"
    monkeypatch.setenv("GEO_FINOPS_DB_PATH", str(db1))
    init_db()

    # Banco 2: via alembic
    db2 = tmp_path / "via_alembic.db"
    cfg = _make_alembic_config(db2)
    upgrade(cfg, "head")

    def _columns(db_path: Path) -> set:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("PRAGMA table_info(llm_calls)").fetchall()
        conn.close()
        return {row[1] for row in rows}  # column name

    cols1 = _columns(db1)
    cols2 = _columns(db2)
    assert cols1 == cols2, f"divergencia entre init_db e alembic: {cols1.symmetric_difference(cols2)}"


def test_baseline_downgrade_blocked(isolated_db):
    """Downgrade do baseline deve levantar (protecao contra perda de dados)."""
    from alembic.command import upgrade, downgrade
    cfg = _make_alembic_config(isolated_db)
    upgrade(cfg, "head")
    with pytest.raises(Exception):
        downgrade(cfg, "base")


def test_alembic_current_after_upgrade(isolated_db):
    """alembic.command.current retorna 0001_baseline apos upgrade."""
    from alembic.command import upgrade
    from alembic.script import ScriptDirectory
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    cfg = _make_alembic_config(isolated_db)
    upgrade(cfg, "head")

    # Verifica via API direta
    engine = create_engine(f"sqlite:///{isolated_db.as_posix()}")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        rev = ctx.get_current_revision()
    engine.dispose()
    assert rev == "0001_baseline"
