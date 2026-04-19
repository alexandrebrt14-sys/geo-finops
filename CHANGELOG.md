# Changelog

Histórico de versões do `geo-finops`. Segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e [SemVer](https://semver.org/lang/pt-BR/).

## [2.0.0] — 2026-04-19

Refatoração completa em 7 ondas aplicando organização, eficácia e eficiência. **API pública estável** (sem quebra de `track_call`, `query_calls`, `get_db_path`, `aggregate_by`, `run_id_for_session`). Detalhes em [`docs/REFACTOR-2026-04-19.md`](docs/REFACTOR-2026-04-19.md).

### Adicionado

- Módulo `geo_finops.config` como fonte única para paths, parsing de `.env` e loading de credenciais (Supabase + WhatsApp).
- Módulo `geo_finops.aggregates` com queries SQL reusáveis (`totals`, `aggregate_by`, `top_models`, `daily_timeseries`, `top_hotspots`, `sync_status_counts`).
- Pacote `geo_finops.digest` (split do antigo `scripts/weekly_digest.py`) com 4 submódulos coesos: `cloud`, `builders`, `formatters`, `reporters`.
- Novas env vars: `GEO_FINOPS_CONFIG_DIR`, `GEO_FINOPS_DB_PATH`, `GEO_FINOPS_SNAPSHOT_PATH`, `GEO_FINOPS_ENV_FILE`, `GEO_FINOPS_FLY_USD_MONTH`, `GEO_FINOPS_VERCEL_USD_MONTH`, `GEO_FINOPS_ALERT_DELTA_PCT`, `GEO_FINOPS_WHATSAPP_OWNER`, `GEO_FINOPS_CARAMASCHI_URL`.
- Entry points no `pyproject.toml`: `geo-finops` e `geo-finops-sync`.
- Configurações completas de tooling: `[tool.pytest.ini_options]`, `[tool.coverage]`, `[tool.ruff]`, `[tool.mypy]`.
- `.pre-commit-config.yaml` padrão (pre-commit-hooks, ruff, mypy, bandit, gitleaks, pytest).
- 78 testes novos: `tests/test_config.py`, `tests/test_tracker.py`, `tests/test_aggregates.py`, `tests/test_digest.py` + `tests/conftest.py` com fixture `isolated_db`.
- Dependências opcionais declaradas: grupos `test` (pytest, pytest-cov) e `dev` (ruff, mypy, pre-commit, pyyaml).

### Modificado

- `geo_finops/db.py` agora delega resolução de path para `config.get_db_path`.
- `geo_finops/sync.py` consome credenciais via `config.load_supabase_creds`; helpers `_candidate_env_files`, `_parse_env_file`, `_load_supabase_creds` viraram re-exports (preservam os 14 testes existentes).
- `geo_finops/tracker.py`: `aggregate_by` delega para `aggregates.aggregate_by` (compat layer).
- `geo_finops/migrate.py` resolve raiz do workspace via `config.get_workspace_root` (no-op se workspace ausente, permite CI genérico).
- `geo_finops/cli.py` usa `aggregates.sync_status_counts` em `cmd_status`.
- Scripts refatorados para consumir `config` + `aggregates`: `bootstrap_supabase.py`, `export_snapshot.py`, `aggregate_dashboard.py`, `health_check.py`, `weekly_digest.py`.
- `scripts/weekly_digest.py` virou thin CLI (60 LOC); lógica vive em `geo_finops.digest`.
- `pyproject.toml` expandido (17 → ~100 linhas).
- `__version__` bumpado para `2.0.0`.

### Removido

- **Paths Windows hardcoded** em 4 scripts (`C:/Sandyboxclaude/...` que quebravam em Linux/Mac/Fly.io). Zero ocorrências restantes.
- **Duplicação de parsing de `.env`** em 4 locais (consolidada em `config.parse_env_file`).
- **Duplicação de SQL de agregação** em 3 locais (consolidada em `aggregates`).

### Fixo

- `scripts/weekly_digest.py` não depende mais de path absoluto para o `.env` do caramaschi: credenciais via `config.load_whatsapp_creds`.
- `scripts/health_check.py` não quebra em hosts sem workspace canônico: adapters e callers órfãos pulam graciosamente.

### Métricas

| | Antes (1.1) | Depois (2.0) |
|---|---:|---:|
| Testes | 57 | 149 |
| Cobertura | ~25% | 54% |
| LOC duplicadas | ~180 | 0 |
| Paths hardcoded | 4 | 0 |
| Health check E2E | 13/13 | 13/13 |

## [1.1.0] — 2026-04-08

Robustez + observabilidade (Wave F solo).

### Adicionado

- `geo_finops/prices.py` + `geo_finops/prices.yaml`: fonte única de preços LLM com 5 providers e 11 modelos. Funções `get_price`, `calculate_cost`, `list_providers`, `list_models`, `get_model_info`. 21 testes.
- `scripts/aggregate_dashboard.py`: dashboard HTML estático agregando 3 fontes. 12 testes.
- Alembic baseline com 10 testes de compatibilidade.
- Security scan workflow (bandit + pip-audit + gitleaks semanalmente).
- Pre-commit `secret_guard`.

### Modificado

- `geo_finops/sync.py`: `_load_supabase_creds` portável (F11) — busca em 6 fontes.
- 14 testes em `tests/test_sync_creds.py` cobrindo parser, candidates, loader.

## [1.0.0] — 2026-04-07

Release inicial — consolidação de tracking LLM.

### Adicionado

- Pacote `geo_finops` com `track_call`, `query_calls`, `aggregate_by`, `run_id_for_session`, `get_db_path`, `init_db`, `get_connection`.
- SQLite local WAL em `~/.config/geo-finops/calls.db` com UNIQUE constraint.
- Sync worker para Supabase com retry exponencial.
- HTTP 409 tratado como sucesso silencioso (idempotência).
- CLI com subcomandos `status`, `summary`, `list`, `migrate`, `sync`.
- Migration histórica de 4 trackers paralelos: 1.467 linhas.
- `scripts/health_check.py` com 13 dimensões.
- Task Scheduler `GeoFinOpsSync` diário às 23:50.
- 4 adapters thin nos projetos consumidores.
