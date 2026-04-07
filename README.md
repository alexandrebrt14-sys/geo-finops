# geo-finops

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![Status](https://img.shields.io/badge/status-production-green.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)

**Tracking centralizado de uso de LLMs para todos os projetos do ecossistema Brasil GEO.**

Substitui 4 sistemas de tracking paralelos por um único SQLite local com sincronização diária para Supabase. Resolveu o problema de **505 calls órfãs** detectadas via OpenAI admin API que não estavam sendo rastreadas por nenhum dos sistemas anteriores.

**Estado atual**: 1.467 calls / $255,43 / 5 providers / 10 modelos / 3 projetos rastreados.

---

## Por que existe

Antes da consolidação, o ecossistema tinha 4 sistemas de tracking paralelos:

| Sistema | Onde | Formato | Quem usa |
|---|---|---|---|
| `cost_history.jsonl` | `geo-orchestrator/output/` | JSONL agregado | orchestrator |
| `papers.db::finops_usage` | `papers/data/papers.db` | SQLite por call | papers (coleta diária) |
| `costs.json` | `curso-factory/output/` | JSON array | curso-factory |
| `ac_core/finops.py` | `caramaschi/src/scripts/` | (não estruturado) | caramaschi |

**Problemas**:
- Auditoria FinOps via OpenAI admin API revelou 505 calls em `gpt-4o-mini` que **nenhum** dos 4 trackers locais registrava
- Cada projeto tinha schema diferente, impossível agregar
- Sem dashboard unificado
- Sem retention automática
- Sem sync com cloud

**Solução**: SQLite local único + helper Python compartilhado + adapters thin + sync noturno para Supabase.

---

## Arquitetura

```
+-------------+    +-------------+    +-------------+    +-------------+
| orchestrator|    |   papers    |    | curso-factory|    | caramaschi  |
| (adapter)   |    | (adapter)   |    | (adapter)    |    | (adapter)   |
+------+------+    +------+------+    +------+-------+    +------+------+
       |                  |                  |                   |
       +------------------+------------------+-------------------+
                                |
                                v
                  +-------------------------------+
                  |  geo_finops.tracker.track_call|
                  +---------------+---------------+
                                  |
                                  v
                  +-------------------------------+
                  |  ~/.config/geo-finops/calls.db|  (SQLite WAL)
                  +---------------+---------------+
                                  |
                       cron 23:50 sync
                                  |
                                  v
                  +-------------------------------+
                  | Supabase: finops_calls (cloud)|
                  +-------------------------------+
                                  |
                                  v
                  +-------------------------------+
                  | alexandrecaramaschi.com/finops|
                  | /api/finops/llm-usage         |
                  +-------------------------------+
```

---

## Schema único

```sql
CREATE TABLE llm_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,        -- ISO 8601 UTC
    project      TEXT    NOT NULL,        -- geo-orchestrator | papers | curso-factory | caramaschi
    run_id       TEXT,                    -- agrupa calls de uma execucao
    task_type    TEXT,                    -- research | writing | architecture | etc
    model_id     TEXT    NOT NULL,        -- claude-opus-4-6 | gpt-4o | gemini-2.5-pro | ...
    provider     TEXT    NOT NULL,        -- anthropic | openai | google | perplexity | groq
    tokens_in    INTEGER NOT NULL DEFAULT 0,
    tokens_out   INTEGER NOT NULL DEFAULT 0,
    cost_usd     REAL    NOT NULL DEFAULT 0,
    success      INTEGER NOT NULL DEFAULT 1,
    metadata     TEXT,                    -- JSON opcional
    sync_status  TEXT    NOT NULL DEFAULT 'pending',  -- pending | synced | error
    synced_at    TEXT
);

-- Dedup garantido
CREATE UNIQUE INDEX idx_llm_calls_dedup
    ON llm_calls(timestamp, project, COALESCE(run_id, ''), model_id);
```

---

## Uso (API Python)

```python
import sys
sys.path.insert(0, "C:/Sandyboxclaude/geo-finops")
from geo_finops import track_call

track_call(
    project="geo-orchestrator",
    model_id="claude-opus-4-6",
    tokens_in=500,
    tokens_out=200,
    cost_usd=0.022,
    run_id="20260407T180000",   # opcional, agrupa calls da mesma execucao
    task_type="architecture",   # opcional
    success=True,
)
```

O `provider` é inferido automaticamente do `model_id`.

---

## CLI

```bash
# Status geral
python -m geo_finops.cli status

# Agregacao por dimensao
python -m geo_finops.cli summary --by provider
python -m geo_finops.cli summary --by project
python -m geo_finops.cli summary --by model_id
python -m geo_finops.cli summary --by task_type
python -m geo_finops.cli summary --by provider --start 2026-04-01

# Listar calls
python -m geo_finops.cli list --project papers --limit 20
python -m geo_finops.cli list --provider anthropic

# Migrar historicos legados (1467 calls migradas em 2026-04-07)
python -m geo_finops.cli migrate

# Sync com Supabase
python -m geo_finops.sync --dry-run        # testa
python -m geo_finops.sync                  # roda sync real
```

---

## Adapters por projeto

Cada projeto tem um adapter thin que importa `geo_finops.track_call` via `sys.path` e expõe uma função `record(...)` compatível com a assinatura do tracker original. **No-op se `geo_finops` não estiver disponível** (compatibilidade total).

| Projeto | Adapter |
|---|---|
| geo-orchestrator | `src/unified_finops.py` (chamado em `cli.py:_execute_plan` ao lado do FinOps singleton) |
| papers | `src/finops/unified_adapter.py` |
| curso-factory | `src/unified_finops.py` |
| caramaschi | `src/scripts/ac_core/unified_finops.py` |

---

## Sync worker (Windows Task Scheduler)

Instalação:

```powershell
cd C:\Sandyboxclaude\geo-finops
powershell -ExecutionPolicy Bypass -File install_scheduler.ps1
```

Cria task `GeoFinOpsSync`:
- **Trigger**: diário às 23:50 local
- **Action**: `python -m geo_finops.sync`
- **Logs**: `~/.config/geo-finops/logs/sync.log`
- **Politicas**: `AllowStartIfOnBatteries`, `RunOnlyIfNetworkAvailable`, `ExecutionTimeLimit 15min`

Para desinstalar: `schtasks /Delete /TN GeoFinOpsSync /F`

### Idempotência

- **Local**: UNIQUE constraint em `(timestamp, project, run_id, model_id)` impede inserções duplicadas
- **Supabase**: `Prefer: resolution=merge-duplicates` + `on_conflict=timestamp,project,run_id,model_id` no upsert

---

## Snapshot para o site

```bash
python scripts/export_snapshot.py
# Gera: C:/Sandyboxclaude/landing-page-geo/public/finops-snapshot.json
```

O dashboard `alexandrecaramaschi.com/finops` consome esse snapshot via:
- **Endpoint Next.js**: `/api/finops/llm-usage` (ISR 1h, fallback automático se snapshot ausente)
- **Componente FinOpsDashboard**: client-side fetch com cache `no-store` para sempre buscar o mais recente

---

## Schema Supabase (criar uma vez)

```sql
CREATE TABLE IF NOT EXISTS finops_calls (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    project TEXT NOT NULL,
    run_id TEXT,
    task_type TEXT,
    model_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(10,6) NOT NULL DEFAULT 0,
    success BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB,
    local_id INTEGER,
    synced_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(timestamp, project, run_id, model_id)
);

CREATE INDEX idx_finops_calls_timestamp ON finops_calls(timestamp);
CREATE INDEX idx_finops_calls_project ON finops_calls(project);
CREATE INDEX idx_finops_calls_provider ON finops_calls(provider);
```

Cole no SQL Editor do Supabase: `https://supabase.com/dashboard/project/drzqkrqebvhcjotwhups/sql/new`

---

## Migração inicial (executada em 2026-04-07)

| Projeto | Calls migradas | Custo total | Fonte |
|---|---:|---:|---|
| geo-orchestrator | 1.189 | $254,17 | `output/execution_*.json` × 131 |
| papers | 257 | $0,14 | `data/papers.db::finops_usage` |
| curso-factory | 21 | $1,12 | `output/costs.json` |
| caramaschi | 0 | $0 | (sem tracker estruturado, instrumentar via adapter) |
| **TOTAL** | **1.467** | **$255,43** | |

Cross-check: bateu **exato** com soma dos `execution_*.json` ($254,17). O bug de dedup colapsando tasks com mesmo timestamp foi corrigido (run_id agora inclui task_id).

---

## Estrutura

```
geo-finops/
├── pyproject.toml
├── README.md                          (este arquivo)
├── install_scheduler.ps1              (Windows Task Scheduler)
├── geo_finops/
│   ├── __init__.py                    (API publica: track_call, query_calls)
│   ├── db.py                          (schema + connection WAL mode)
│   ├── tracker.py                     (track_call, query_calls, aggregate_by)
│   ├── migrate.py                     (migra historicos legados)
│   ├── sync.py                        (worker noturno -> Supabase)
│   └── cli.py                         (CLI status/summary/list/migrate/sync)
└── scripts/
    └── export_snapshot.py             (gera snapshot.json para o site)
```

---

## Próximos passos

1. **Criar tabela `finops_calls` no Supabase** (DDL acima) — habilita o sync worker
2. **Instalar Task Scheduler** (`install_scheduler.ps1`) — sync automático 23:50 diário
3. **Instrumentar caramaschi** com `record()` do adapter (ainda não tem tracker estruturado)
4. **Instrumentar callers órfãos do gpt-4o-mini** — `landing-page-geo/lib/geo-checker/llm-probes.ts`, `scripts/python/llm_utils.py`, `caramaschi/whatsapp_nlp_handler.py` — esses 505 calls que descobrimos via OpenAI admin key vão começar a aparecer no `calls.db`

---

## Licença

MIT
