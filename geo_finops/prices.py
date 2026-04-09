"""prices.py — leitura e calculo de custo LLM via prices.yaml.

Achado B-020 da auditoria de ecossistema 2026-04-08 (Onda 3 filtrada).
Antes deste modulo, 4 consumers calculavam custo independentemente
com tabelas hardcoded — perplexity estava subestimada 3x ate Sprint 5
do orchestrator (incidente real documentado em CLAUDE.md).

Este modulo eh a fonte unica de verdade. Consumers devem usar:

    from geo_finops.prices import get_price, calculate_cost

    # Pega tupla (input_per_1k, output_per_1k)
    pin, pout = get_price("anthropic", "claude-opus-4-6")

    # OU calcula custo direto
    cost = calculate_cost(
        provider="anthropic",
        model="claude-opus-4-6",
        tokens_in=1000,
        tokens_out=500,
    )

Lazy loading: o YAML eh carregado apenas no primeiro uso. Cached em
memoria com versao via _PRICES_VERSION para invalidacao manual em
testes.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PRICES_PATH = Path(__file__).resolve().parent / "prices.yaml"


def _load_yaml() -> dict:
    """Carrega o YAML. Cached via lru_cache abaixo."""
    try:
        import yaml  # type: ignore
    except ImportError:
        # PyYAML eh dep transitiva via alembic, mas se nao estiver
        # disponivel, usamos parser minimo embedded
        return _parse_yaml_minimal(_PRICES_PATH.read_text(encoding="utf-8"))

    with open(_PRICES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _parse_yaml_minimal(text: str) -> dict:
    """Parser YAML minimo para o caso PyYAML nao instalado.

    Suporta apenas a estrutura especifica do prices.yaml — nao eh
    parser geral. Usado como fallback defensivo.
    """
    result: dict = {"providers": {}, "fallback": {}}
    current_provider: Optional[str] = None
    current_model: Optional[str] = None
    in_fallback = False
    lines = text.split("\n")

    for raw_line in lines:
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        # Top-level: providers:, fallback:, version:, last_verified:
        if not line.startswith(" "):
            if line.startswith("providers:"):
                in_fallback = False
                continue
            if line.startswith("fallback:"):
                in_fallback = True
                continue
            if line.startswith("version:"):
                result["version"] = line.split(":", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("last_verified:"):
                result["last_verified"] = line.split(":", 1)[1].strip().strip('"').strip("'")
            continue

        if in_fallback:
            stripped = line.strip()
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                k = k.strip()
                v = v.strip()
                if k in ("input_per_1k", "output_per_1k"):
                    try:
                        result["fallback"][k] = float(v)
                    except ValueError:
                        pass
                elif k == "notes":
                    result["fallback"]["notes"] = v.strip('"').strip("'")
            continue

        # Indent 2: provider name
        if line.startswith("  ") and not line.startswith("    "):
            stripped = line.strip()
            if stripped.endswith(":") and "models" not in stripped:
                current_provider = stripped[:-1]
                result["providers"][current_provider] = {"models": {}}
            continue

        # Indent 6: model name (e.g. "      claude-opus-4-6:")
        if line.startswith("      ") and not line.startswith("        "):
            stripped = line.strip()
            if stripped.endswith(":"):
                current_model = stripped[:-1]
                if current_provider:
                    result["providers"][current_provider]["models"][current_model] = {}
            continue

        # Indent 8: model field
        if line.startswith("        ") and current_provider and current_model:
            stripped = line.strip()
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                k = k.strip()
                v = v.strip()
                if k in ("input_per_1k", "output_per_1k"):
                    try:
                        result["providers"][current_provider]["models"][current_model][k] = float(v)
                    except ValueError:
                        pass
                elif k in ("tier", "notes"):
                    result["providers"][current_provider]["models"][current_model][k] = (
                        v.strip('"').strip("'")
                    )

    return result


@lru_cache(maxsize=1)
def _cached_data() -> dict:
    return _load_yaml()


def reload_prices() -> None:
    """Forca recarregamento do YAML — util em testes ou hot-reload."""
    _cached_data.cache_clear()


def get_version() -> str:
    """Retorna a versao do arquivo prices.yaml."""
    data = _cached_data()
    return str(data.get("version", "unknown"))


def get_price(provider: str, model: str) -> tuple[float, float]:
    """Retorna (input_per_1k, output_per_1k) em USD para o modelo.

    Se o (provider, model) nao estiver na tabela, retorna o fallback
    com warning no log. Nunca crasha — projetado para uso em hot path
    de tracking.

    Args:
        provider: Nome do provider (anthropic, openai, google,
                  perplexity, groq).
        model: Model ID exato (claude-opus-4-6, gpt-4o, etc).

    Returns:
        Tupla (input_per_1k_usd, output_per_1k_usd).
    """
    data = _cached_data()
    providers = data.get("providers", {})

    if provider not in providers:
        logger.warning(
            "prices.yaml: provider desconhecido '%s' — usando fallback",
            provider,
        )
        fb = data.get("fallback", {})
        return fb.get("input_per_1k", 0.001), fb.get("output_per_1k", 0.003)

    models = providers[provider].get("models", {})
    if model not in models:
        # Tenta match por prefixo (e.g. "claude-opus-4-6-20250415" -> "claude-opus-4-6")
        for known_model in models:
            if model.startswith(known_model):
                logger.debug(
                    "prices.yaml: %s/%s matched via prefix %s",
                    provider, model, known_model,
                )
                m = models[known_model]
                return m.get("input_per_1k", 0.001), m.get("output_per_1k", 0.003)

        logger.warning(
            "prices.yaml: model desconhecido '%s' em provider '%s' — usando fallback",
            model, provider,
        )
        fb = data.get("fallback", {})
        return fb.get("input_per_1k", 0.001), fb.get("output_per_1k", 0.003)

    m = models[model]
    return m.get("input_per_1k", 0.001), m.get("output_per_1k", 0.003)


def calculate_cost(
    provider: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> float:
    """Calcula o custo total em USD de uma chamada LLM.

    Formula: (tokens_in / 1000 * input_per_1k) + (tokens_out / 1000 * output_per_1k)

    Args:
        provider: Nome do provider.
        model: Model ID.
        tokens_in: Tokens de entrada.
        tokens_out: Tokens de saida.

    Returns:
        Custo em USD com 6 casas decimais.
    """
    if tokens_in < 0 or tokens_out < 0:
        return 0.0
    pin, pout = get_price(provider, model)
    cost = (tokens_in / 1000.0 * pin) + (tokens_out / 1000.0 * pout)
    return round(cost, 6)


def list_providers() -> list[str]:
    """Lista todos os providers configurados em prices.yaml."""
    data = _cached_data()
    return sorted(data.get("providers", {}).keys())


def list_models(provider: Optional[str] = None) -> list[str]:
    """Lista modelos. Se provider for None, lista todos os modelos
    de todos os providers."""
    data = _cached_data()
    providers = data.get("providers", {})
    if provider:
        return sorted(providers.get(provider, {}).get("models", {}).keys())
    all_models: list[str] = []
    for p_data in providers.values():
        all_models.extend(p_data.get("models", {}).keys())
    return sorted(all_models)


def get_model_info(provider: str, model: str) -> dict:
    """Retorna metadata completa do modelo (preco + tier + notes)."""
    data = _cached_data()
    providers = data.get("providers", {})
    if provider not in providers:
        return {}
    return providers[provider].get("models", {}).get(model, {})
