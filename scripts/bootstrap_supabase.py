"""Cria a tabela finops_calls no Supabase via REST + verifica conectividade.

Tenta 3 estrategias em ordem:
1. POST /rest/v1/rpc/exec_ddl  (se houver function custom)
2. Inserir linha de teste — se 404 a tabela nao existe, abortar
3. Usar PostgREST upsert para validar acesso

Para criar a tabela sem precisar passar pelo dashboard, este script:
- Tenta upsert numa linha de teste
- Se 404 (tabela inexistente), exibe DDL para o user copiar no SQL editor
- Imediatamente abre o SQL editor URL no browser via print
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

DDL = """
CREATE TABLE IF NOT EXISTS public.finops_calls (
    id           BIGSERIAL PRIMARY KEY,
    timestamp    TIMESTAMPTZ NOT NULL,
    project      TEXT NOT NULL,
    run_id       TEXT,
    task_type    TEXT,
    model_id     TEXT NOT NULL,
    provider     TEXT NOT NULL,
    tokens_in    INTEGER NOT NULL DEFAULT 0,
    tokens_out   INTEGER NOT NULL DEFAULT 0,
    cost_usd     NUMERIC(12,6) NOT NULL DEFAULT 0,
    success      BOOLEAN NOT NULL DEFAULT TRUE,
    metadata     JSONB,
    local_id     INTEGER,
    synced_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_finops_calls_dedup
    ON public.finops_calls(timestamp, project, COALESCE(run_id, ''), model_id);

CREATE INDEX IF NOT EXISTS idx_finops_calls_timestamp ON public.finops_calls(timestamp);
CREATE INDEX IF NOT EXISTS idx_finops_calls_project   ON public.finops_calls(project);
CREATE INDEX IF NOT EXISTS idx_finops_calls_provider  ON public.finops_calls(provider);
CREATE INDEX IF NOT EXISTS idx_finops_calls_model     ON public.finops_calls(model_id);

-- Permite inserts da service_role (default em tabelas novas)
ALTER TABLE public.finops_calls ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "service_role full access" ON public.finops_calls;
CREATE POLICY "service_role full access"
    ON public.finops_calls FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
"""


def _load_creds() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        env_file = Path("C:/Sandyboxclaude/geo-orchestrator/.env")
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("SUPABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("SUPABASE_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not url or not key:
        print("FATAL: SUPABASE_URL/SUPABASE_KEY ausentes")
        sys.exit(1)
    return url, key


def check_table_exists(url: str, key: str) -> bool:
    """Tenta GET /rest/v1/finops_calls?limit=0 — 200=exists, 404=missing."""
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = httpx.get(f"{url.rstrip('/')}/rest/v1/finops_calls?limit=0", headers=headers, timeout=15)
    return r.status_code == 200


def try_create_via_pg_meta(url: str, key: str) -> bool:
    """Supabase Studio expoe /pg/query para a service_role em alguns projetos."""
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    # Tenta endpoint pg_meta interno (raro estar publicamente exposto)
    candidates = [
        f"{url.rstrip('/')}/pg/query",
        f"{url.rstrip('/')}/database/query",
    ]
    for endpoint in candidates:
        try:
            r = httpx.post(endpoint, headers=headers, json={"query": DDL}, timeout=20)
            if r.status_code in (200, 201):
                print(f"OK via {endpoint}")
                return True
        except httpx.RequestError:
            continue
    return False


def main():
    url, key = _load_creds()
    print(f"Supabase URL: {url}")

    # 1) Verifica se ja existe
    if check_table_exists(url, key):
        print("Tabela 'finops_calls' JA EXISTE no Supabase.")
        return

    print("Tabela 'finops_calls' NAO existe. Tentando criar via API...")

    # 2) Tenta criar via pg_meta endpoint (raramente disponivel)
    if try_create_via_pg_meta(url, key):
        if check_table_exists(url, key):
            print("Criada com sucesso via API.")
            return

    # 3) Fallback: instrucoes para criar manualmente
    print()
    print("=" * 70)
    print("API direta nao tem permissao DDL. Use o SQL Editor do dashboard:")
    project_ref = url.split("//")[1].split(".")[0]
    print(f"  https://supabase.com/dashboard/project/{project_ref}/sql/new")
    print()
    print("DDL a executar:")
    print("=" * 70)
    print(DDL)
    print("=" * 70)
    sys.exit(2)


if __name__ == "__main__":
    main()
