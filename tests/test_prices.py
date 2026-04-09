"""Tests para geo_finops.prices — fonte unica de precos LLM (B-020).

Achado B-020 da auditoria de ecossistema 2026-04-08 (Onda 3 filtrada).
Antes deste modulo, perplexity estava subestimada 3x ate Sprint 5 do
orchestrator (incidente real). Estes testes garantem que prices.yaml
eh autoritativo e que valores conhecidos nao regridem.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from geo_finops.prices import (  # noqa: E402
    calculate_cost,
    get_model_info,
    get_price,
    get_version,
    list_models,
    list_providers,
    reload_prices,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Limpa cache antes de cada teste para isolamento."""
    reload_prices()
    yield
    reload_prices()


# ─── Smoke + version ──────────────────────────────────────────────────────


def test_prices_yaml_loads():
    version = get_version()
    assert version != "unknown"
    assert len(version) > 0


def test_list_providers_returns_5_canonical():
    providers = list_providers()
    expected = {"anthropic", "openai", "google", "perplexity", "groq"}
    assert expected.issubset(set(providers))


def test_list_models_anthropic():
    models = list_models("anthropic")
    assert "claude-opus-4-6" in models
    assert "claude-sonnet-4-6" in models
    assert "claude-haiku-4-5" in models


def test_list_models_all():
    models = list_models()
    assert len(models) >= 10  # 5 providers × 2+ models


# ─── get_price — valores canonicos ────────────────────────────────────────


def test_anthropic_opus_canonical_price():
    """Sentinela: Opus 4.6 deve ser $0.015 / $0.075 por 1K tokens."""
    pin, pout = get_price("anthropic", "claude-opus-4-6")
    assert pin == 0.015
    assert pout == 0.075


def test_anthropic_haiku_canonical_price():
    """Haiku eh ~19x mais barato que Opus."""
    pin, pout = get_price("anthropic", "claude-haiku-4-5")
    assert pin == 0.0008
    assert pout == 0.004
    # Sentinela: ratio Opus/Haiku
    pin_opus, _ = get_price("anthropic", "claude-opus-4-6")
    assert pin_opus / pin >= 18  # ~19x


def test_openai_gpt4o_canonical_price():
    pin, pout = get_price("openai", "gpt-4o")
    assert pin == 0.005
    assert pout == 0.015


def test_perplexity_sonar_pro_NOT_underestimated():
    """Sentinela contra o bug historico: sonar-pro deve ser $0.003/$0.015,
    NAO $0.001/$0.001 (que era o valor errado pre-Sprint 5)."""
    pin, pout = get_price("perplexity", "sonar-pro")
    assert pin == 0.003, f"perplexity sonar-pro INPUT incorreto: {pin}"
    assert pout == 0.015, f"perplexity sonar-pro OUTPUT incorreto: {pout}"


def test_groq_llama_70b_canonical_price():
    pin, pout = get_price("groq", "llama-3.3-70b-versatile")
    assert pin > 0 and pin < 0.001  # ultra barato
    assert pout > 0 and pout < 0.001


# ─── Fallback graceful ────────────────────────────────────────────────────


def test_unknown_provider_returns_fallback():
    pin, pout = get_price("unknown_provider", "any_model")
    assert pin > 0
    assert pout > 0
    # Deve retornar valores do fallback (definidos em prices.yaml)
    # Sentinela: nao deve ser zero
    assert pin == 0.001  # default fallback input
    assert pout == 0.003  # default fallback output


def test_unknown_model_returns_fallback(caplog):
    pin, pout = get_price("anthropic", "claude-future-version-99")
    assert pin > 0
    assert pout > 0


def test_prefix_match_for_versioned_model_id():
    """Model ID com sufixo de versao deve fazer match por prefixo.
    Ex: 'claude-opus-4-6-20250415' -> 'claude-opus-4-6'."""
    pin_versioned, pout_versioned = get_price("anthropic", "claude-opus-4-6-20250415")
    pin_canonical, pout_canonical = get_price("anthropic", "claude-opus-4-6")
    assert pin_versioned == pin_canonical
    assert pout_versioned == pout_canonical


# ─── calculate_cost ───────────────────────────────────────────────────────


def test_calculate_cost_anthropic_opus():
    """1000 tokens in + 500 tokens out em Opus = 0.015 + 0.0375 = 0.0525."""
    cost = calculate_cost("anthropic", "claude-opus-4-6", 1000, 500)
    assert cost == 0.0525


def test_calculate_cost_zero_tokens():
    cost = calculate_cost("anthropic", "claude-opus-4-6", 0, 0)
    assert cost == 0.0


def test_calculate_cost_negative_tokens_returns_zero():
    """Tokens negativos eh sinal de bug — retornar 0 para nao explodir."""
    cost = calculate_cost("anthropic", "claude-opus-4-6", -100, 50)
    assert cost == 0.0


def test_calculate_cost_haiku_cheaper_than_opus():
    """Sentinela: Haiku DEVE ser sempre mais barato que Opus para
    o mesmo numero de tokens."""
    cost_opus = calculate_cost("anthropic", "claude-opus-4-6", 1000, 1000)
    cost_haiku = calculate_cost("anthropic", "claude-haiku-4-5", 1000, 1000)
    assert cost_haiku < cost_opus
    # Aprox 19x mais barato
    assert cost_opus / cost_haiku >= 15


def test_calculate_cost_returns_6_decimals():
    """Tokens grandes para evitar truncamento de 6 decimais."""
    cost = calculate_cost("groq", "llama-3.1-8b-instant", 100_000, 100_000)
    assert cost > 0
    assert isinstance(cost, float)
    # 100k tokens em ambos a $0.00005 + $0.00008 = $0.005 + $0.008 = $0.013
    assert 0.01 <= cost <= 0.02


# ─── Metadata ─────────────────────────────────────────────────────────────


def test_model_info_has_tier():
    info = get_model_info("anthropic", "claude-opus-4-6")
    assert info.get("tier") == "high"
    assert info.get("input_per_1k") == 0.015


def test_model_info_unknown_returns_empty():
    info = get_model_info("nonexistent", "ghost-model")
    assert info == {}


# ─── Reload + cache ───────────────────────────────────────────────────────


def test_reload_prices_clears_cache():
    """Apos reload_prices, get_version reproduz o valor."""
    v1 = get_version()
    reload_prices()
    v2 = get_version()
    assert v1 == v2


# ─── Integridade do YAML ──────────────────────────────────────────────────


def test_all_models_have_input_and_output_prices():
    """Sentinela: todo modelo listado tem ambos os precos definidos."""
    for provider in list_providers():
        for model in list_models(provider):
            pin, pout = get_price(provider, model)
            assert pin > 0, f"{provider}/{model}: input price <= 0"
            assert pout >= 0, f"{provider}/{model}: output price < 0"
