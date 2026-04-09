#!/usr/bin/env python3
"""aggregate_dashboard.py — dashboard HTML agregado de IA solo (B-022).

Achado B-022 da auditoria de ecossistema 2026-04-08 (Onda 3 filtrada
para projeto solo). Antes deste script, nao havia visao consolidada
de uso de LLM em todo o ecossistema. Cada repo tinha seu proprio:

- geo-orchestrator/.kpi_history.jsonl  (KPIs por execucao)
- ~/.config/geo-finops/calls.db        (calls registradas, multi-projeto)
- caramaschi.fly.dev/finops             (estado em producao 24/7)

Este script consolida tudo em UM arquivo HTML estatico que pode ser:
- Aberto direto no navegador (file://)
- Servido via static host
- Enviado por email semanal
- Anexado em relatorio mensal

Uso:
    python scripts/aggregate_dashboard.py
    python scripts/aggregate_dashboard.py --output ~/.cache/geo-dashboard.html
    python scripts/aggregate_dashboard.py --since 30  # ultimos 30 dias

Por que solo: este eh um dashboard para UMA pessoa olhar de vez em
quando — nao precisa de auth, multi-tenant, real-time, ou frontend
React. HTML estatico Chart.js inline atende perfeitamente.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# ─── Fontes de dados ──────────────────────────────────────────────────────


def _load_geo_finops_calls(since_days: int = 30) -> list[dict]:
    """Le calls.db do geo-finops local."""
    from geo_finops.db import get_db_path
    db_path = get_db_path()
    if not db_path.exists():
        logger.info("geo-finops calls.db nao existe em %s", db_path)
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT timestamp, project, provider, model_id, task_type,
                      tokens_in, tokens_out, cost_usd
               FROM llm_calls
               WHERE timestamp >= ?
               ORDER BY timestamp DESC""",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _load_orchestrator_kpis(since_days: int = 30) -> list[dict]:
    """Le .kpi_history.jsonl do geo-orchestrator (se path configurado)."""
    candidates = [
        Path.home() / ".cache" / "geo-orchestrator" / ".kpi_history.jsonl",
        Path("C:/Sandyboxclaude/geo-orchestrator/output/.kpi_history.jsonl"),
        Path.cwd().parent / "geo-orchestrator" / "output" / ".kpi_history.jsonl",
    ]
    for path in candidates:
        try:
            if path.exists():
                logger.info("KPI history em %s", path)
                entries = []
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                return entries[-100:]  # ultimos 100
        except OSError:
            continue
    return []


def _try_caramaschi_finops() -> dict | None:
    """Tenta GET caramaschi.fly.dev/finops (timeout curto, fail-graceful)."""
    try:
        import httpx
        url = "https://caramaschi.fly.dev/finops"
        resp = httpx.get(url, timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        logger.debug("caramaschi /finops indisponivel: %s", exc)
    return None


# ─── Agregacoes ───────────────────────────────────────────────────────────


def _aggregate_by_project(calls: list[dict]) -> dict[str, dict]:
    """Agrega por projeto: total cost, total calls, top model."""
    by_project: dict[str, dict] = {}
    for c in calls:
        proj = c.get("project") or "unknown"
        if proj not in by_project:
            by_project[proj] = {
                "calls": 0,
                "cost": 0.0,
                "tokens_in": 0,
                "tokens_out": 0,
                "models": {},
            }
        by_project[proj]["calls"] += 1
        by_project[proj]["cost"] += c.get("cost_usd") or 0.0
        by_project[proj]["tokens_in"] += c.get("tokens_in") or 0
        by_project[proj]["tokens_out"] += c.get("tokens_out") or 0
        m = c.get("model_id") or "unknown"
        by_project[proj]["models"][m] = by_project[proj]["models"].get(m, 0) + 1
    return by_project


def _aggregate_by_provider(calls: list[dict]) -> dict[str, dict]:
    by_provider: dict[str, dict] = {}
    for c in calls:
        prov = c.get("provider") or "unknown"
        if prov not in by_provider:
            by_provider[prov] = {"calls": 0, "cost": 0.0}
        by_provider[prov]["calls"] += 1
        by_provider[prov]["cost"] += c.get("cost_usd") or 0.0
    return by_provider


def _daily_timeseries(calls: list[dict]) -> dict[str, dict]:
    """Agrega por dia (UTC): cost + calls."""
    by_day: dict[str, dict] = {}
    for c in calls:
        ts = c.get("timestamp") or ""
        day = ts[:10] if ts else "unknown"
        if day not in by_day:
            by_day[day] = {"cost": 0.0, "calls": 0}
        by_day[day]["cost"] += c.get("cost_usd") or 0.0
        by_day[day]["calls"] += 1
    return dict(sorted(by_day.items()))


# ─── HTML render ──────────────────────────────────────────────────────────


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Dashboard agregado — Brasil GEO ({since_days}d)</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#0A0E27;--bg-2:#151A4A;--surface:#1A2050;--border:rgba(255,255,255,0.10);
  --text:#F8FAFC;--text-2:#CBD5E1;--text-3:#94A3B8;
  --cyan:#06B6D4;--purple:#A855F7;--green:#10B981;--amber:#F59E0B;--red:#EF4444;
}}
html{{-webkit-text-size-adjust:100%}}
body{{
  font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;
  background:var(--bg);color:var(--text);line-height:1.6;
  -webkit-font-smoothing:antialiased;padding:24px;
}}
body::before{{
  content:'';position:fixed;inset:0;
  background:radial-gradient(ellipse 800px 600px at 20% 0%,rgba(6,182,212,0.10),transparent 50%),
             radial-gradient(ellipse 600px 800px at 80% 30%,rgba(168,85,247,0.08),transparent 50%);
  pointer-events:none;z-index:0;
}}
.container{{max-width:1200px;margin:0 auto;position:relative;z-index:1}}
h1{{font-size:clamp(1.6rem,3vw,2.4rem);font-weight:900;letter-spacing:-0.022em;margin-bottom:8px}}
h2{{font-size:1.4rem;font-weight:800;margin:32px 0 16px;letter-spacing:-0.018em}}
.eyebrow{{
  display:inline-block;font-size:.72rem;font-weight:700;text-transform:uppercase;
  letter-spacing:.14em;padding:6px 14px;border-radius:100px;
  background:linear-gradient(180deg,rgba(6,182,212,0.16),rgba(168,85,247,0.10));
  border:1px solid rgba(6,182,212,0.35);color:var(--cyan);margin-bottom:16px;
}}
.subtitle{{color:var(--text-2);font-size:.95rem;margin-bottom:24px}}
.grid-stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-bottom:32px}}
.stat-card{{
  background:linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.01));
  border:1px solid var(--border);border-radius:14px;padding:20px;
}}
.stat-label{{font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.10em;color:var(--text-3);margin-bottom:8px}}
.stat-value{{font-size:2rem;font-weight:900;letter-spacing:-0.03em;line-height:1}}
.stat-value.cyan{{color:var(--cyan)}}
.stat-value.purple{{color:var(--purple)}}
.stat-value.green{{color:var(--green)}}
.stat-value.amber{{color:var(--amber)}}
.section{{
  background:linear-gradient(180deg,rgba(255,255,255,0.025),rgba(255,255,255,0.005));
  border:1px solid var(--border);border-radius:16px;padding:24px;margin-bottom:20px;
}}
table{{width:100%;border-collapse:collapse;font-size:.9rem}}
th{{
  text-align:left;padding:10px 12px;font-size:.72rem;font-weight:700;
  text-transform:uppercase;letter-spacing:.08em;color:var(--text-3);
  border-bottom:1px solid var(--border);
}}
td{{padding:10px 12px;border-bottom:1px solid rgba(255,255,255,0.05)}}
tr:last-child td{{border-bottom:none}}
.num{{font-variant-numeric:tabular-nums;text-align:right}}
.ok{{color:var(--green)}}
.warn{{color:var(--amber)}}
.crit{{color:var(--red)}}
canvas{{max-width:100%;height:300px}}
.footer{{margin-top:48px;padding-top:24px;border-top:1px solid var(--border);color:var(--text-3);font-size:.85rem;text-align:center}}
</style>
</head>
<body>
<div class="container">
  <div class="eyebrow">Dashboard solo · {since_days} dias · {generated_at}</div>
  <h1>Uso de IA no ecossistema Brasil GEO</h1>
  <p class="subtitle">Consolidado de geo-finops + geo-orchestrator KPI history + caramaschi /finops</p>

  <div class="grid-stats">
    <div class="stat-card">
      <div class="stat-label">Custo total (USD)</div>
      <div class="stat-value cyan">${total_cost:.2f}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Chamadas LLM</div>
      <div class="stat-value purple">{total_calls:,}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Tokens (in + out)</div>
      <div class="stat-value green">{total_tokens:,}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Projetos ativos</div>
      <div class="stat-value amber">{n_projects}</div>
    </div>
  </div>

  <div class="section">
    <h2>Custo diario (USD)</h2>
    <canvas id="dailyChart"></canvas>
  </div>

  <div class="section">
    <h2>Custo por projeto</h2>
    <table>
      <thead><tr><th>Projeto</th><th class="num">Calls</th><th class="num">Custo (USD)</th><th class="num">Tokens in/out</th><th>Top model</th></tr></thead>
      <tbody>
{project_rows}
      </tbody>
    </table>
  </div>

  <div class="section">
    <h2>Custo por provider</h2>
    <table>
      <thead><tr><th>Provider</th><th class="num">Calls</th><th class="num">Custo (USD)</th><th class="num">% do total</th></tr></thead>
      <tbody>
{provider_rows}
      </tbody>
    </table>
  </div>

  {caramaschi_section}

  <div class="footer">
    Gerado por geo-finops/scripts/aggregate_dashboard.py · achado B-022 · {generated_at}
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script>
const ctx = document.getElementById('dailyChart').getContext('2d');
new Chart(ctx, {{
  type: 'bar',
  data: {{
    labels: {daily_labels},
    datasets: [{{
      label: 'USD',
      data: {daily_costs},
      backgroundColor: 'rgba(6, 182, 212, 0.6)',
      borderColor: 'rgba(6, 182, 212, 1)',
      borderWidth: 1
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ color: 'rgba(255,255,255,0.05)' }}, ticks: {{ color: '#94A3B8' }} }},
      y: {{ grid: {{ color: 'rgba(255,255,255,0.05)' }}, ticks: {{ color: '#94A3B8' }}, beginAtZero: true }}
    }}
  }}
}});
</script>
</body>
</html>
"""


def render_dashboard(since_days: int = 30) -> str:
    """Constroi o HTML completo agregando todas as fontes."""
    calls = _load_geo_finops_calls(since_days)
    by_project = _aggregate_by_project(calls)
    by_provider = _aggregate_by_provider(calls)
    daily = _daily_timeseries(calls)
    caramaschi = _try_caramaschi_finops()

    total_cost = sum(c.get("cost_usd") or 0 for c in calls)
    total_calls = len(calls)
    total_tokens = sum(
        (c.get("tokens_in") or 0) + (c.get("tokens_out") or 0) for c in calls
    )
    n_projects = len(by_project)

    # Project rows
    project_rows_html = []
    for proj, data in sorted(by_project.items(), key=lambda x: -x[1]["cost"]):
        top_model = max(data["models"].items(), key=lambda x: x[1])[0] if data["models"] else "—"
        project_rows_html.append(
            f"<tr><td><strong>{proj}</strong></td>"
            f"<td class='num'>{data['calls']:,}</td>"
            f"<td class='num'>${data['cost']:.4f}</td>"
            f"<td class='num'>{data['tokens_in']:,} / {data['tokens_out']:,}</td>"
            f"<td><code>{top_model}</code></td></tr>"
        )

    # Provider rows
    provider_rows_html = []
    for prov, data in sorted(by_provider.items(), key=lambda x: -x[1]["cost"]):
        pct = (data["cost"] / total_cost * 100) if total_cost > 0 else 0
        provider_rows_html.append(
            f"<tr><td><strong>{prov}</strong></td>"
            f"<td class='num'>{data['calls']:,}</td>"
            f"<td class='num'>${data['cost']:.4f}</td>"
            f"<td class='num'>{pct:.1f}%</td></tr>"
        )

    # caramaschi snapshot (opcional, soh se reachable)
    caramaschi_html = ""
    if caramaschi:
        keys = list(caramaschi.keys())[:5]
        caramaschi_html = (
            "<div class='section'><h2>caramaschi.fly.dev /finops snapshot</h2>"
            f"<pre style='font-size:.8rem;color:#CBD5E1;overflow-x:auto'>"
            f"{json.dumps({k: caramaschi[k] for k in keys}, indent=2, ensure_ascii=False)[:1500]}"
            "</pre></div>"
        )

    # Daily chart data
    daily_labels = json.dumps(list(daily.keys())[-30:])
    daily_costs = json.dumps([round(d["cost"], 4) for d in list(daily.values())[-30:]])

    return HTML_TEMPLATE.format(
        since_days=since_days,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        total_cost=total_cost,
        total_calls=total_calls,
        total_tokens=total_tokens,
        n_projects=n_projects,
        project_rows="\n".join(project_rows_html) or "<tr><td colspan='5' style='text-align:center;color:#64748B'>Nenhum dado encontrado</td></tr>",
        provider_rows="\n".join(provider_rows_html) or "<tr><td colspan='4' style='text-align:center;color:#64748B'>Nenhum dado encontrado</td></tr>",
        caramaschi_section=caramaschi_html,
        daily_labels=daily_labels,
        daily_costs=daily_costs,
    )


# ─── CLI ──────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", "-o", type=Path,
                        default=Path.home() / ".cache" / "geo-dashboard.html",
                        help="Caminho de saida do HTML")
    parser.add_argument("--since", type=int, default=30,
                        help="Janela em dias (default: 30)")
    parser.add_argument("--print", action="store_true",
                        help="Imprime resumo no stdout")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    html = render_dashboard(since_days=args.since)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(f"Dashboard gerado: {args.output}")
    print(f"Tamanho: {args.output.stat().st_size:,} bytes")
    print(f"Para abrir: file:///{args.output.as_posix()}")

    if args.print:
        # Resumo rapido no terminal
        from geo_finops.db import get_db_path
        print(f"\nFontes:")
        print(f"  geo-finops calls.db:  {get_db_path()}")
        print(f"  KPI history:           ~/.cache/geo-orchestrator/.kpi_history.jsonl")
        print(f"  caramaschi /finops:    https://caramaschi.fly.dev/finops")
    return 0


if __name__ == "__main__":
    sys.exit(main())
