"""Estimativas de custos cloud (Fly.io, Vercel, GitHub Actions).

Valores default sao constantes confirmadas em audits:

- Fly.io caramaschi (shared-cpu-1x 512mb GRU sempre-on + volume 1gb):
  ~US$ 2,50/mes (audit 2026-04-08).
- Vercel landing-page-geo pos-switch para Standard machine + filtro v2:
  ~US$ 6/mes (memoria ``project_finops_vercel.md``).

Ambos podem ser overridden via env var (``GEO_FINOPS_FLY_USD_MONTH``,
``GEO_FINOPS_VERCEL_USD_MONTH``), cujos valores sao expostos em
``geo_finops.config`` para nao duplicar.
"""

from __future__ import annotations

import json
import subprocess

from ..config import FLY_USD_PER_MONTH, VERCEL_USD_PER_MONTH


def fly_weekly_cost() -> float:
    """Rateio semanal do Fly.io (constante / 30 * 7)."""
    return round(FLY_USD_PER_MONTH * 7 / 30, 2)


def vercel_weekly_cost() -> float:
    """Rateio semanal da Vercel (constante / 30 * 7)."""
    return round(VERCEL_USD_PER_MONTH * 7 / 30, 2)


def github_actions_minutes_month() -> int | None:
    """Minutos consumidos no mes atual via ``gh api``. ``None`` se falhar.

    Requer o CLI ``gh`` autenticado. Se o CLI nao existir ou a chamada
    falhar, retorna ``None`` sem propagar excecao — o digest degrada
    graciosamente sem a metrica.
    """
    try:
        out = subprocess.run(
            ["gh", "api", "user/settings/billing/actions"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout)
        return data.get("total_minutes_used")
    except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
