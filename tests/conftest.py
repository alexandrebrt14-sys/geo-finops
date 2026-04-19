"""Fixtures compartilhadas: DB isolado por teste via ``GEO_FINOPS_DB_PATH``.

Os testes que tocam no SQLite usam a fixture ``isolated_db``, que aponta
``GEO_FINOPS_DB_PATH`` para um arquivo no ``tmp_path`` do pytest. Assim,
nada vaza para ``~/.config/geo-finops/calls.db`` do usuario.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def isolated_db(monkeypatch, tmp_path: Path):
    """Aponta GEO_FINOPS_DB_PATH para um arquivo temporario e inicializa schema.

    Yields:
        Path absoluto do banco isolado (util para assertivas de integridade).
    """
    db_file = tmp_path / "isolated.db"
    monkeypatch.setenv("GEO_FINOPS_DB_PATH", str(db_file))

    # Garante que os modulos vejam a env var fresca
    from geo_finops import db as _db

    importlib.reload(_db)
    _db.init_db()

    # E tambem os dois re-importers principais
    from geo_finops import (
        aggregates,  # noqa: F401
        tracker,  # noqa: F401
    )

    yield db_file
