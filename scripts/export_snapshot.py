"""Exporta snapshot agregado do calls.db para JSON consumido pelo site.

Roda local (ou pelo Task Scheduler) e gera:
  landing-page-geo/public/finops-snapshot.json

O site Next.js pode entao fazer fetch do snapshot via /finops-snapshot.json
ou via API route que faz import dele com revalidacao ISR.

Conteudo do snapshot:
  - generated_at, period
  - totals: {calls, cost_usd, tokens_in, tokens_out, projects, providers}
  - by_provider, by_project, by_model
  - daily series (ultimos 30 dias)
  - top 10 modelos
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Adiciona path do pacote
HERE = Path(__file__).resolve().parent
ROOT_PKG = HERE.parent
sys.path.insert(0, str(ROOT_PKG))

from geo_finops.db import get_connection, init_db


SITE_OUTPUT = Path("C:/Sandyboxclaude/landing-page-geo/public/finops-snapshot.json")


def build_snapshot() -> dict:
    init_db()
    conn = get_connection()
    try:
        # Totais
        totals = conn.execute("""
            SELECT
                COUNT(*)            as calls,
                COALESCE(SUM(cost_usd), 0)   as cost_usd,
                COALESCE(SUM(tokens_in), 0)  as tokens_in,
                COALESCE(SUM(tokens_out), 0) as tokens_out
            FROM llm_calls
        """).fetchone()

        # Periodo
        period = conn.execute("""
            SELECT MIN(timestamp), MAX(timestamp) FROM llm_calls
        """).fetchone()

        # Por provider
        by_provider = [dict(r) for r in conn.execute("""
            SELECT provider as key,
                   COUNT(*) as calls,
                   SUM(tokens_in) as tokens_in,
                   SUM(tokens_out) as tokens_out,
                   SUM(cost_usd) as cost_usd
            FROM llm_calls
            GROUP BY provider
            ORDER BY cost_usd DESC
        """).fetchall()]

        # Por projeto
        by_project = [dict(r) for r in conn.execute("""
            SELECT project as key,
                   COUNT(*) as calls,
                   SUM(tokens_in) as tokens_in,
                   SUM(tokens_out) as tokens_out,
                   SUM(cost_usd) as cost_usd
            FROM llm_calls
            GROUP BY project
            ORDER BY cost_usd DESC
        """).fetchall()]

        # Top 10 modelos
        by_model = [dict(r) for r in conn.execute("""
            SELECT model_id as key,
                   provider,
                   COUNT(*) as calls,
                   SUM(tokens_in) as tokens_in,
                   SUM(tokens_out) as tokens_out,
                   SUM(cost_usd) as cost_usd
            FROM llm_calls
            GROUP BY model_id
            ORDER BY cost_usd DESC
            LIMIT 15
        """).fetchall()]

        # Ultimos 30 dias (serie diaria)
        thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        daily = [dict(r) for r in conn.execute("""
            SELECT substr(timestamp, 1, 10) as date,
                   COUNT(*) as calls,
                   SUM(cost_usd) as cost_usd
            FROM llm_calls
            WHERE timestamp >= ?
            GROUP BY date
            ORDER BY date
        """, (thirty_days_ago,)).fetchall()]

        # Counts auxiliares
        n_projects = len({r["key"] for r in by_project})
        n_providers = len({r["key"] for r in by_provider if r["key"] != "unknown"})
    finally:
        conn.close()

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "period": {
            "start": period[0],
            "end": period[1],
        },
        "totals": {
            "calls": totals["calls"],
            "cost_usd": round(totals["cost_usd"] or 0, 4),
            "tokens_in": totals["tokens_in"] or 0,
            "tokens_out": totals["tokens_out"] or 0,
            "projects": n_projects,
            "providers": n_providers,
        },
        "by_provider": by_provider,
        "by_project": by_project,
        "by_model": by_model,
        "daily": daily,
    }
    return snapshot


def main():
    snapshot = build_snapshot()
    SITE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    SITE_OUTPUT.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    t = snapshot["totals"]
    print(f"Snapshot exportado: {SITE_OUTPUT}")
    print(f"  generated_at: {snapshot['generated_at']}")
    print(f"  totals:       {t['calls']} calls, ${t['cost_usd']:.2f}, "
          f"{t['providers']} providers, {t['projects']} projects")
    print(f"  daily points: {len(snapshot['daily'])}")
    print(f"  top models:   {len(snapshot['by_model'])}")


if __name__ == "__main__":
    main()
