"""Exporta snapshot agregado do ``calls.db`` para JSON consumido pelo site.

Roda local (ou pelo Task Scheduler) e gera por default:

    <workspace>/landing-page-geo/public/finops-snapshot.json

O site Next.js le o snapshot via ``/finops-snapshot.json`` ou via API
route com ISR revalidation.

Conteudo do snapshot:

- ``generated_at``, ``period``, ``schema_version``
- ``totals``: ``{calls, cost_usd, tokens_in, tokens_out, projects, providers}``
- ``by_provider``, ``by_project``, ``by_model``
- ``daily`` (serie dos ultimos 30 dias)

Refatorado em 2026-04-19: todas as queries foram movidas para
``geo_finops.aggregates``, e o path de saida passa a respeitar
``GEO_FINOPS_SNAPSHOT_PATH`` (override) ou o workspace canonico.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geo_finops import aggregates  # noqa: E402
from geo_finops.config import get_snapshot_path  # noqa: E402
from geo_finops.db import get_connection, init_db  # noqa: E402


def build_snapshot(days: int = 30) -> dict:
    init_db()
    conn = get_connection()
    try:
        totals = aggregates.totals(conn=conn)
        by_provider = aggregates.aggregate_by("provider", conn=conn)
        by_project = aggregates.aggregate_by("project", conn=conn)
        by_model = aggregates.top_models(limit=15, conn=conn)
        daily = aggregates.daily_timeseries(days=days, conn=conn)
    finally:
        conn.close()

    n_providers = len({r["key"] for r in by_provider if r["key"] != "unknown"})
    n_projects = len({r["key"] for r in by_project})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "period": {
            "start": totals.get("period_start"),
            "end": totals.get("period_end"),
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


def _resolve_output(cli_output: Path | None) -> Path:
    """Ordem: ``--output`` CLI > ``GEO_FINOPS_SNAPSHOT_PATH`` > workspace > stdout fallback."""
    if cli_output is not None:
        return cli_output
    from_cfg = get_snapshot_path()
    if from_cfg is not None:
        return from_cfg
    # Ultimo fallback: mesmo diretorio do script
    return ROOT / "finops-snapshot.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Path de saida (override de GEO_FINOPS_SNAPSHOT_PATH)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Janela da serie diaria",
    )
    args = parser.parse_args()

    snapshot = build_snapshot(days=args.days)
    output = _resolve_output(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    t = snapshot["totals"]
    print(f"Snapshot exportado: {output}")
    print(f"  generated_at: {snapshot['generated_at']}")
    print(
        f"  totals:       {t['calls']} calls, ${t['cost_usd']:.2f}, "
        f"{t['providers']} providers, {t['projects']} projects"
    )
    print(f"  daily points: {len(snapshot['daily'])}")
    print(f"  top models:   {len(snapshot['by_model'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
