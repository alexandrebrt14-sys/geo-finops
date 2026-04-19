"""Testes do modulo ``geo_finops.aggregates`` (nova camada SQL reusavel).

Cobre:

- ``totals`` com e sem janela temporal
- ``aggregate_by`` com todas as dimensoes validas e ValueError em invalida
- ``top_models`` com limite
- ``daily_timeseries`` retornando serie ordenada
- ``top_hotspots`` ordenacao por custo desc
- ``sync_status_counts`` cobrindo os 3 status
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from geo_finops import aggregates  # noqa: E402
from geo_finops.tracker import track_call  # noqa: E402


def _seed(isolated_db):
    """Popula o banco isolado com um mix representativo."""
    rows = [
        {
            "project": "a",
            "model_id": "claude-opus-4-6",
            "provider": "anthropic",
            "tokens_in": 100,
            "tokens_out": 50,
            "cost_usd": 0.10,
            "run_id": "1",
            "task_type": "research",
            "timestamp": "2026-04-01T10:00:00+00:00",
        },
        {
            "project": "a",
            "model_id": "claude-opus-4-6",
            "provider": "anthropic",
            "tokens_in": 200,
            "tokens_out": 100,
            "cost_usd": 0.20,
            "run_id": "2",
            "task_type": "research",
            "timestamp": "2026-04-02T10:00:00+00:00",
        },
        {
            "project": "b",
            "model_id": "gpt-4o",
            "provider": "openai",
            "tokens_in": 300,
            "tokens_out": 150,
            "cost_usd": 0.30,
            "run_id": "3",
            "task_type": "code",
            "timestamp": "2026-04-03T10:00:00+00:00",
        },
        {
            "project": "b",
            "model_id": "gpt-4o-mini",
            "provider": "openai",
            "tokens_in": 50,
            "tokens_out": 25,
            "cost_usd": 0.05,
            "run_id": "4",
            "task_type": "code",
            "timestamp": "2026-04-04T10:00:00+00:00",
        },
    ]
    for r in rows:
        track_call(**r)


# ---------------------------------------------------------------------------
# totals
# ---------------------------------------------------------------------------


def test_totals_empty(isolated_db):
    t = aggregates.totals()
    assert t["calls"] == 0
    assert t["cost_usd"] == 0


def test_totals_aggregates_everything(isolated_db):
    _seed(isolated_db)
    t = aggregates.totals()
    assert t["calls"] == 4
    assert abs(t["cost_usd"] - 0.65) < 1e-6
    assert t["tokens_in"] == 650
    assert t["tokens_out"] == 325
    assert t["period_start"] == "2026-04-01T10:00:00+00:00"
    assert t["period_end"] == "2026-04-04T10:00:00+00:00"


def test_totals_respects_window(isolated_db):
    _seed(isolated_db)
    t = aggregates.totals(
        start="2026-04-02T00:00:00+00:00",
        end="2026-04-03T23:59:59+00:00",
    )
    assert t["calls"] == 2
    assert abs(t["cost_usd"] - 0.50) < 1e-6


# ---------------------------------------------------------------------------
# aggregate_by
# ---------------------------------------------------------------------------


def test_aggregate_by_invalid_field_raises(isolated_db):
    with pytest.raises(ValueError):
        aggregates.aggregate_by("cost_usd")


def test_aggregate_by_provider(isolated_db):
    _seed(isolated_db)
    rows = aggregates.aggregate_by("provider")
    by_key = {r["key"]: r for r in rows}
    assert by_key["anthropic"]["calls"] == 2
    assert by_key["openai"]["calls"] == 2
    assert abs(by_key["openai"]["cost_usd"] - 0.35) < 1e-6
    assert abs(by_key["anthropic"]["cost_usd"] - 0.30) < 1e-6
    # Ordenado por cost_usd DESC: openai (0.35) > anthropic (0.30)
    assert rows[0]["key"] == "openai"


def test_aggregate_by_project_with_limit(isolated_db):
    _seed(isolated_db)
    rows = aggregates.aggregate_by("project", limit=1)
    assert len(rows) == 1
    # 'b' tem maior custo (0.35)
    assert rows[0]["key"] == "b"


def test_aggregate_by_model_id(isolated_db):
    _seed(isolated_db)
    rows = aggregates.aggregate_by("model_id")
    keys = [r["key"] for r in rows]
    assert set(keys) == {"claude-opus-4-6", "gpt-4o", "gpt-4o-mini"}


def test_aggregate_by_task_type(isolated_db):
    _seed(isolated_db)
    rows = aggregates.aggregate_by("task_type")
    keys = [r["key"] for r in rows]
    assert set(keys) == {"research", "code"}


# ---------------------------------------------------------------------------
# top_models
# ---------------------------------------------------------------------------


def test_top_models_includes_provider_field(isolated_db):
    _seed(isolated_db)
    rows = aggregates.top_models(limit=5)
    assert all("provider" in r for r in rows)
    assert all("key" in r for r in rows)
    # claude-opus-4-6 (2 rows = 0.30) empata com gpt-4o (1 row = 0.30).
    # A ordem entre empates nao eh deterministica no SQLite, so afirmamos
    # que ambos estao nos 2 primeiros e gpt-4o-mini eh o ultimo.
    top2_keys = {rows[0]["key"], rows[1]["key"]}
    assert top2_keys == {"claude-opus-4-6", "gpt-4o"}
    assert rows[-1]["key"] == "gpt-4o-mini"


def test_top_models_limit(isolated_db):
    _seed(isolated_db)
    rows = aggregates.top_models(limit=2)
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# daily_timeseries
# ---------------------------------------------------------------------------


def test_daily_timeseries_groups_by_date(isolated_db):
    _seed(isolated_db)
    rows = aggregates.daily_timeseries(days=30)
    dates = [r["date"] for r in rows]
    assert "2026-04-01" in dates
    assert "2026-04-02" in dates
    # Ordenado crescente
    assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# top_hotspots
# ---------------------------------------------------------------------------


def test_top_hotspots_ordered_by_cost(isolated_db):
    _seed(isolated_db)
    rows = aggregates.top_hotspots(limit=10)
    # top_hotspots agrupa por (project, model_id, task_type). O seed produz
    # 3 combinacoes distintas: a/claude/research (2 rows = 0.30),
    # b/gpt-4o/code (1 row = 0.30), b/gpt-4o-mini/code (1 row = 0.05).
    assert len(rows) == 3
    costs = [r["cost_usd"] for r in rows]
    assert costs == sorted(costs, reverse=True)


def test_top_hotspots_limit(isolated_db):
    _seed(isolated_db)
    assert len(aggregates.top_hotspots(limit=1)) == 1


# ---------------------------------------------------------------------------
# sync_status_counts
# ---------------------------------------------------------------------------


def test_sync_status_counts_empty_returns_zeros(isolated_db):
    counts = aggregates.sync_status_counts()
    assert counts == {"pending": 0, "synced": 0, "error": 0}


def test_sync_status_counts_pending_after_insert(isolated_db):
    _seed(isolated_db)
    counts = aggregates.sync_status_counts()
    assert counts["pending"] == 4
    assert counts["synced"] == 0
    assert counts["error"] == 0
