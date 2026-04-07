# Changelog

Histórico de versões do `geo-finops`. Segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e [SemVer](https://semver.org/lang/pt-BR/).

## [1.1.0] — 2026-04-07

### Added
- **`scripts/health_check.py`** com 13 dimensões de validação ponta-a-ponta. Sai com código 0 se passou. Reproduzível.
- **`_normalize_timestamp()`** em `tracker.py` — garante que todos os caminhos de tracking (Python local, Next.js server-side, manual) gerem ISO 8601 UTC idêntico. Resolve falsas duplicatas por diferença de microsegundos entre callers.
- **Retry exponencial** em `sync.py::push_batch()` — 3 tentativas com backoff `1s/2.5s/5s` para erros 5xx e de rede. Erros 4xx fatais (`401/403/422`) não fazem retry.
- **Tratamento de HTTP 409 como sucesso** — quando todo o batch consiste de duplicatas, Postgres retorna `409 Conflict (23505)`. Antes, sync marcava como erro permanente; agora trata como idempotência funcionando.
- **`scripts/bootstrap_supabase.py`** — verifica conectividade Supabase e, se a tabela não existir, imprime o DDL pronto para colar no SQL editor.
- **`install_task.cmd`** — alternativa ao `install_scheduler.ps1` que usa `schtasks /Create` (não precisa de privilégios elevados).
- **`scripts/export_snapshot.py`** — gera `landing-page-geo/public/finops-snapshot.json` consumido pelo dashboard live.

### Fixed
- **Bug crítico de re-sync** ([commit 109dfe5](https://github.com/alexandrebrt14-sys/geo-finops/commit/109dfe5)): a sincronização noturna falharia silenciosamente em catch-ups. Causa raiz: 409 Conflict mal interpretado como erro fatal. Detectado durante double-check end-to-end com testes reais.
- **Bug de duplicatas falsas por timestamp**: o mesmo logical-call gravado por dois caminhos (ex: `llm_utils` Python local com `.898307` µs vs `/api/finops/track` Next.js sem µs) escapava do dedup porque os timestamps não batiam exatamente. Resolvido normalizando todos para ISO 8601 UTC com `datetime.fromisoformat().astimezone(timezone.utc).isoformat()`.

### Changed
- `Prefer: resolution=merge-duplicates,return=minimal` → `Prefer: resolution=ignore-duplicates,return=minimal`. PostgREST não aceita `on_conflict` com índices expressionais (que usam `COALESCE`). `ignore-duplicates` funciona com qualquer constraint UNIQUE.
- `_LLM_TO_PROVIDER` no `tracker.py` agora aceita 3 formatos: nome de provider, nome interno LLM_CONFIGS, e **model id real** (ex: `claude-opus-4-6`).

### Validated end-to-end
4 testes reais executados manualmente antes do release:
1. `query_openai()` real via `llm_utils.py` instrumentado → SQLite ✅
2. `client.messages.create()` real do Anthropic via caramaschi handler → SQLite ✅
3. `POST /api/finops/track` em produção (alexandrecaramaschi.com) → Supabase ✅
4. Re-sync idempotente forçado → 0 errors, 0 duplicatas ✅

Health check rodado **3 vezes consecutivas** após o fix: 13/13 passing em todas.

## [1.0.0] — 2026-04-07 (release inicial)

### Added
- **Pacote `geo_finops`** com schema único SQLite WAL em `~/.config/geo-finops/calls.db`
- **`tracker.py`**: API pública `track_call()`, `query_calls()`, `aggregate_by()`. Inferência automática de provider via `model_id`.
- **`db.py`**: Schema com `UNIQUE(timestamp, project, COALESCE(run_id,''), model_id)`. WAL mode + busy_timeout 10s.
- **`migrate.py`**: Migrações dos 4 trackers legados (orchestrator JSONL, papers SQLite, curso-factory JSON, caramaschi).
- **`sync.py`**: Worker que envia pending → Supabase `finops_calls` em batches de 500.
- **`cli.py`**: Comandos `status`, `summary --by {provider,project,model_id,task_type}`, `list`, `migrate`, `sync`.
- **`scripts/export_snapshot.py`**: Gera snapshot JSON consumido pelo dashboard `alexandrecaramaschi.com/finops`.
- **`install_scheduler.ps1`**: Registra Task Scheduler do Windows para sync diário 23:50.
- **4 adapters thin** em cada projeto: no-op se `geo_finops` indisponível, garante backwards compat total.

### Migração inicial
- 1.467 calls migradas de 4 trackers paralelos
- $254,17 (orchestrator) + $0,14 (papers) + $1,12 (curso-factory)
- Schema unificado: `(timestamp, project, run_id, task_type, model_id, provider, tokens_in, tokens_out, cost_usd, success, metadata, sync_status)`

### Background
A auditoria FinOps de 2026-04-07 detectou via OpenAI admin API **769 calls reais** vs **264 nos trackers locais conhecidos** = **505 calls órfãs**, 100% em `gpt-4o-mini`. O `geo-finops` foi criado para resolver definitivamente esse gap unificando todos os trackers e instrumentando os callers órfãos.

## Próximas versões planejadas

### [1.2.0] — Q2 2026 (planejado)
- BigQuery export opcional para análise histórica
- Dashboard web standalone (opcional, sem depender do landing-page-geo)
- Métricas Prometheus para Grafana
- Backup automático do `calls.db` para Supabase Storage semanal

### [2.0.0] — futuro
- Suporte a múltiplos workspaces (multi-tenant)
- Integração nativa com OpenTelemetry
- gRPC API alternativa ao SQLite local para contextos distribuídos
