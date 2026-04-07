"""geo-finops — Tracking centralizado de uso de LLMs em todos os projetos Brasil GEO.

API publica:
    from geo_finops import track_call, query_calls, get_db_path

Schema unico em ~/.config/geo-finops/calls.db.
Sync diario para Supabase via worker noturno.
"""

from .tracker import track_call, query_calls, run_id_for_session
from .db import get_db_path, init_db, get_connection

__version__ = "1.0.0"
__all__ = [
    "track_call",
    "query_calls",
    "run_id_for_session",
    "get_db_path",
    "init_db",
    "get_connection",
]
