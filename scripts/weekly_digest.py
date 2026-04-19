#!/usr/bin/env python3
"""Weekly FinOps digest — CLI thin para o pacote ``geo_finops.digest``.

Refatorado em 2026-04-19: a logica (aggregates, formatters, delivery)
vive em ``geo_finops.digest`` como pacote testavel. Este script e
apenas o entry point argparse.

Uso:

    python scripts/weekly_digest.py                         # markdown stdout
    python scripts/weekly_digest.py --format json           # json indent
    python scripts/weekly_digest.py --format whatsapp       # compacto
    python scripts/weekly_digest.py --send-whatsapp         # envia via Meta API
    python scripts/weekly_digest.py --weeks-back 1          # semana passada
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geo_finops.config import DEFAULT_WHATSAPP_OWNER  # noqa: E402
from geo_finops.digest import (  # noqa: E402
    build_digest,
    format_json,
    format_markdown,
    format_whatsapp,
    save_snapshot,
    send_whatsapp,
)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--weeks-back",
        type=int,
        default=0,
        help="0 = semana atual, 1 = semana passada, etc.",
    )
    p.add_argument(
        "--send-whatsapp",
        action="store_true",
        help="Envia digest via WhatsApp Meta API",
    )
    p.add_argument(
        "--to",
        default=DEFAULT_WHATSAPP_OWNER,
        help="Numero destino WhatsApp (formato API, sem +)",
    )
    p.add_argument(
        "--format",
        choices=["markdown", "whatsapp", "json"],
        default="markdown",
    )
    args = p.parse_args()

    digest = build_digest(weeks_back=args.weeks_back)
    snapshot_path = save_snapshot(digest)

    if args.format == "json":
        print(format_json(digest))
    elif args.format == "whatsapp":
        print(format_whatsapp(digest))
    else:
        print(format_markdown(digest))

    print(f"\n[snapshot] {snapshot_path}", file=sys.stderr)

    if args.send_whatsapp:
        result = send_whatsapp(format_whatsapp(digest), to=args.to)
        print(f"[whatsapp] {json.dumps(result)}", file=sys.stderr)
        if not result.get("ok"):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
