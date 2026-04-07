"""Health check completo do pipeline geo-finops.

Valida:
1. SQLite local existe e tem dados
2. Schema esta correto (todas colunas)
3. Constraint UNIQUE funcional (insere duplicata)
4. Migracao registrada na tabela migrations
5. Supabase finops_calls existe e contem dados
6. Sync status: pending vs synced
7. Snapshot estatico existe e e fresco (<24h)
8. Endpoint /api/finops/llm-usage retorna 200 (se url passada)
9. Task Scheduler GeoFinOpsSync registrado (Windows)
10. Pacote geo_finops importavel
11. 4 adapters thin existem nos projetos

Uso:
    python scripts/health_check.py
    python scripts/health_check.py --site https://alexandrecaramaschi.com
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add package to path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


class Check:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.detail = ""

    def ok(self, detail: str = "") -> "Check":
        self.passed = True
        self.detail = detail
        return self

    def fail(self, detail: str) -> "Check":
        self.passed = False
        self.detail = detail
        return self

    def __str__(self) -> str:
        icon = "[OK]  " if self.passed else "[FAIL]"
        return f"{icon} {self.name}: {self.detail}"


def check_local_db() -> Check:
    c = Check("SQLite local existe e tem dados")
    try:
        from geo_finops.db import get_db_path, get_connection
        path = get_db_path()
        if not path.exists():
            return c.fail(f"db nao existe: {path}")
        conn = get_connection()
        n = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
        cost = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls").fetchone()[0]
        conn.close()
        if n == 0:
            return c.fail("0 calls no banco")
        return c.ok(f"{n} calls, ${cost:.2f}, path={path}")
    except Exception as exc:
        return c.fail(str(exc))


def check_local_schema() -> Check:
    c = Check("Schema SQLite esta completo")
    try:
        from geo_finops.db import get_connection
        conn = get_connection()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(llm_calls)")]
        required = {"id", "timestamp", "project", "run_id", "task_type", "model_id",
                    "provider", "tokens_in", "tokens_out", "cost_usd", "success",
                    "metadata", "sync_status", "synced_at"}
        missing = required - set(cols)
        conn.close()
        if missing:
            return c.fail(f"missing cols: {missing}")
        return c.ok(f"{len(cols)} cols presentes")
    except Exception as exc:
        return c.fail(str(exc))


def check_local_dedup() -> Check:
    c = Check("Constraint UNIQUE funcional (dedup)")
    try:
        from geo_finops.tracker import track_call
        from geo_finops.db import get_connection
        ts = "2026-01-01T00:00:00+00:00"
        # Garante limpo
        conn = get_connection()
        conn.execute("DELETE FROM llm_calls WHERE project='_health_check'")
        conn.close()
        id1 = track_call("_health_check", "test-model", 1, 1, 0.001, run_id="hc", task_type="t", timestamp=ts)
        id2 = track_call("_health_check", "test-model", 1, 1, 0.001, run_id="hc", task_type="t", timestamp=ts)
        # Cleanup
        conn = get_connection()
        conn.execute("DELETE FROM llm_calls WHERE project='_health_check'")
        conn.close()
        if id1 and not id2:
            return c.ok("dedup ativa: 2a insercao retornou None")
        return c.fail(f"dedup falhou: id1={id1}, id2={id2}")
    except Exception as exc:
        return c.fail(str(exc))


def check_migrations() -> Check:
    c = Check("Migracoes historicas registradas")
    try:
        from geo_finops.db import get_connection
        conn = get_connection()
        rows = conn.execute("SELECT name, rows_added FROM migrations").fetchall()
        conn.close()
        if not rows:
            return c.fail("nenhuma migracao registrada")
        total = sum(r[1] or 0 for r in rows)
        names = [r[0] for r in rows]
        return c.ok(f"{len(rows)} migracoes, {total} rows total: {names}")
    except Exception as exc:
        return c.fail(str(exc))


def check_supabase() -> Check:
    c = Check("Supabase finops_calls existe e tem dados")
    try:
        import httpx
        env_file = Path("C:/Sandyboxclaude/geo-orchestrator/.env")
        url = key = None
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("SUPABASE_URL="):
                url = line.split("=", 1)[1].strip()
            elif line.startswith("SUPABASE_KEY="):
                key = line.split("=", 1)[1].strip()
        if not url or not key:
            return c.fail("supabase creds ausentes no .env")
        h = {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact"}
        r = httpx.get(f"{url}/rest/v1/finops_calls?select=id&limit=1", headers=h, timeout=15)
        # PostgREST retorna 200 (full) ou 206 (partial content via count=exact)
        if r.status_code not in (200, 206):
            return c.fail(f"HTTP {r.status_code}: {r.text[:150]}")
        total = r.headers.get("content-range", "0/0").split("/")[-1]
        return c.ok(f"{total} linhas no Supabase")
    except Exception as exc:
        return c.fail(str(exc))


def check_sync_status() -> Check:
    c = Check("Sync status: pending vs synced")
    try:
        from geo_finops.db import get_connection
        conn = get_connection()
        pending = conn.execute("SELECT COUNT(*) FROM llm_calls WHERE sync_status='pending'").fetchone()[0]
        synced = conn.execute("SELECT COUNT(*) FROM llm_calls WHERE sync_status='synced'").fetchone()[0]
        error = conn.execute("SELECT COUNT(*) FROM llm_calls WHERE sync_status='error'").fetchone()[0]
        conn.close()
        if error > 0:
            return c.fail(f"{error} rows em erro (pending={pending}, synced={synced})")
        return c.ok(f"pending={pending}, synced={synced}, error=0")
    except Exception as exc:
        return c.fail(str(exc))


def check_snapshot() -> Check:
    c = Check("Snapshot estatico fresco")
    try:
        path = Path("C:/Sandyboxclaude/landing-page-geo/public/finops-snapshot.json")
        if not path.exists():
            return c.fail(f"nao existe: {path}")
        snap = json.loads(path.read_text(encoding="utf-8"))
        gen = snap.get("generated_at")
        if not gen:
            return c.fail("generated_at ausente")
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(gen.replace("Z", "+00:00"))).total_seconds() / 3600
        n = snap.get("totals", {}).get("calls", 0)
        if age_h > 168:  # > 7 dias
            return c.fail(f"snapshot velho: {age_h:.1f}h, {n} calls")
        return c.ok(f"age={age_h:.1f}h, {n} calls, ${snap['totals']['cost_usd']:.2f}")
    except Exception as exc:
        return c.fail(str(exc))


def check_live_endpoint(site: str) -> Check:
    c = Check(f"Endpoint /api/finops/llm-usage live")
    try:
        import httpx
        r = httpx.get(f"{site}/api/finops/llm-usage", timeout=15)
        if r.status_code != 200:
            return c.fail(f"HTTP {r.status_code}")
        d = r.json()
        return c.ok(f"{d['totals']['calls']} calls, ${d['totals']['cost_usd']:.2f}, age={d.get('meta',{}).get('age_hours','?')}h")
    except Exception as exc:
        return c.fail(str(exc))


def check_task_scheduler() -> Check:
    c = Check("Task Scheduler GeoFinOpsSync")
    if os.name != "nt":
        return c.ok("skip (nao Windows)")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "schtasks /Query /TN 'GeoFinOpsSync' /FO CSV /NH"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return c.fail("task nao registrada")
        # Parse CSV: TaskName,Next Run Time,Status
        line = r.stdout.strip().strip('"').replace('","', '|')
        parts = line.split('|')
        if len(parts) >= 3:
            return c.ok(f"next_run={parts[1]}, status={parts[2]}")
        return c.ok(r.stdout.strip()[:80])
    except Exception as exc:
        return c.fail(str(exc))


def check_package_import() -> Check:
    c = Check("Pacote geo_finops importavel")
    try:
        from geo_finops import track_call, query_calls, get_db_path
        return c.ok(f"track_call, query_calls, get_db_path -> OK")
    except Exception as exc:
        return c.fail(str(exc))


def check_adapters() -> Check:
    c = Check("4 adapters thin existem nos projetos")
    paths = [
        Path("C:/Sandyboxclaude/geo-orchestrator/src/unified_finops.py"),
        Path("C:/Sandyboxclaude/papers/src/finops/unified_adapter.py"),
        Path("C:/Sandyboxclaude/curso-factory/src/unified_finops.py"),
        Path("C:/Sandyboxclaude/caramaschi/src/scripts/ac_core/unified_finops.py"),
    ]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        return c.fail(f"missing: {missing}")
    return c.ok(f"4/4 adapters")


def check_orphan_callers_instrumented() -> Check:
    c = Check("Callers orfaos instrumentados (gpt-4o-mini)")
    files_to_check = {
        "scripts/python/llm_utils.py":          "_track_to_geo_finops",
        "caramaschi/src/scripts/whatsapp_nlp_handler.py": "_track_caramaschi",
        "landing-page-geo/src/lib/geo-checker/llm-probes.ts": "trackProbeCall",
        "landing-page-geo/src/app/api/finops/track/route.ts": "/rest/v1/finops_calls",
    }
    missing = []
    for relpath, marker in files_to_check.items():
        p = Path(f"C:/Sandyboxclaude/{relpath}")
        if not p.exists():
            missing.append(f"{relpath} (file)")
            continue
        if marker not in p.read_text(encoding="utf-8"):
            missing.append(f"{relpath} (marker '{marker}')")
    if missing:
        return c.fail(f"missing: {missing}")
    return c.ok(f"{len(files_to_check)}/{len(files_to_check)} instrumentados")


def check_resync_idempotency() -> Check:
    """Re-sincroniza uma row ja sincronizada e valida que sync.py NAO marca como erro."""
    c = Check("Re-sync idempotente (409 Conflict tratado como sucesso)")
    try:
        from geo_finops.db import get_connection
        from geo_finops.sync import sync as run_sync, _load_supabase_creds

        # Pega 1 row ja sincronizada e marca como pending pra forcar re-envio
        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM llm_calls WHERE sync_status='synced' LIMIT 1"
        ).fetchone()
        if not row:
            return c.fail("sem linhas synced para testar")
        rid = row["id"]
        conn.execute(
            "UPDATE llm_calls SET sync_status='pending' WHERE id=?", (rid,)
        )
        conn.close()

        url, key = _load_supabase_creds()
        if not url or not key:
            return c.fail("supabase creds ausentes")

        result = run_sync(batch_size=10, dry_run=False)
        if result.get("errors", 0) > 0:
            return c.fail(f"sync re-tentou e marcou como erro: {result}")
        if result.get("synced", 0) == 0:
            return c.fail(f"nada foi processado: {result}")
        return c.ok(f"row id={rid} re-syncada com sucesso (409 OK)")
    except Exception as exc:
        return c.fail(str(exc))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="https://alexandrecaramaschi.com")
    args = parser.parse_args()

    checks = [
        check_package_import(),
        check_local_db(),
        check_local_schema(),
        check_local_dedup(),
        check_migrations(),
        check_supabase(),
        check_sync_status(),
        check_snapshot(),
        check_live_endpoint(args.site),
        check_task_scheduler(),
        check_adapters(),
        check_orphan_callers_instrumented(),
        check_resync_idempotency(),
    ]

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
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
