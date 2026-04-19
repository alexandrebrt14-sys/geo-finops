"""Health check completo do pipeline geo-finops.

Valida:

1. Pacote ``geo_finops`` importavel
2. SQLite local existe e tem dados
3. Schema correto (todas colunas)
4. Constraint UNIQUE funcional (dedup)
5. Migracoes historicas registradas
6. Supabase ``finops_calls`` existe e contem dados
7. Sync status: pending vs synced vs error
8. Snapshot estatico existe e eh fresco
9. Endpoint ``/api/finops/llm-usage`` responde 200 (opcional)
10. Task Scheduler ``GeoFinOpsSync`` registrado (Windows)
11. 4 adapters thin existem nos projetos
12. Callers orfaos instrumentados
13. Re-sync idempotente (409 Conflict tratado como sucesso)

Uso:

    python scripts/health_check.py
    python scripts/health_check.py --site https://alexandrecaramaschi.com

Refatorado em 2026-04-19:
- Supabase creds agora via ``config.load_supabase_creds`` (sem hardcode)
- Paths de adapters e snapshot deriva de ``config.get_workspace_root``
- Callers orfaos sao opcionais se o workspace nao existir
- Cada check exporta seu resultado como ``Check`` imutavel
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geo_finops.config import (  # noqa: E402
    get_snapshot_path,
    get_workspace_root,
    load_supabase_creds,
)


@dataclass
class Check:
    """Resultado imutavel de uma verificacao."""

    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        icon = "[OK]  " if self.passed else "[FAIL]"
        return f"{icon} {self.name}: {self.detail}"


def _ok(name: str, detail: str = "") -> Check:
    return Check(name=name, passed=True, detail=detail)


def _fail(name: str, detail: str) -> Check:
    return Check(name=name, passed=False, detail=detail)


# ---------------------------------------------------------------------------
# Checks individuais
# ---------------------------------------------------------------------------


def check_package_import() -> Check:
    name = "Pacote geo_finops importavel"
    try:
        from geo_finops import get_db_path, query_calls, track_call  # noqa: F401

        return _ok(name, "track_call, query_calls, get_db_path -> OK")
    except Exception as exc:
        return _fail(name, str(exc))


def check_local_db() -> Check:
    name = "SQLite local existe e tem dados"
    try:
        from geo_finops.db import get_connection, get_db_path

        path = get_db_path()
        if not path.exists():
            return _fail(name, f"db nao existe: {path}")
        conn = get_connection()
        try:
            n = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
            cost = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls").fetchone()[0]
        finally:
            conn.close()
        if n == 0:
            return _fail(name, "0 calls no banco")
        return _ok(name, f"{n} calls, ${cost:.2f}, path={path}")
    except Exception as exc:
        return _fail(name, str(exc))


def check_local_schema() -> Check:
    name = "Schema SQLite esta completo"
    try:
        from geo_finops.db import get_connection

        required = {
            "id",
            "timestamp",
            "project",
            "run_id",
            "task_type",
            "model_id",
            "provider",
            "tokens_in",
            "tokens_out",
            "cost_usd",
            "success",
            "metadata",
            "sync_status",
            "synced_at",
        }
        conn = get_connection()
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(llm_calls)")}
        finally:
            conn.close()
        missing = required - cols
        if missing:
            return _fail(name, f"missing cols: {missing}")
        return _ok(name, f"{len(cols)} cols presentes")
    except Exception as exc:
        return _fail(name, str(exc))


def check_local_dedup() -> Check:
    name = "Constraint UNIQUE funcional (dedup)"
    try:
        from geo_finops.db import get_connection
        from geo_finops.tracker import track_call

        ts = "2026-01-01T00:00:00+00:00"
        conn = get_connection()
        conn.execute("DELETE FROM llm_calls WHERE project='_health_check'")
        conn.close()

        id1 = track_call(
            "_health_check",
            "test-model",
            1,
            1,
            0.001,
            run_id="hc",
            task_type="t",
            timestamp=ts,
        )
        id2 = track_call(
            "_health_check",
            "test-model",
            1,
            1,
            0.001,
            run_id="hc",
            task_type="t",
            timestamp=ts,
        )

        conn = get_connection()
        conn.execute("DELETE FROM llm_calls WHERE project='_health_check'")
        conn.close()

        if id1 and not id2:
            return _ok(name, "dedup ativa: 2a insercao retornou None")
        return _fail(name, f"dedup falhou: id1={id1}, id2={id2}")
    except Exception as exc:
        return _fail(name, str(exc))


def check_migrations() -> Check:
    name = "Migracoes historicas registradas"
    try:
        from geo_finops.db import get_connection

        conn = get_connection()
        try:
            rows = conn.execute("SELECT name, rows_added FROM migrations").fetchall()
        finally:
            conn.close()
        if not rows:
            return _fail(name, "nenhuma migracao registrada")
        total = sum(r[1] or 0 for r in rows)
        names = [r[0] for r in rows]
        return _ok(name, f"{len(rows)} migracoes, {total} rows total: {names}")
    except Exception as exc:
        return _fail(name, str(exc))


def check_supabase() -> Check:
    name = "Supabase finops_calls existe e tem dados"
    try:
        import httpx

        url, key = load_supabase_creds()
        if not url or not key:
            return _fail(name, "supabase creds ausentes (env ou .env)")
        h = {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact"}
        r = httpx.get(
            f"{url}/rest/v1/finops_calls?select=id&limit=1",
            headers=h,
            timeout=15,
        )
        if r.status_code not in (200, 206):
            return _fail(name, f"HTTP {r.status_code}: {r.text[:150]}")
        total = r.headers.get("content-range", "0/0").split("/")[-1]
        return _ok(name, f"{total} linhas no Supabase")
    except Exception as exc:
        return _fail(name, str(exc))


def check_sync_status() -> Check:
    name = "Sync status: pending vs synced"
    try:
        from geo_finops.aggregates import sync_status_counts

        counts = sync_status_counts()
        if counts["error"] > 0:
            return _fail(
                name,
                f"{counts['error']} rows em erro "
                f"(pending={counts['pending']}, synced={counts['synced']})",
            )
        return _ok(
            name,
            f"pending={counts['pending']}, synced={counts['synced']}, error=0",
        )
    except Exception as exc:
        return _fail(name, str(exc))


def check_snapshot() -> Check:
    name = "Snapshot estatico fresco"
    try:
        path = get_snapshot_path()
        if path is None or not path.exists():
            return _fail(name, f"nao existe: {path}")
        snap = json.loads(path.read_text(encoding="utf-8"))
        gen = snap.get("generated_at")
        if not gen:
            return _fail(name, "generated_at ausente")
        age_h = (
            datetime.now(timezone.utc) - datetime.fromisoformat(gen.replace("Z", "+00:00"))
        ).total_seconds() / 3600
        n = snap.get("totals", {}).get("calls", 0)
        if age_h > 168:
            return _fail(name, f"snapshot velho: {age_h:.1f}h, {n} calls")
        return _ok(
            name,
            f"age={age_h:.1f}h, {n} calls, ${snap['totals']['cost_usd']:.2f}",
        )
    except Exception as exc:
        return _fail(name, str(exc))


def check_live_endpoint(site: str) -> Check:
    name = "Endpoint /api/finops/llm-usage live"
    try:
        import httpx

        r = httpx.get(f"{site}/api/finops/llm-usage", timeout=15)
        if r.status_code != 200:
            return _fail(name, f"HTTP {r.status_code}")
        d = r.json()
        return _ok(
            name,
            f"{d['totals']['calls']} calls, ${d['totals']['cost_usd']:.2f}, "
            f"age={d.get('meta', {}).get('age_hours', '?')}h",
        )
    except Exception as exc:
        return _fail(name, str(exc))


def check_task_scheduler() -> Check:
    name = "Task Scheduler GeoFinOpsSync"
    if os.name != "nt":
        return _ok(name, "skip (nao Windows)")
    try:
        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "schtasks /Query /TN 'GeoFinOpsSync' /FO CSV /NH",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            return _fail(name, "task nao registrada")
        line = r.stdout.strip().strip('"').replace('","', "|")
        parts = line.split("|")
        if len(parts) >= 3:
            return _ok(name, f"next_run={parts[1]}, status={parts[2]}")
        return _ok(name, r.stdout.strip()[:80])
    except Exception as exc:
        return _fail(name, str(exc))


def check_adapters() -> Check:
    name = "4 adapters thin existem nos projetos"
    workspace = get_workspace_root()
    if workspace is None:
        return _ok(name, "skip (workspace canonico ausente)")
    paths = [
        workspace / "geo-orchestrator" / "src" / "unified_finops.py",
        workspace / "papers" / "src" / "finops" / "unified_adapter.py",
        workspace / "curso-factory" / "src" / "unified_finops.py",
        workspace / "caramaschi" / "src" / "scripts" / "ac_core" / "unified_finops.py",
    ]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        return _fail(name, f"missing: {missing}")
    return _ok(name, f"{len(paths)}/{len(paths)} adapters")


def check_orphan_callers_instrumented() -> Check:
    name = "Callers orfaos instrumentados (gpt-4o-mini)"
    workspace = get_workspace_root()
    if workspace is None:
        return _ok(name, "skip (workspace canonico ausente)")
    files_to_check = {
        "scripts/python/llm_utils.py": "_track_to_geo_finops",
        "caramaschi/src/scripts/whatsapp_nlp_handler.py": "_track_caramaschi",
        "landing-page-geo/src/lib/geo-checker/llm-probes.ts": "trackProbeCall",
        "landing-page-geo/src/app/api/finops/track/route.ts": "/rest/v1/finops_calls",
    }
    missing: list[str] = []
    for relpath, marker in files_to_check.items():
        p = workspace / relpath
        if not p.exists():
            missing.append(f"{relpath} (file)")
            continue
        if marker not in p.read_text(encoding="utf-8"):
            missing.append(f"{relpath} (marker '{marker}')")
    if missing:
        return _fail(name, f"missing: {missing}")
    return _ok(name, f"{len(files_to_check)}/{len(files_to_check)} instrumentados")


def check_resync_idempotency() -> Check:
    """Re-sincroniza uma row ja sincronizada — valida que sync.py trata 409 OK."""
    name = "Re-sync idempotente (409 Conflict tratado como sucesso)"
    try:
        from geo_finops.db import get_connection
        from geo_finops.sync import sync as run_sync

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT id FROM llm_calls WHERE sync_status='synced' LIMIT 1"
            ).fetchone()
            if not row:
                return _fail(name, "sem linhas synced para testar")
            rid = row["id"]
            conn.execute("UPDATE llm_calls SET sync_status='pending' WHERE id=?", (rid,))
        finally:
            conn.close()

        url, key = load_supabase_creds()
        if not url or not key:
            return _fail(name, "supabase creds ausentes")

        result = run_sync(batch_size=10, dry_run=False)
        if result.get("errors", 0) > 0:
            return _fail(name, f"sync re-tentou e marcou como erro: {result}")
        if result.get("synced", 0) == 0:
            return _fail(name, f"nada foi processado: {result}")
        return _ok(name, f"row id={rid} re-syncada com sucesso (409 OK)")
    except Exception as exc:
        return _fail(name, str(exc))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_all(site: str) -> list[Check]:
    return [
        check_package_import(),
        check_local_db(),
        check_local_schema(),
        check_local_dedup(),
        check_migrations(),
        check_supabase(),
        check_sync_status(),
        check_snapshot(),
        check_live_endpoint(site),
        check_task_scheduler(),
        check_adapters(),
        check_orphan_callers_instrumented(),
        check_resync_idempotency(),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="https://alexandrecaramaschi.com")
    args = parser.parse_args()

    checks = run_all(args.site)

    print()
    print("=" * 80)
    print("HEALTH CHECK — geo-finops pipeline ponta-a-ponta")
    print("=" * 80)
    for c in checks:
        print(c)
    print("=" * 80)
    passed = sum(1 for c in checks if c.passed)
    total = len(checks)
    print(f"RESULT: {passed}/{total} passed")
    print()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
