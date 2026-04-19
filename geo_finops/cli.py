"""CLI de inspecao do ``calls.db`` unificado.

Uso:

    python -m geo_finops.cli status
    python -m geo_finops.cli summary --by provider
    python -m geo_finops.cli summary --start 2026-03-01
    python -m geo_finops.cli list --project papers --limit 20
    python -m geo_finops.cli migrate
    python -m geo_finops.cli sync --dry-run
"""

from __future__ import annotations

import argparse
import json

from .aggregates import sync_status_counts
from .db import get_connection, get_db_path, init_db
from .tracker import aggregate_by, query_calls


def cmd_status(_args: argparse.Namespace) -> None:
    init_db()
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        cost = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls").fetchone()[0]
        first = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM llm_calls").fetchone()
        migs = conn.execute("SELECT COUNT(*) FROM migrations").fetchone()[0]
    finally:
        conn.close()
    counts = sync_status_counts()
    print(f"DB:           {get_db_path()}")
    print(f"Total calls:  {total:,}")
    print(f"Custo total:  ${cost:.4f}")
    print(f"Periodo:      {first[0]} a {first[1]}")
    print("Sync status:")
    print(f"  pending:    {counts['pending']:,}")
    print(f"  synced:     {counts['synced']:,}")
    print(f"  error:      {counts['error']:,}")
    print(f"Migracoes:    {migs}")


def cmd_summary(args: argparse.Namespace) -> None:
    rows = aggregate_by(args.by, start=args.start, end=args.end)
    print(f"\n=== Agregacao por {args.by} ({args.start or 'all'} -> {args.end or 'all'}) ===")
    print(f"{args.by:<25} {'calls':>8} {'tok_in':>12} {'tok_out':>12} {'cost USD':>12}")
    print("-" * 75)
    for r in rows:
        key = (r["key"] or "NULL")[:25]
        print(
            f"{key:<25} {r['calls']:>8} "
            f"{r['tokens_in'] or 0:>12} {r['tokens_out'] or 0:>12} "
            f"{r['cost_usd'] or 0:>12.4f}"
        )


def cmd_list(args: argparse.Namespace) -> None:
    rows = query_calls(
        project=args.project,
        provider=args.provider,
        start=args.start,
        end=args.end,
        limit=args.limit,
    )
    print(f"{'timestamp':<26} {'project':<18} {'provider':<12} {'model':<28} {'cost':>10}")
    print("-" * 100)
    for r in rows:
        print(
            f"{r['timestamp'][:26]:<26} {r['project']:<18} {r['provider']:<12} "
            f"{r['model_id'][:28]:<28} {r['cost_usd']:>10.4f}"
        )


def cmd_migrate(_args: argparse.Namespace) -> None:
    from .migrate import migrate_all

    print("Migrando historicos...")
    result = migrate_all()
    print(json.dumps(result, indent=2))
    print(f"\nTotal: {sum(result.values())} linhas migradas")


def cmd_sync(args: argparse.Namespace) -> None:
    from .sync import sync

    result = sync(batch_size=args.batch_size, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geo_finops")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("summary")
    p.add_argument(
        "--by",
        choices=["provider", "project", "model_id", "task_type"],
        default="provider",
    )
    p.add_argument("--start")
    p.add_argument("--end")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("list")
    p.add_argument("--project")
    p.add_argument("--provider")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("migrate")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("sync")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--batch-size", type=int, default=500)
    p.set_defaults(func=cmd_sync)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
