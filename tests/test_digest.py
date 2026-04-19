"""Testes do pacote ``geo_finops.digest`` (split do weekly_digest).

Cobre builders, formatters, cloud estimators e reporters (com mock
para WhatsApp delivery). Antes da refatoracao 2026-04-19, ``weekly_digest``
era um script monolitico sem testes.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from geo_finops.digest import (  # noqa: E402
    build_digest,
    cloud,
    format_json,
    format_markdown,
    format_whatsapp,
    save_snapshot,
    send_whatsapp,
    week_window,
)
from geo_finops.tracker import track_call  # noqa: E402

# ---------------------------------------------------------------------------
# week_window
# ---------------------------------------------------------------------------


def test_week_window_current_starts_on_monday():
    start, end, label = week_window(0)
    assert start.weekday() == 0  # Monday
    assert end.weekday() == 0
    assert (end - start) == timedelta(days=7)
    assert label.startswith(str(start.year))
    assert "-W" in label


def test_week_window_previous_is_7d_earlier():
    curr_start, _, _ = week_window(0)
    prev_start, _, _ = week_window(1)
    assert (curr_start - prev_start) == timedelta(days=7)


# ---------------------------------------------------------------------------
# cloud estimators
# ---------------------------------------------------------------------------


def test_fly_weekly_cost_is_rateio():
    from geo_finops.config import FLY_USD_PER_MONTH

    expected = round(FLY_USD_PER_MONTH * 7 / 30, 2)
    assert cloud.fly_weekly_cost() == expected


def test_vercel_weekly_cost_is_rateio():
    from geo_finops.config import VERCEL_USD_PER_MONTH

    expected = round(VERCEL_USD_PER_MONTH * 7 / 30, 2)
    assert cloud.vercel_weekly_cost() == expected


def test_github_actions_minutes_returns_none_when_gh_missing(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr("subprocess.run", fake_run)
    assert cloud.github_actions_minutes_month() is None


# ---------------------------------------------------------------------------
# build_digest
# ---------------------------------------------------------------------------


def _seed_week(isolated_db):
    """Popula a semana corrente e a anterior com 2 calls cada."""
    curr_start, _, _ = week_window(0)
    prev_start, _, _ = week_window(1)

    # Semana atual
    track_call(
        project="curr-proj",
        model_id="claude-opus-4-6",
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.50,
        run_id="c1",
        task_type="research",
        timestamp=(curr_start + timedelta(hours=2)).isoformat(),
    )
    track_call(
        project="curr-proj",
        model_id="gpt-4o",
        tokens_in=200,
        tokens_out=100,
        cost_usd=0.30,
        run_id="c2",
        task_type="code",
        timestamp=(curr_start + timedelta(hours=5)).isoformat(),
    )

    # Semana anterior
    track_call(
        project="prev-proj",
        model_id="claude-opus-4-6",
        tokens_in=50,
        tokens_out=25,
        cost_usd=0.20,
        run_id="p1",
        task_type="research",
        timestamp=(prev_start + timedelta(hours=3)).isoformat(),
    )


def test_build_digest_structure(isolated_db, monkeypatch):
    # Evita chamar gh no teste
    monkeypatch.setattr(cloud, "github_actions_minutes_month", lambda: None)
    _seed_week(isolated_db)
    d = build_digest(weeks_back=0)

    assert "label" in d
    assert "llm" in d
    assert "cloud" in d
    assert "total" in d
    assert d["llm"]["current"]["calls"] == 2
    assert abs(d["llm"]["current"]["cost_usd"] - 0.80) < 1e-6
    assert d["llm"]["previous"]["calls"] == 1


def test_build_digest_delta_pct_format(isolated_db, monkeypatch):
    monkeypatch.setattr(cloud, "github_actions_minutes_month", lambda: None)
    _seed_week(isolated_db)
    d = build_digest(weeks_back=0)
    # current 0.80 vs previous 0.20 => +300%
    assert d["llm"]["delta_pct"].startswith("+")


def test_build_digest_triggers_alert_when_big_jump(isolated_db, monkeypatch):
    monkeypatch.setattr(cloud, "github_actions_minutes_month", lambda: None)
    _seed_week(isolated_db)
    d = build_digest(weeks_back=0)
    # 300% > 30% default → alerta
    assert len(d["alerts"]) >= 1
    assert "LLM subiu" in d["alerts"][0]


def test_build_digest_empty_window_returns_zero(isolated_db, monkeypatch):
    monkeypatch.setattr(cloud, "github_actions_minutes_month", lambda: None)
    d = build_digest(weeks_back=0)
    assert d["llm"]["current"]["calls"] == 0
    assert d["llm"]["current"]["cost_usd"] == 0


# ---------------------------------------------------------------------------
# formatters
# ---------------------------------------------------------------------------


def _sample_digest() -> dict:
    return {
        "label": "2026-W16",
        "window": {"start": "2026-04-13T00:00:00+00:00", "end": "2026-04-20T00:00:00+00:00"},
        "llm": {
            "current": {"calls": 10, "cost_usd": 0.50, "tokens_in": 1000, "tokens_out": 500},
            "previous": {"calls": 5, "cost_usd": 0.25, "tokens_in": 500, "tokens_out": 250},
            "delta_pct": "+100.0%",
            "by_provider": [{"provider": "anthropic", "calls": 10, "cost": 0.50}],
            "hotspots": [
                {
                    "project": "papers",
                    "model_id": "claude-opus-4-6",
                    "task_type": "research",
                    "calls": 10,
                    "cost": 0.50,
                },
            ],
        },
        "cloud": {"fly_usd": 0.58, "vercel_usd_estimate": 1.40, "github_actions_minutes_month": 42},
        "total": {"current_usd": 2.48, "previous_usd": 2.23, "delta_pct": "+11.2%"},
        "alerts": ["LLM subiu 100% vs semana passada (limiar 30%)"],
        "generated_at": "2026-04-19T12:00:00+00:00",
    }


def test_format_markdown_includes_sections():
    md = format_markdown(_sample_digest())
    assert "FinOps Digest 2026-W16" in md
    assert "Resumo total" in md
    assert "Top 3 hotspots" in md
    assert "ALERTAS" in md
    assert "papers" in md


def test_format_whatsapp_is_compact():
    text = format_whatsapp(_sample_digest())
    # Menos de 1500 chars (cabe em notificacao)
    assert len(text) < 1500
    assert "FinOps 2026-W16" in text
    assert "Top hotspots" in text


def test_format_whatsapp_no_emojis():
    import re

    text = format_whatsapp(_sample_digest())
    emoji_re = re.compile(r"[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001F600-\U0001F64F]")
    assert not emoji_re.findall(text)


def test_format_json_valid():
    import json

    s = format_json(_sample_digest())
    parsed = json.loads(s)
    assert parsed["label"] == "2026-W16"


# ---------------------------------------------------------------------------
# reporters
# ---------------------------------------------------------------------------


def test_save_snapshot_writes_under_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GEO_FINOPS_CONFIG_DIR", str(tmp_path))
    d = _sample_digest()
    path = save_snapshot(d)
    assert path.exists()
    assert path.name == "2026-W16.json"
    import json

    assert json.loads(path.read_text(encoding="utf-8"))["label"] == "2026-W16"


def test_send_whatsapp_without_creds_returns_error(monkeypatch):
    """Quando load_whatsapp_creds retorna (None, None), o send fail-fast."""
    # Mocka o loader direto para evitar pegar creds do .env do caramaschi
    # (que existe no workspace canonico em desenvolvimento local).
    from geo_finops.digest import reporters

    monkeypatch.setattr(reporters, "load_whatsapp_creds", lambda: (None, None))
    result = send_whatsapp("test")
    assert result["ok"] is False
    assert "Credenciais" in result.get("error", "")
