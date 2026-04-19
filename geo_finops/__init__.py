"""geo-finops — Tracking centralizado de uso de LLMs em projetos Brasil GEO.

API publica (estavel):

    from geo_finops import track_call, query_calls, get_db_path
    from geo_finops import aggregate_by, run_id_for_session
    from geo_finops.config import load_supabase_creds, get_config_dir
    from geo_finops.aggregates import totals, top_models, daily_timeseries
    from geo_finops.digest import build_digest, format_markdown
    from geo_finops.prices import calculate_cost, get_price

Schema unico em ``~/.config/geo-finops/calls.db`` (respeita XDG e
``GEO_FINOPS_CONFIG_DIR`` / ``GEO_FINOPS_DB_PATH``). Sync diario para
Supabase via worker noturno.

Versao ``2.0.0`` (2026-04-19) reorganizou o pacote em camadas coesas.
Ver ``docs/REFACTOR-2026-04-19.md`` para detalhes da refatoracao.
"""

from .db import get_connection, get_db_path, init_db
from .tracker import aggregate_by, query_calls, run_id_for_session, track_call

__version__ = "2.0.0"
__all__ = [
    "aggregate_by",
    "get_connection",
    "get_db_path",
    "init_db",
    "query_calls",
    "run_id_for_session",
    "track_call",
]
