"""Validade declarada pelo proprio digest.

Antes de 2026-08-09 o payload trazia so `generated_at`, que no momento da
geracao e sempre "agora" e por isso nunca denuncia nada. O endpoint publico
serviu `generated_at: 2026-04-19` por 112 dias com cara de numero atual.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from geo_finops.digest.builders import (  # noqa: E402
    VALIDADE_HORAS,
    _carimbo_de_validade,
    digest_esta_obsoleto,
)


def test_carimbo_traz_os_tres_campos():
    c = _carimbo_de_validade()
    assert set(c) == {"generated_at", "valid_for_hours", "stale_after"}
    assert c["valid_for_hours"] == VALIDADE_HORAS
    nascimento = datetime.fromisoformat(c["generated_at"])
    prazo = datetime.fromisoformat(c["stale_after"])
    assert prazo - nascimento == timedelta(hours=VALIDADE_HORAS)


def test_digest_recem_gerado_nao_esta_obsoleto():
    assert digest_esta_obsoleto(_carimbo_de_validade()) is False


def test_digest_passado_do_prazo_esta_obsoleto():
    c = _carimbo_de_validade()
    depois = datetime.now(timezone.utc) + timedelta(hours=VALIDADE_HORAS + 1)
    assert digest_esta_obsoleto(c, agora=depois) is True


def test_o_caso_real_de_112_dias():
    # O payload que o endpoint serviu de 19/04 a 09/08/2026, sem stale_after.
    antigo = {"generated_at": "2026-04-19T12:00:00+00:00"}
    agora = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
    assert digest_esta_obsoleto(antigo, agora=agora) is True


def test_payload_sem_data_e_tratado_como_obsoleto():
    # Nao saber quando nasceu nao pode virar presuncao de frescor.
    assert digest_esta_obsoleto({}) is True
    assert digest_esta_obsoleto({"generated_at": "nao-e-data"}) is True
    assert digest_esta_obsoleto({"stale_after": "nao-e-data"}) is True


def test_prazo_sem_fuso_e_lido_como_utc():
    # Robustez: digest gerado por versao que serializava sem tzinfo.
    futuro = (datetime.now(timezone.utc) + timedelta(hours=5)).replace(tzinfo=None)
    assert digest_esta_obsoleto({"stale_after": futuro.isoformat()}) is False
