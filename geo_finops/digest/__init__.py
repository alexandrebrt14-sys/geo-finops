"""Weekly FinOps digest — top 3 hotspots + delta vs semana anterior.

Pacote split a partir do script monolitico ``scripts/weekly_digest.py``
(refatoracao 2026-04-19). A motivacao era que o script tinha 400+ LOC
misturando quatro responsabilidades:

- Querying de dados LLM (agregacoes)
- Estimativa de custos cloud (Fly.io, Vercel, GH Actions)
- Formatacao (markdown, whatsapp, JSON)
- Delivery (WhatsApp via Meta Graph API, persistencia em disco)

Agora cada responsabilidade tem seu modulo testavel em isolamento:

- ``geo_finops.digest.builders``    — constroi o dict do digest
- ``geo_finops.digest.cloud``       — estima Fly/Vercel/GH Actions
- ``geo_finops.digest.formatters``  — markdown / whatsapp / json
- ``geo_finops.digest.reporters``   — save snapshot, envia WhatsApp

O entry point ``scripts/weekly_digest.py`` virou um thin CLI (40 LOC)
que compoe as quatro peças.

API rapida:

    from geo_finops.digest import build_digest, format_markdown

    digest = build_digest(weeks_back=0)
    print(format_markdown(digest))
"""

from .builders import build_digest, week_window
from .cloud import (
    fly_weekly_cost,
    github_actions_minutes_month,
    vercel_weekly_cost,
)
from .formatters import format_json, format_markdown, format_whatsapp
from .reporters import save_snapshot, send_whatsapp

__all__ = [
    "build_digest",
    "fly_weekly_cost",
    "format_json",
    "format_markdown",
    "format_whatsapp",
    "github_actions_minutes_month",
    "save_snapshot",
    "send_whatsapp",
    "vercel_weekly_cost",
    "week_window",
]
