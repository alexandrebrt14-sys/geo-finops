# geo-finops

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![Version](https://img.shields.io/badge/version-2.0.0-blue.svg)
![Status](https://img.shields.io/badge/status-production-green.svg)
![Tests](https://img.shields.io/badge/tests-149%20passing-success.svg)
![Coverage](https://img.shields.io/badge/coverage-54%25-yellow.svg)
![Health](https://img.shields.io/badge/health-13%2F13-success.svg)
![License](https://img.shields.io/badge/License-MIT-blue.svg)

**Tracking centralizado de uso de LLMs para todos os projetos do ecossistema Brasil GEO.**

Substitui 4 sistemas de tracking paralelos por um único SQLite local com sincronização diária para Supabase. Resolveu o problema das **505 calls órfãs** detectadas via OpenAI admin API que não eram rastreadas por nenhum dos sistemas anteriores.

**Estado atual** (validado por health check 13/13):

- 1.474 calls / $255,45 / 5 providers / 5 projetos
- Pipeline ponta-a-ponta operacional (SQLite local → Supabase → snapshot → endpoint live)
- 149 testes / 54% de cobertura
- 4 callers órfãos instrumentados
- Task Scheduler diário 23:50 ativo
- Re-sync idempotente validado (HTTP 409 tratado como sucesso)
- Live em <https://alexandrecaramaschi.com/finops>

> **Documentação adicional:**
>
> - [`docs/REFACTOR-2026-04-19.md`](docs/REFACTOR-2026-04-19.md) — refatoração v2.0 completa
> - [`CHANGELOG.md`](CHANGELOG.md) — histórico de versões
> - [`docs/LESSONS_LEARNED.md`](docs/LESSONS_LEARNED.md) — 9 bugs reais encontrados
> - [`scripts/health_check.py`](scripts/health_check.py) — 13 dimensões de validação

---

## Arquitetura

```
+-------------+   +-------------+   +--------------+   +------------+
| orchestrator|   |   papers    |   | curso-factory|   | caramaschi |
| (adapter)   |   | (adapter)   |   | (adapter)    |   | (adapter)  |
+------+------+   +------+------+   +------+-------+   +-----+------+
       |                 |                 |                 |
       +--------+--------+-----------------+-----------------+
                |
                v
    +-----------+------------+
    | geo_finops.track_call  |
    +-----------+------------+
                |
                v
    +----------------------------+
    | ~/.config/geo-finops/      |   <- XDG / GEO_FINOPS_CONFIG_DIR
    |   calls.db  (SQLite WAL)   |
    +----------------------------+
                |
        cron 23:50 sync
                |
                v
    +----------------------------+
    | Supabase: finops_calls     |
    +----------------------------+
                |
                v
    +----------------------------+
    | alexandrecaramaschi.com    |
    |   /finops                  |
    |   /api/finops/llm-usage    |
    +----------------------------+
```

---

## Estrutura do pacote (v2.0)

```
geo_finops/
├── __init__.py           # API publica estavel
├── config.py             # paths, env parsing, creds (fonte unica)
├── db.py                 # schema + connection WAL
├── tracker.py            # track_call + query_calls + provider infer
├── aggregates.py         # SQL reutilizavel (totals, daily, top_models)
├── sync.py               # worker noturno -> Supabase
├── migrate.py            # migra 4 trackers legados
├── cli.py                # status/summary/list/migrate/sync
├── prices.py             # calculo de custo (prices.yaml)
├── prices.yaml           # 5 providers, 11 modelos
├── digest/               # weekly FinOps digest
│   ├── cloud.py          # Fly + Vercel + GH Actions estimators
│   ├── builders.py       # build_digest + week_window
│   ├── formatters.py     # markdown / whatsapp / json
│   └── reporters.py      # save_snapshot + send_whatsapp
└── migrations/           # Alembic (schema evolution)

scripts/
├── health_check.py       # 13 dimensoes ponta-a-ponta
├── export_snapshot.py    # snapshot publico para landing-page-geo
├── aggregate_dashboard.py # HTML Chart.js estatico solo
├── weekly_digest.py      # CLI thin do pacote digest/
└── bootstrap_supabase.py # criacao inicial da tabela

tests/                    # 149 testes
├── conftest.py
├── test_config.py        (20)
├── test_tracker.py       (28)
├── test_aggregates.py    (14)
├── test_digest.py        (15)
├── test_sync_creds.py    (14)
├── test_prices.py        (21)
├── test_alembic_baseline.py (10)
└── test_aggregate_dashboard.py (12)
```

---

## API Python

### Tracking (principal)

```python
from geo_finops import track_call

track_call(
    project="geo-orchestrator",
    model_id="claude-opus-4-6",
    tokens_in=500,
    tokens_out=200,
    cost_usd=0.022,
    run_id="20260419T120000_abc123",   # agrupa calls da mesma execucao
    task_type="architecture",           # opcional
    success=True,
)
```

O `provider` é inferido automaticamente do `model_id`. O retorno é o `id` da linha inserida ou `None` (dedup hit / erro).

### Query + agregações

```python
from geo_finops import query_calls, aggregate_by

rows = query_calls(project="papers", limit=10)

# Ou diretamente via camada aggregates (mais expressiva):
from geo_finops.aggregates import totals, top_models, daily_timeseries

t = totals(start="2026-04-01T00:00:00+00:00")
# {'calls': 1234, 'cost_usd': 120.50, 'tokens_in': ..., 'period_start': ..., ...}

models = top_models(limit=10)  # [{'key': 'claude-opus-4-6', 'provider': 'anthropic', ...}]
daily = daily_timeseries(days=30)  # [{'date': '2026-04-01', 'calls': 42, 'cost_usd': 5.30}]
```

### Digest semanal

```python
from geo_finops.digest import build_digest, format_markdown, save_snapshot

d = build_digest(weeks_back=0)
print(format_markdown(d))
save_snapshot(d)  # ~/.config/geo-finops/digests/2026-W16.json
```

### Configuração

```python
from geo_finops.config import (
    get_config_dir, get_db_path, load_supabase_creds, load_whatsapp_creds
)

print(get_db_path())               # PosixPath('/home/.../calls.db') ou equivalente
url, key = load_supabase_creds()   # (str, str) ou (None, None)
```

---

## CLI

```bash
# Status geral
python -m geo_finops.cli status

# Agregações
python -m geo_finops.cli summary --by provider
python -m geo_finops.cli summary --by project
python -m geo_finops.cli summary --by provider --start 2026-04-01

# Listar calls
python -m geo_finops.cli list --project papers --limit 20
python -m geo_finops.cli list --provider anthropic

# Migrar históricos legados (idempotente)
python -m geo_finops.cli migrate

# Sync com Supabase
python -m geo_finops.cli sync --dry-run
python -m geo_finops.cli sync

# Entry points (se instalado via pip install -e .)
geo-finops status
geo-finops-sync
```

### Scripts (operação)

```bash
# Health check 13/13
python scripts/health_check.py

# Gerar snapshot para landing-page-geo
python scripts/export_snapshot.py

# Dashboard HTML agregado (solo)
python scripts/aggregate_dashboard.py --since 30

# Weekly digest (markdown/json/whatsapp)
python scripts/weekly_digest.py --format markdown
python scripts/weekly_digest.py --send-whatsapp

# Verificar tabela Supabase / criar se ausente
python scripts/bootstrap_supabase.py
```

---

## Environment variables

Todas opcionais — o pacote funciona com defaults XDG em qualquer host.

| Variável | Default | Propósito |
|---|---|---|
| `GEO_FINOPS_CONFIG_DIR` | `~/.config/geo-finops` | Diretório base do pacote |
| `GEO_FINOPS_DB_PATH` | `<config>/calls.db` | Path completo do SQLite |
| `GEO_FINOPS_ENV_FILE` | — | Override explícito de `.env` |
| `GEO_FINOPS_SNAPSHOT_PATH` | `<workspace>/landing-page-geo/public/finops-snapshot.json` | Destino do JSON público |
| `GEO_FINOPS_FLY_USD_MONTH` | `2.50` | Custo Fly.io mensal (rateio no digest) |
| `GEO_FINOPS_VERCEL_USD_MONTH` | `6.00` | Custo Vercel mensal |
| `GEO_FINOPS_ALERT_DELTA_PCT` | `30.0` | Limiar de alerta no digest |
| `GEO_FINOPS_WHATSAPP_OWNER` | `556298141505` | Destino default WhatsApp |
| `GEO_FINOPS_CARAMASCHI_URL` | `https://caramaschi.fly.dev/finops` | Endpoint probing |
| `SUPABASE_URL` + `SUPABASE_KEY` / `SUPABASE_SERVICE_ROLE_KEY` | — | Credenciais sync |
| `WHATSAPP_API_TOKEN` + `WHATSAPP_PHONE_ID` | — | Credenciais digest send |
| `XDG_CONFIG_HOME` | — | Override global XDG (Linux/Mac) |

Alternativamente, `SUPABASE_*` e `WHATSAPP_*` podem vir de qualquer `.env` no chain (ordem em `config.candidate_env_files()`).

---

## Schema SQLite (`llm_calls`)

```sql
CREATE TABLE llm_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,        -- ISO 8601 UTC
    project      TEXT    NOT NULL,        -- geo-orchestrator | papers | ...
    run_id       TEXT,                    -- agrupa calls de uma execucao
    task_type    TEXT,                    -- research | code | architecture | ...
    model_id     TEXT    NOT NULL,        -- claude-opus-4-6 | gpt-4o | ...
    provider     TEXT    NOT NULL,        -- anthropic | openai | google | ...
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

## Sync worker

**Windows**: `powershell -ExecutionPolicy Bypass -File install_scheduler.ps1` cria task `GeoFinOpsSync` diária às 23:50.

**Linux/Mac**: adicione ao crontab:

```cron
50 23 * * * /usr/bin/env python -m geo_finops.sync >> ~/.config/geo-finops/logs/sync.log 2>&1
```

### Idempotência

- **Local**: UNIQUE constraint em `(timestamp, project, run_id, model_id)` + `INSERT OR IGNORE`.
- **Supabase**: `Prefer: resolution=ignore-duplicates` no POST (PostgREST `ON CONFLICT DO NOTHING`).
- **HTTP 409**: tratado como sucesso silencioso (dedup funcionando).

### Retry

Até 3 tentativas com backoff exponencial (1s, 2.5s, 5s) em 5xx e erros de rede. 4xx fatais (401/403/422) não fazem retry.

---

## Desenvolvimento

```bash
# Install em modo editable com dev deps
pip install -e ".[dev]"

# Rodar testes
pytest
pytest --cov=geo_finops --cov-report=term-missing

# Lint + format
ruff check .
ruff format .

# Type check
mypy geo_finops

# Pre-commit (primeira vez)
pre-commit install
pre-commit run --all-files
```

Todos os 149 testes rodam em <10s localmente sem depender de Supabase/WhatsApp reais.

---

## Snapshot para o site

```bash
python scripts/export_snapshot.py
```

Gera `<workspace>/landing-page-geo/public/finops-snapshot.json` (path override via `GEO_FINOPS_SNAPSHOT_PATH`).

O dashboard `alexandrecaramaschi.com/finops` consome via `/api/finops/llm-usage` (ISR 1h, fallback se snapshot ausente).

---

## Runbook de troubleshooting

### Sync não envia para Supabase

1. `python scripts/health_check.py` — diagnóstico geral
2. Se "Supabase finops_calls" falhar: `python scripts/bootstrap_supabase.py`
3. Se "Sync status" mostrar `error > 0`:

   ```python
   python -c "from geo_finops.db import get_connection; c=get_connection(); print(c.execute(\"UPDATE llm_calls SET sync_status='pending' WHERE sync_status='error'\").rowcount)"
   ```

4. Rodar manualmente: `python -m geo_finops.sync`

### Task Scheduler não roda (Windows)

```powershell
schtasks /Query /TN GeoFinOpsSync /V /FO LIST
schtasks /Delete /TN GeoFinOpsSync /F
powershell -NoProfile -Command "schtasks /Create /TN 'GeoFinOpsSync' /TR 'python -m geo_finops.sync' /SC DAILY /ST 23:50 /F"
schtasks /Run /TN GeoFinOpsSync
```

### Endpoint live retorna dados velhos

Snapshot é estático:

```bash
python scripts/export_snapshot.py
cd ../landing-page-geo && git add public/finops-snapshot.json
git commit -m "chore(finops): refresh snapshot [skip build]" && git push
```

### Instrumentar novo projeto

1. Adicionar adapter thin em `<projeto>/src/unified_finops.py`:

   ```python
   import sys
   sys.path.insert(0, "<path>/geo-finops")
   try:
       from geo_finops import track_call
   except ImportError:
       def track_call(**_): return None

   def record(**kwargs):
       return track_call(project="<seu-projeto>", **kwargs)
   ```

2. Chamar `record(model_id=..., tokens_in=..., ...)` após cada call LLM
3. `python scripts/health_check.py` para validar

---

## Adapters por projeto

Cada projeto tem um adapter thin que importa `geo_finops.track_call` via `sys.path`. **No-op se `geo_finops` não disponível** (compatibilidade total — tracking jamais quebra a call principal).

| Projeto | Adapter |
|---|---|
| geo-orchestrator | `src/unified_finops.py` |
| papers | `src/finops/unified_adapter.py` |
| curso-factory | `src/unified_finops.py` |
| caramaschi | `src/scripts/ac_core/unified_finops.py` |

---

## Migração inicial (executada em 2026-04-07)

| Projeto | Calls | Custo | Fonte |
|---|---:|---:|---|
| geo-orchestrator | 1.189 | $254,17 | `output/execution_*.json` × 131 |
| papers | 257 | $0,14 | `data/papers.db::finops_usage` |
| curso-factory | 21 | $1,12 | `output/costs.json` |
| caramaschi | 0 | $0 | (sem tracker estruturado) |
| **Total** | **1.467** | **$255,43** | |

O bug de dedup colapsando tasks com mesmo timestamp foi corrigido incluindo `task_id` no `run_id` sintético.

---

## Licença

MIT
