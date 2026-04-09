"""Tests para scripts/aggregate_dashboard.py — B-022."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import aggregate_dashboard as agg  # noqa: E402


# ─── Aggregations ─────────────────────────────────────────────────────────


def test_aggregate_by_project_empty():
    assert agg._aggregate_by_project([]) == {}


def test_aggregate_by_project_basic():
    calls = [
        {"project": "papers", "cost_usd": 0.05, "tokens_in": 100, "tokens_out": 200, "model_id": "gpt-4o"},
        {"project": "papers", "cost_usd": 0.03, "tokens_in": 50, "tokens_out": 100, "model_id": "gpt-4o"},
        {"project": "curso", "cost_usd": 0.02, "tokens_in": 30, "tokens_out": 60, "model_id": "claude-haiku-4-5"},
    ]
    result = agg._aggregate_by_project(calls)
    assert "papers" in result
    assert "curso" in result
    assert result["papers"]["calls"] == 2
    assert result["papers"]["cost"] == pytest.approx(0.08)
    assert result["papers"]["tokens_in"] == 150
    assert result["papers"]["tokens_out"] == 300
    assert result["papers"]["models"]["gpt-4o"] == 2
    assert result["curso"]["calls"] == 1


def test_aggregate_by_provider_basic():
    calls = [
        {"provider": "anthropic", "cost_usd": 0.10},
        {"provider": "openai", "cost_usd": 0.05},
        {"provider": "anthropic", "cost_usd": 0.07},
    ]
    result = agg._aggregate_by_provider(calls)
    assert result["anthropic"]["calls"] == 2
    assert result["anthropic"]["cost"] == pytest.approx(0.17)
    assert result["openai"]["calls"] == 1


def test_aggregate_handles_none_fields():
    """None em cost/tokens nao deve crashar."""
    calls = [
        {"project": "x", "cost_usd": None, "tokens_in": None, "tokens_out": None, "model_id": None},
    ]
    result = agg._aggregate_by_project(calls)
    assert result["x"]["calls"] == 1
    assert result["x"]["cost"] == 0.0


def test_daily_timeseries_groups_by_date():
    calls = [
        {"timestamp": "2026-04-01T10:00:00Z", "cost_usd": 0.05},
        {"timestamp": "2026-04-01T15:30:00Z", "cost_usd": 0.03},
        {"timestamp": "2026-04-02T09:00:00Z", "cost_usd": 0.07},
    ]
    result = agg._daily_timeseries(calls)
    assert "2026-04-01" in result
    assert "2026-04-02" in result
    assert result["2026-04-01"]["calls"] == 2
    assert result["2026-04-01"]["cost"] == pytest.approx(0.08)
    assert result["2026-04-02"]["calls"] == 1


def test_daily_timeseries_sorted():
    calls = [
        {"timestamp": "2026-04-03", "cost_usd": 0.01},
        {"timestamp": "2026-04-01", "cost_usd": 0.02},
        {"timestamp": "2026-04-02", "cost_usd": 0.03},
    ]
    result = agg._daily_timeseries(calls)
    keys = list(result.keys())
    assert keys == ["2026-04-01", "2026-04-02", "2026-04-03"]


# ─── HTML render ──────────────────────────────────────────────────────────


def test_render_dashboard_returns_html(monkeypatch):
    """render_dashboard retorna HTML valido sem precisar de DB real."""
    monkeypatch.setattr(agg, "_load_geo_finops_calls", lambda since_days: [
        {"project": "papers", "provider": "openai", "cost_usd": 0.05,
         "tokens_in": 100, "tokens_out": 200, "model_id": "gpt-4o",
         "timestamp": "2026-04-09T10:00:00Z"},
    ])
    monkeypatch.setattr(agg, "_try_caramaschi_finops", lambda: None)
    monkeypatch.setattr(agg, "_load_orchestrator_kpis", lambda since_days: [])

    html = agg.render_dashboard(since_days=7)
    assert "<!DOCTYPE html>" in html
    assert "Dashboard agregado" in html or "Brasil GEO" in html
    assert "papers" in html
    assert "$0.05" in html or "0.05" in html


def test_render_dashboard_empty_data(monkeypatch):
    """Sem dados, dashboard ainda renderiza com placeholder."""
    monkeypatch.setattr(agg, "_load_geo_finops_calls", lambda since_days: [])
    monkeypatch.setattr(agg, "_try_caramaschi_finops", lambda: None)
    monkeypatch.setattr(agg, "_load_orchestrator_kpis", lambda since_days: [])

    html = agg.render_dashboard(since_days=30)
    assert "<!DOCTYPE html>" in html
    assert "Nenhum dado encontrado" in html


def test_render_dashboard_with_caramaschi(monkeypatch):
    """Quando caramaschi /finops responde, snapshot vai pro HTML."""
    monkeypatch.setattr(agg, "_load_geo_finops_calls", lambda since_days: [])
    monkeypatch.setattr(agg, "_try_caramaschi_finops", lambda: {
        "spent_today": 0.5,
        "calls_today": 12,
    })
    monkeypatch.setattr(agg, "_load_orchestrator_kpis", lambda since_days: [])

    html = agg.render_dashboard(since_days=7)
    assert "caramaschi" in html
    assert "spent_today" in html


def test_render_dashboard_includes_chart_js(monkeypatch):
    """Sentinela: Chart.js eh carregado via CDN."""
    monkeypatch.setattr(agg, "_load_geo_finops_calls", lambda since_days: [])
    monkeypatch.setattr(agg, "_try_caramaschi_finops", lambda: None)
    monkeypatch.setattr(agg, "_load_orchestrator_kpis", lambda since_days: [])
    html = agg.render_dashboard(since_days=7)
    assert "chart.js" in html.lower() or "Chart" in html


def test_render_dashboard_no_emojis(monkeypatch):
    """Sentinela contra regra global: zero emojis."""
    import re
    monkeypatch.setattr(agg, "_load_geo_finops_calls", lambda since_days: [])
    monkeypatch.setattr(agg, "_try_caramaschi_finops", lambda: None)
    monkeypatch.setattr(agg, "_load_orchestrator_kpis", lambda since_days: [])
    html = agg.render_dashboard(since_days=7)
    emoji_pattern = re.compile(
        r"[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001F600-\U0001F64F]"
    )
    emojis_found = emoji_pattern.findall(html)
    assert len(emojis_found) == 0, f"Emojis encontrados: {emojis_found}"


def test_caramaschi_finops_fail_graceful(monkeypatch):
    """_try_caramaschi_finops retorna None se nao conseguir conectar."""
    # Sem mock — vai tentar conectar real e falhar (sem internet ou
    # endpoint indisponivel). Deve retornar None sem crashar.
    result = agg._try_caramaschi_finops()
    # Aceita None ou dict — apenas garante que nao crasha
    assert result is None or isinstance(result, dict)
