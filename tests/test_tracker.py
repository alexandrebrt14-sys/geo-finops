"""Testes de ``geo_finops.tracker`` (track_call + helpers privados).

Antes da refatoracao 2026-04-19, este modulo tinha ZERO testes (223 LOC).
Agora cobrimos:

- ``_infer_provider`` para todos os substrings suportados
- ``_normalize_timestamp`` nos paths principais (None, Z, naive, invalido)
- ``track_call`` dedup via UNIQUE constraint
- ``track_call`` com/sem metadata
- ``query_calls`` filtros combinados
- ``run_id_for_session`` formato e entropia
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from geo_finops.tracker import (  # noqa: E402
    _infer_provider,
    _normalize_timestamp,
    query_calls,
    run_id_for_session,
    track_call,
)

# ---------------------------------------------------------------------------
# _infer_provider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_id,expected",
    [
        ("claude-opus-4-6", "anthropic"),
        ("claude-3-5-sonnet-latest", "anthropic"),
        ("anthropic/claude-opus-4-6", "anthropic"),
        ("gpt-4o", "openai"),
        ("gpt-4o-mini", "openai"),
        ("o1-preview", "openai"),
        ("o3-mini", "openai"),
        ("o4-mini", "openai"),
        ("openai/gpt-4o", "openai"),
        ("gemini-2.5-pro", "google"),
        ("gemini-1.5-flash", "google"),
        ("gemma-2b", "google"),
        ("sonar-pro", "perplexity"),
        ("perplexity/sonar", "perplexity"),
        ("llama-3.3-70b", "groq"),
        ("kimi-k2", "groq"),
        ("qwen-2.5", "groq"),
        ("mixtral-8x7b", "groq"),
        ("unknown-model", "unknown"),
        ("", "unknown"),
    ],
)
def test_infer_provider(model_id, expected):
    assert _infer_provider(model_id) == expected


def test_infer_provider_handles_none():
    assert _infer_provider(None) == "unknown"


# ---------------------------------------------------------------------------
# _normalize_timestamp
# ---------------------------------------------------------------------------


def test_normalize_timestamp_none_returns_now():
    result = _normalize_timestamp(None)
    parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    assert abs((parsed - now).total_seconds()) < 5


def test_normalize_timestamp_accepts_z_suffix():
    result = _normalize_timestamp("2026-04-19T10:00:00Z")
    assert result.endswith("+00:00")
    assert "2026-04-19T10:00:00" in result


def test_normalize_timestamp_keeps_utc():
    result = _normalize_timestamp("2026-04-19T10:00:00+00:00")
    assert result == "2026-04-19T10:00:00+00:00"


def test_normalize_timestamp_naive_assumed_utc():
    result = _normalize_timestamp("2026-04-19T10:00:00")
    assert "+00:00" in result


def test_normalize_timestamp_converts_timezone():
    # -03:00 BRT -> +00:00 UTC
    result = _normalize_timestamp("2026-04-19T10:00:00-03:00")
    assert "2026-04-19T13:00:00" in result


def test_normalize_timestamp_invalid_fallbacks_to_now():
    result = _normalize_timestamp("nao-eh-iso")
    parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    assert abs((parsed - now).total_seconds()) < 5


# ---------------------------------------------------------------------------
# track_call + dedup
# ---------------------------------------------------------------------------


def test_track_call_inserts(isolated_db):
    rid = track_call(
        project="test",
        model_id="claude-opus-4-6",
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.01,
        run_id="run-A",
        task_type="research",
    )
    assert rid is not None
    assert rid > 0


def test_track_call_dedup_returns_none(isolated_db):
    kwargs = {
        "project": "test",
        "model_id": "gpt-4o",
        "tokens_in": 1,
        "tokens_out": 1,
        "cost_usd": 0.001,
        "run_id": "r1",
        "task_type": "t",
        "timestamp": "2026-04-19T12:00:00+00:00",
    }
    first = track_call(**kwargs)
    second = track_call(**kwargs)
    assert first is not None
    assert second is None


def test_track_call_infers_provider(isolated_db):
    track_call(
        project="test",
        model_id="gpt-4o-mini",
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.0001,
        run_id="infer-1",
    )
    rows = query_calls(project="test")
    assert rows
    assert rows[0]["provider"] == "openai"


def test_track_call_accepts_explicit_provider(isolated_db):
    track_call(
        project="test",
        model_id="custom-model",
        tokens_in=1,
        tokens_out=1,
        cost_usd=0.0,
        run_id="custom-1",
        provider="anthropic",
    )
    rows = query_calls(project="test")
    assert rows[0]["provider"] == "anthropic"


def test_track_call_serializes_metadata(isolated_db):
    track_call(
        project="test",
        model_id="claude-opus-4-6",
        tokens_in=10,
        tokens_out=10,
        cost_usd=0.01,
        run_id="meta-1",
        metadata={"feature": "x", "n": 42},
    )
    rows = query_calls(project="test")
    import json

    meta = json.loads(rows[0]["metadata"])
    assert meta["feature"] == "x"
    assert meta["n"] == 42


def test_track_call_none_values_become_zero(isolated_db):
    track_call(
        project="test",
        model_id="claude-opus-4-6",
        tokens_in=None,  # type: ignore[arg-type]
        tokens_out=None,  # type: ignore[arg-type]
        cost_usd=None,  # type: ignore[arg-type]
        run_id="none-1",
    )
    rows = query_calls(project="test")
    assert rows[0]["tokens_in"] == 0
    assert rows[0]["tokens_out"] == 0
    assert rows[0]["cost_usd"] == 0


# ---------------------------------------------------------------------------
# query_calls filtros
# ---------------------------------------------------------------------------


def test_query_calls_filter_by_project(isolated_db):
    for i, proj in enumerate(["a", "b", "a"]):
        track_call(
            project=proj,
            model_id="claude-opus-4-6",
            tokens_in=i,
            tokens_out=i,
            cost_usd=0.001,
            run_id=f"{proj}-{i}",
        )
    assert len(query_calls(project="a")) == 2
    assert len(query_calls(project="b")) == 1


def test_query_calls_filter_by_provider(isolated_db):
    track_call(
        project="x",
        model_id="claude-opus-4-6",
        tokens_in=1,
        tokens_out=1,
        cost_usd=0.001,
        run_id="1",
    )
    track_call(
        project="x", model_id="gpt-4o", tokens_in=1, tokens_out=1, cost_usd=0.001, run_id="2"
    )
    anthropic = query_calls(provider="anthropic")
    openai = query_calls(provider="openai")
    assert len(anthropic) == 1
    assert len(openai) == 1


def test_query_calls_limit(isolated_db):
    for i in range(5):
        track_call(
            project="p",
            model_id="m",
            provider="anthropic",
            tokens_in=i,
            tokens_out=i,
            cost_usd=0.001,
            run_id=f"r{i}",
        )
    assert len(query_calls(limit=3)) == 3


# ---------------------------------------------------------------------------
# run_id_for_session
# ---------------------------------------------------------------------------


def test_run_id_for_session_format():
    rid = run_id_for_session()
    # YYYYMMDDTHHMMSS_<6hex> = 15 chars + 1 + 6 = 22
    assert len(rid) == 22
    assert rid[8] == "T"
    assert rid[15] == "_"


def test_run_id_for_session_unique():
    ids = {run_id_for_session() for _ in range(50)}
    assert len(ids) == 50  # colisao extremamente improvavel


# ---------------------------------------------------------------------------
# aggregate_by (compat layer)
# ---------------------------------------------------------------------------


def test_aggregate_by_rejects_invalid_field():
    from geo_finops.tracker import aggregate_by

    with pytest.raises(ValueError):
        aggregate_by("cost_usd")


def test_aggregate_by_groups_correctly(isolated_db):
    from geo_finops.tracker import aggregate_by

    track_call(
        project="a",
        model_id="claude-opus-4-6",
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.10,
        run_id="1",
    )
    track_call(
        project="b",
        model_id="claude-opus-4-6",
        tokens_in=200,
        tokens_out=100,
        cost_usd=0.20,
        run_id="2",
    )
    track_call(
        project="a", model_id="gpt-4o", tokens_in=50, tokens_out=25, cost_usd=0.05, run_id="3"
    )

    result = aggregate_by("project")
    by_key = {r["key"]: r for r in result}
    assert by_key["a"]["calls"] == 2
    assert by_key["b"]["calls"] == 1
    assert abs(by_key["a"]["cost_usd"] - 0.15) < 1e-6
