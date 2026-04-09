# geo-finops — Instruções Claude Code

## 2026-04-09 — Wave F (Onda 3 versão solo)

### B-020: `prices.yaml` fonte única de preços LLM
- **Commit:** `d19c61e` — `feat(finops): prices.yaml fonte unica + dashboard agregado solo`
- **Arquivos:** `geo_finops/prices.yaml` + `geo_finops/prices.py`
- **API:** `get_price(provider, model)`, `calculate_cost(provider, model, tin, tout)`, `list_providers()`, `list_models()`, `get_model_info()`
- **5 providers, 11 modelos** (Anthropic Opus/Sonnet/Haiku, OpenAI GPT-4o/mini, Google Gemini Pro/Flash, Perplexity Sonar Pro/básico, Groq Llama 3.3/3.1)
- **Versionado** por campo `version` no YAML
- **Sentinela contra regressão histórica:** Perplexity sonar-pro NUNCA pode voltar a $0.001 (incidente Sprint 5)
- **21 testes**

### B-022: Dashboard agregado HTML solo
- **Commit:** `d19c61e` (junto com B-020)
- **Arquivo:** `scripts/aggregate_dashboard.py`
- Consolida 3 fontes: `calls.db` local + `geo-orchestrator/.kpi_history.jsonl` + `caramaschi.fly.dev/finops`
- HTML estático Chart.js inline em `~/.cache/geo-dashboard.html`
- **CLI:** `python scripts/aggregate_dashboard.py [--since 30] [--output PATH]`
- **12 testes**

### B-014: Alembic baseline (Wave E)
- **Commit:** `48146bb` — `feat(db): setup Alembic com baseline`
- **Setup minimal sem ORM SQLAlchemy** — usa `op.execute(SQL raw)` para preservar padrão existente
- **Workflow:** `alembic revision -m "msg"` → editar SQL → `alembic upgrade head`
- **Para DB existente:** `alembic stamp 0001_baseline` (sem reaplicar SQL)
- **10 testes**

### B-025: security-scan workflow
- bandit + pip-audit + gitleaks semanalmente

## Propósito

Tracking centralizado de uso de LLMs em todos os projetos do ecossistema Brasil GEO. SQLite local em `~/.config/geo-finops/calls.db` + sync diário Supabase. Substituiu 4 trackers paralelos. 1467 calls migradas historicamente.

## 2026-04-09 — Mudanças da auditoria de ecossistema

### 1. `_load_supabase_creds` portátil (F11)
- **Commit:** `8dd13c5` — `fix(sync): _load_supabase_creds portavel sem path Windows hardcoded`
- **Antes:** `Path("C:/Sandyboxclaude/geo-orchestrator/.env")` hardcoded como fallback. Quebrava em Linux/Mac/Fly.io e em qualquer host com layout diferente.
- **Depois:** busca dinâmica em 6 fontes ordenadas:
  1. Env vars `SUPABASE_URL` + `SUPABASE_KEY` (ou `SUPABASE_SERVICE_ROLE_KEY`)
  2. `GEO_FINOPS_ENV_FILE` (override explícito)
  3. `$XDG_CONFIG_HOME/geo-finops/.env`
  4. `~/.config/geo-finops/.env` (XDG default Linux/Mac)
  5. `~/.geo-finops.env` (home dotfile)
  6. `<parent>/geo-orchestrator/.env` (sibling repo, calculado via `Path(__file__).resolve().parents[2]` — portável)
- **Refatoração em 3 funções coesas:**
  - `_candidate_env_files()` — lista ordenada de candidatos
  - `_parse_env_file()` — leitura robusta com tratamento de erro/encoding/comentários/aspas
  - `_load_supabase_creds()` — orquestração

### 2. Tests novos — primeiro módulo de testes do repo (parcialmente atende F20)
- **Arquivo:** `tests/test_sync_creds.py` (novo)
- **Cobertura:** 14 testes — parser de .env (5), busca de candidatos (5), orquestração de loader (4)
- **Sentinela contra regressão:** `test_candidate_files_no_windows_hardcoded_in_source` inspeciona `inspect.getsource(_candidate_env_files)` e bloqueia literais `Path("C:'`. Detecta o padrão no código-fonte, não no path resolvido.
- **Próxima evolução (Onda 2):** expandir para `db.py`, `tracker.py`, `migrate.py`. Falta também Alembic para schema migrations (B-014).

### 3. Pre-commit secret_guard (F44)
- **Commit:** `0f6e415` — `sec(precommit): instala secret_guard`
- **Arquivos:** `.tools/secret_guard.py`, `.githooks/pre-commit`
- **Já ativado** localmente

## API pública

Importável de `geo_finops`:

```python
from geo_finops import (
    track_call,           # registra uma chamada LLM
    query_calls,          # query SQL no SQLite local
    run_id_for_session,   # gera run_id consistente para uma sessão
    get_db_path,          # path do calls.db
    init_db,              # inicializa schema
    get_connection,       # conexão SQLite WAL
)
```

## Schema SQLite (`llm_calls`)

14 colunas, dedup expressional via `UNIQUE(timestamp, project, COALESCE(run_id, ''), model_id)`. WAL mode + busy_timeout 10s para concorrência multi-processo.

## Sync Supabase

`python -m geo_finops.sync` — POST batches de até 500 rows para `/rest/v1/finops_calls` com `Prefer: resolution=ignore-duplicates`. Retry exponencial 3 tentativas. HTTP 409 tratado como sucesso silencioso (idempotência).

Task Scheduler Windows registra `GeoFinOpsSync` daily 23:50 via `install_scheduler.ps1`.

## Adapter pattern para consumers

Cada projeto consumidor importa `geo_finops` via thin adapter (`unified_finops.py`) com fire-and-forget gracioso (no-op se import falhar). Zero quebra reversa.

## Próximos passos planejados

- B-014 (Onda 2): Alembic para schema migrations
- B-020 (Onda 3): centralizar cálculo de custo aqui (deixar de ser responsabilidade dos consumers) com `prices.yaml` versionado

## API keys

Lidos de variáveis de ambiente OU do `.env` do orchestrator (sibling repo). NUNCA commitar `.env` — pre-commit hook bloqueia automaticamente desde 2026-04-09.

## Sem Emojis

Proibido emojis em qualquer conteúdo, output ou documentação.
