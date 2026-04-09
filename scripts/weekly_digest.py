#!/usr/bin/env python3
"""Weekly FinOps digest — top 3 hotspots da semana + delta vs semana anterior.

Cruza:
  - LLM calls (geo-finops SQLite local OU Supabase)
  - Fly.io estimado (constante mensal -> rateio semanal)
  - GitHub Actions consumo do mes (via gh api)
  - Vercel: TODO (sem API token agora; entra como placeholder)

Saidas:
  - JSON snapshot em ~/.config/geo-finops/digests/YYYY-WW.json
  - stdout markdown
  - opcional: envia mensagem WhatsApp via Meta Graph API (--send-whatsapp)

Uso:
    python -m geo_finops.scripts.weekly_digest                  # so imprime
    python scripts/weekly_digest.py --send-whatsapp             # imprime + envia
    python scripts/weekly_digest.py --weeks-back 0              # semana atual
    python scripts/weekly_digest.py --weeks-back 1              # semana passada

Dependencias minimas: stdlib + httpx (ja em geo-finops). Se geo_finops nao
estiver instalavel, usa caminho direto pra ~/.config/geo-finops/calls.db.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Custo Fly.io estimado para o app caramaschi (shared-cpu-1x 512mb GRU
# always-on + volume 1gb). Confirmado em audit 2026-04-08: ~US$ 2.5/mes.
# Rateio semanal = 2.5 * 7/30 = ~0.58/semana.
FLY_USD_PER_MONTH = 2.50

# Vercel landing-page-geo apos switch para Standard machine + filtro v2
# (memoria project_finops_vercel.md): ~US$ 5-8/mes em pico de trafego.
# Sem API key, usar estimativa media.
VERCEL_USD_PER_MONTH_ESTIMATE = 6.00

# Limite mental: alertar se semana atual > 1.3x media das ultimas 4 semanas.
ALERT_DELTA_PCT = 30.0

# WhatsApp destino (mesmo numero usado pelo caramaschi)
DEFAULT_OWNER = "556298141505"


# ---------------------------------------------------------------------------
# Conexao SQLite
# ---------------------------------------------------------------------------

def _resolve_db_path() -> Path:
    """Resolve o caminho do calls.db sem precisar importar geo_finops."""
    # Tenta o caminho canonico primeiro
    home = Path.home()
    p = home / ".config" / "geo-finops" / "calls.db"
    if p.exists():
        return p
    # Fallback Windows: %USERPROFILE%\.config
    raise FileNotFoundError(
        f"calls.db nao encontrado em {p}. "
        "Rode 'python -m geo_finops.cli status' antes."
    )


def _connect() -> sqlite3.Connection:
    db = _resolve_db_path()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Janela temporal
# ---------------------------------------------------------------------------

def _week_window(weeks_back: int = 0) -> tuple[datetime, datetime, str]:
    """Retorna (start, end, label) da semana corrente menos N semanas.

    Semana ISO: segunda 00:00 ate domingo 23:59:59.
    """
    now = datetime.now(timezone.utc)
    # Vai pra segunda da semana atual
    monday = now - timedelta(days=now.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    # Recua N semanas
    monday = monday - timedelta(weeks=weeks_back)
    sunday = monday + timedelta(days=7)
    iso_year, iso_week, _ = monday.isocalendar()
    label = f"{iso_year}-W{iso_week:02d}"
    return monday, sunday, label


# ---------------------------------------------------------------------------
# Agregacoes LLM
# ---------------------------------------------------------------------------

def _llm_total(conn: sqlite3.Connection, start: datetime, end: datetime) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS calls,
               COALESCE(SUM(cost_usd), 0) AS cost,
               COALESCE(SUM(tokens_in), 0) AS tok_in,
               COALESCE(SUM(tokens_out), 0) AS tok_out
        FROM llm_calls
        WHERE timestamp >= ? AND timestamp < ?
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    return {
        "calls": row["calls"],
        "cost_usd": round(row["cost"], 4),
        "tokens_in": row["tok_in"],
        "tokens_out": row["tok_out"],
    }


def _llm_top_hotspots(
    conn: sqlite3.Connection,
    start: datetime,
    end: datetime,
    limit: int = 3,
) -> list[dict]:
    """Top N hotspots por (project, model_id, task_type)."""
    rows = conn.execute(
        """
        SELECT project,
               model_id,
               COALESCE(task_type, '?') AS task_type,
               COUNT(*) AS calls,
               ROUND(SUM(cost_usd), 4) AS cost
        FROM llm_calls
        WHERE timestamp >= ? AND timestamp < ?
        GROUP BY project, model_id, task_type
        ORDER BY cost DESC
        LIMIT ?
        """,
        (start.isoformat(), end.isoformat(), limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _llm_by_provider(
    conn: sqlite3.Connection, start: datetime, end: datetime
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT provider,
               COUNT(*) AS calls,
               ROUND(SUM(cost_usd), 4) AS cost
        FROM llm_calls
        WHERE timestamp >= ? AND timestamp < ?
        GROUP BY provider
        ORDER BY cost DESC
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Custos cloud (Fly, Vercel, GH Actions)
# ---------------------------------------------------------------------------

def _fly_weekly_cost() -> float:
    return round(FLY_USD_PER_MONTH * 7 / 30, 2)


def _vercel_weekly_cost() -> float:
    return round(VERCEL_USD_PER_MONTH_ESTIMATE * 7 / 30, 2)


def _gh_actions_minutes_used() -> int | None:
    """Retorna minutos consumidos no mes atual via gh api. None se falhar."""
    try:
        out = subprocess.run(
            ["gh", "api", "user/settings/billing/actions"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout)
        return data.get("total_minutes_used")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Formatacao
# ---------------------------------------------------------------------------

def _delta_pct(curr: float, prev: float) -> str:
    if prev <= 0:
        return "n/a"
    delta = (curr - prev) / prev * 100
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.1f}%"


def build_digest(weeks_back: int = 0) -> dict:
    """Constroi o digest completo (semana atual + delta vs semana anterior)."""
    conn = _connect()
    try:
        curr_start, curr_end, curr_label = _week_window(weeks_back)
        prev_start, prev_end, prev_label = _week_window(weeks_back + 1)

        curr = _llm_total(conn, curr_start, curr_end)
        prev = _llm_total(conn, prev_start, prev_end)
        hotspots = _llm_top_hotspots(conn, curr_start, curr_end)
        by_provider = _llm_by_provider(conn, curr_start, curr_end)
    finally:
        conn.close()

    fly_cost = _fly_weekly_cost()
    vercel_cost = _vercel_weekly_cost()
    gh_min = _gh_actions_minutes_used()

    total_curr = curr["cost_usd"] + fly_cost + vercel_cost
    total_prev = prev["cost_usd"] + fly_cost + vercel_cost  # cloud constante

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
            "by_provider": by_provider,
            "hotspots": hotspots,
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
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_alerts(curr: float, prev: float) -> list[str]:
    alerts = []
    if prev > 0:
        delta = (curr - prev) / prev * 100
        if delta > ALERT_DELTA_PCT:
            alerts.append(
                f"LLM subiu {delta:.0f}% vs semana passada (limiar {ALERT_DELTA_PCT:.0f}%)"
            )
    return alerts


def format_markdown(d: dict) -> str:
    lines = [
        f"# FinOps Digest {d['label']}",
        "",
        f"**Janela**: {d['window']['start'][:10]} → {d['window']['end'][:10]}",
        "",
        "## Resumo total",
        f"- LLM:        ${d['llm']['current']['cost_usd']:.2f} ({d['llm']['delta_pct']} vs sem. anterior)",
        f"- Fly.io:     ${d['cloud']['fly_usd']:.2f} (estimado constante)",
        f"- Vercel:     ${d['cloud']['vercel_usd_estimate']:.2f} (estimado)",
        f"- **Total:    ${d['total']['current_usd']:.2f}** ({d['total']['delta_pct']})",
        "",
        "## Top 3 hotspots LLM",
    ]
    for i, h in enumerate(d["llm"]["hotspots"], 1):
        lines.append(
            f"{i}. `{h['project']}` / `{h['model_id']}` / {h['task_type']} — "
            f"{h['calls']} calls / ${h['cost']:.2f}"
        )
    lines += ["", "## Por provider"]
    for p in d["llm"]["by_provider"]:
        lines.append(f"- {p['provider']}: {p['calls']} calls / ${p['cost']:.4f}")
    if d["cloud"]["github_actions_minutes_month"] is not None:
        lines += [
            "",
            f"## GitHub Actions: {d['cloud']['github_actions_minutes_month']} min consumidos no mes",
        ]
    if d["alerts"]:
        lines += ["", "## ALERTAS"]
        for a in d["alerts"]:
            lines.append(f"- {a}")
    return "\n".join(lines)


def format_whatsapp(d: dict) -> str:
    """Versao compacta sem emoji nem markdown pesado."""
    lines = [
        f"FinOps {d['label']}",
        f"Total: ${d['total']['current_usd']:.2f} ({d['total']['delta_pct']})",
        f"LLM ${d['llm']['current']['cost_usd']:.2f} | Fly ${d['cloud']['fly_usd']:.2f} | Vercel ${d['cloud']['vercel_usd_estimate']:.2f}",
        "",
        "Top hotspots:",
    ]
    for i, h in enumerate(d["llm"]["hotspots"], 1):
        proj_short = h["project"][:18]
        model_short = h["model_id"][:22]
        lines.append(f"{i}. {proj_short}/{model_short}")
        lines.append(f"   {h['calls']} calls / ${h['cost']:.2f}")
    if d["alerts"]:
        lines.append("")
        for a in d["alerts"]:
            lines.append("ALERTA: " + a)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# WhatsApp delivery
# ---------------------------------------------------------------------------

def send_whatsapp(text: str, to: str = DEFAULT_OWNER) -> dict:
    """Envia via Meta Graph API. Le credenciais do .env do caramaschi."""
    import httpx

    env_path = Path("C:/Sandyboxclaude/caramaschi/src/scripts/.env")
    creds = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            creds[k.strip()] = v.strip().strip('"').strip("'")

    token = creds.get("WHATSAPP_API_TOKEN") or os.getenv("WHATSAPP_API_TOKEN")
    phone_id = creds.get("WHATSAPP_PHONE_ID") or os.getenv("WHATSAPP_PHONE_ID")
    if not token or not phone_id:
        return {"ok": False, "error": "Credenciais WhatsApp ausentes"}

    url = f"https://graph.facebook.com/v22.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=15)
        return {"ok": resp.status_code == 200, "status": resp.status_code, "body": resp.text[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Snapshot persistence
# ---------------------------------------------------------------------------

def save_snapshot(d: dict) -> Path:
    out_dir = Path.home() / ".config" / "geo-finops" / "digests"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{d['label']}.json"
    path.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--weeks-back", type=int, default=0,
                   help="0 = semana atual, 1 = semana passada, etc.")
    p.add_argument("--send-whatsapp", action="store_true",
                   help="Envia digest via WhatsApp Meta API")
    p.add_argument("--to", default=DEFAULT_OWNER,
                   help="Numero destino WhatsApp (formato API, sem +)")
    p.add_argument("--format", choices=["markdown", "whatsapp", "json"],
                   default="markdown")
    args = p.parse_args()

    digest = build_digest(weeks_back=args.weeks_back)
    snapshot_path = save_snapshot(digest)

    if args.format == "json":
        print(json.dumps(digest, indent=2, ensure_ascii=False))
    elif args.format == "whatsapp":
        print(format_whatsapp(digest))
    else:
        print(format_markdown(digest))

    print(f"\n[snapshot] {snapshot_path}", file=sys.stderr)

    if args.send_whatsapp:
        result = send_whatsapp(format_whatsapp(digest), to=args.to)
        print(f"[whatsapp] {json.dumps(result)}", file=sys.stderr)
        if not result.get("ok"):
            sys.exit(1)


if __name__ == "__main__":
    main()
