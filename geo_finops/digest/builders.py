"""Builder do dict central do digest.

Produz uma estrutura unificada que os formatters sabem renderizar.
Usa ``geo_finops.aggregates`` para as queries — consistente com o
snapshot e com o dashboard agregado.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..aggregates import aggregate_by, top_hotspots, totals
from ..config import ALERT_DELTA_PCT
from ..db import get_connection, init_db
from .cloud import fly_weekly_cost, github_actions_minutes_month, vercel_weekly_cost


def week_window(weeks_back: int = 0) -> tuple[datetime, datetime, str]:
    """Janela ISO da semana corrente menos ``weeks_back`` semanas.

    Retorna ``(monday_00:00:00, next_monday_00:00:00, "YYYY-Www")``.
    """
    now = datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    monday = monday - timedelta(weeks=weeks_back)
    sunday = monday + timedelta(days=7)
    iso_year, iso_week, _ = monday.isocalendar()
    label = f"{iso_year}-W{iso_week:02d}"
    return monday, sunday, label


def _delta_pct(curr: float, prev: float) -> str:
    """Delta percentual formatado com sinal. ``n/a`` se base zero."""
    if prev <= 0:
        return "n/a"
    delta = (curr - prev) / prev * 100
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.1f}%"


def _build_alerts(curr: float, prev: float) -> list[str]:
    alerts: list[str] = []
    if prev > 0:
        delta = (curr - prev) / prev * 100
        if delta > ALERT_DELTA_PCT:
            alerts.append(
                f"LLM subiu {delta:.0f}% vs semana passada (limiar {ALERT_DELTA_PCT:.0f}%)"
            )
    return alerts


def build_digest(weeks_back: int = 0) -> dict:
    """Constroi o digest da semana (atual - ``weeks_back``).

    Inclui:
    - Totais LLM da semana + delta vs semana anterior
    - Top 3 hotspots (project x model_id x task_type)
    - Breakdown por provider
    - Custos cloud (Fly.io + Vercel rateio semanal + GH Actions)
    - Alertas (delta acima de ``ALERT_DELTA_PCT``)
    """
    init_db()
    curr_start, curr_end, curr_label = week_window(weeks_back)
    prev_start, prev_end, _ = week_window(weeks_back + 1)

    conn = get_connection()
    try:
        curr_totals = totals(
            start=curr_start.isoformat(),
            end=curr_end.isoformat(),
            conn=conn,
        )
        prev_totals = totals(
            start=prev_start.isoformat(),
            end=prev_end.isoformat(),
            conn=conn,
        )
        by_provider = aggregate_by(
            "provider",
            start=curr_start.isoformat(),
            end=curr_end.isoformat(),
            conn=conn,
        )
        hotspots = top_hotspots(
            start=curr_start.isoformat(),
            end=curr_end.isoformat(),
            limit=3,
            conn=conn,
        )
    finally:
        conn.close()

    curr = {
        "calls": curr_totals["calls"],
        "cost_usd": round(curr_totals["cost_usd"] or 0, 4),
        "tokens_in": curr_totals["tokens_in"],
        "tokens_out": curr_totals["tokens_out"],
    }
    prev = {
        "calls": prev_totals["calls"],
        "cost_usd": round(prev_totals["cost_usd"] or 0, 4),
        "tokens_in": prev_totals["tokens_in"],
        "tokens_out": prev_totals["tokens_out"],
    }

    fly_cost = fly_weekly_cost()
    vercel_cost = vercel_weekly_cost()
    gh_min = github_actions_minutes_month()

    total_curr = curr["cost_usd"] + fly_cost + vercel_cost
    total_prev = prev["cost_usd"] + fly_cost + vercel_cost  # cloud assumido constante

    # Renomeia 'cost_usd' -> 'cost' para manter wire-format do weekly_digest
    # (scripts de downstream ja dependem do campo 'cost').
    by_provider_out = [
        {"provider": r["key"], "calls": r["calls"], "cost": round(r["cost_usd"] or 0, 4)}
        for r in by_provider
    ]
    hotspots_out = [
        {
            "project": h["project"],
            "model_id": h["model_id"],
            "task_type": h["task_type"],
            "calls": h["calls"],
            "cost": h["cost_usd"],
        }
        for h in hotspots
    ]

    return {
        "label": curr_label,
        "window": {
            "start": curr_start.isoformat(),
            "end": curr_end.isoformat(),
        },
        "llm": {
            "current": curr,
            "previous": prev,
            "delta_pct": _delta_pct(curr["cost_usd"], prev["cost_usd"]),
            "by_provider": by_provider_out,
            "hotspots": hotspots_out,
        },
        "cloud": {
            "fly_usd": fly_cost,
            "vercel_usd_estimate": vercel_cost,
            "github_actions_minutes_month": gh_min,
        },
        "total": {
            "current_usd": round(total_curr, 2),
            "previous_usd": round(total_prev, 2),
            "delta_pct": _delta_pct(total_curr, total_prev),
        },
        "alerts": _build_alerts(curr["cost_usd"], prev["cost_usd"]),
        **_carimbo_de_validade(),
    }


# Quanto tempo um digest continua servindo como resposta honesta. O pipeline que
# alimenta este pacote e diario, entao 48 horas cobre um dia perdido sem alarme
# falso e acusa o segundo.
VALIDADE_HORAS = 48


def _carimbo_de_validade() -> dict:
    """Carimba o digest com o momento da geracao E o prazo de validade dele.

    ACRESCENTADO em 2026-08-09. O payload trazia so `generated_at`, que no
    momento da geracao e sempre "agora" e por isso nunca denuncia nada. Quem
    consome ficava com duas opcoes ruins: confiar cegamente, ou inventar um
    limiar proprio de obsolescencia e mante-lo em sincronia com a cadencia do
    produtor, que ele nao conhece.

    O custo disso foi medido: o endpoint publico /api/finops/llm-usage serviu
    `generated_at: 2026-04-19` por 112 dias, com cara de numero atual, enquanto
    a decisao de gasto do ecossistema dependia dele.

    Agora o produtor declara a propria validade. Qualquer consumidor compara o
    relogio dele com `stale_after` e sabe, sem precisar saber a cadencia de
    ninguem. `valid_for_hours` vai junto para o consumidor poder explicar ao
    usuario o porque, em vez de so mostrar um selo.
    """
    agora = datetime.now(timezone.utc)
    return {
        "generated_at": agora.isoformat(),
        "valid_for_hours": VALIDADE_HORAS,
        "stale_after": (agora + timedelta(hours=VALIDADE_HORAS)).isoformat(),
    }


def digest_esta_obsoleto(digest: dict, agora: datetime | None = None) -> bool:
    """Diz se um digest ja passou da validade que ele mesmo declarou.

    Digest antigo, gerado antes deste carimbo existir, nao tem `stale_after`.
    Nesse caso cai no `generated_at` mais a validade padrao; e sem nenhum dos
    dois o digest e tratado como OBSOLETO, porque um payload que nao sabe dizer
    quando nasceu nao pode ser apresentado como medicao corrente.
    """
    agora = agora or datetime.now(timezone.utc)

    limite = digest.get("stale_after")
    if not limite:
        nascimento = digest.get("generated_at")
        if not nascimento:
            return True
        try:
            limite = (
                datetime.fromisoformat(nascimento) + timedelta(hours=VALIDADE_HORAS)
            ).isoformat()
        except (TypeError, ValueError):
            return True

    try:
        prazo = datetime.fromisoformat(limite)
    except (TypeError, ValueError):
        return True

    if prazo.tzinfo is None:
        prazo = prazo.replace(tzinfo=timezone.utc)
    return agora > prazo
