"""Configuracao centralizada: paths, parsing de .env, loading de credenciais.

Este modulo concentra TODO o acesso ao filesystem e env vars do pacote.
Antes da refatoracao 2026-04-19, a logica estava duplicada em 4 lugares:

    sync.py                 _parse_env_file + _load_supabase_creds
    bootstrap_supabase.py   _load_creds (versao simplificada)
    weekly_digest.py        inline parser em send_whatsapp
    health_check.py         inline parser em check_supabase

A duplicacao causava tres problemas:

1. **Inconsistencia**: cada copia tratava comentarios/aspas de forma
   ligeiramente diferente, gerando bugs dificeis de diagnosticar.
2. **Paths Windows hardcoded**: 4 scripts ainda referenciavam
   ``C:/Sandyboxclaude/.../.env``, quebrando em Linux/Mac/Fly.io.
3. **Testes**: so o sync tinha testes; os scripts nao.

Agora todos os consumidores importam daqui. Regra:

    from geo_finops.config import load_supabase_creds, get_db_path

Os helpers privados que ainda existem em `sync.py` (``_load_supabase_creds``,
``_candidate_env_files``, ``_parse_env_file``) sao apenas re-exports para
preservar compatibilidade com os testes existentes.

API publica (estavel):

- ``get_config_dir()``         → ``~/.config/geo-finops`` (respeita XDG)
- ``get_db_path()``            → calls.db sob o config_dir
- ``get_logs_dir()``            → logs/ sob o config_dir
- ``get_digests_dir()``        → digests/ sob o config_dir
- ``candidate_env_files()``    → lista ordenada de .env candidates
- ``parse_env_file(path)``     → dict robusto (comentarios/aspas/erros)
- ``load_env_chain()``         → merge de todos os .env encontrados
- ``load_supabase_creds()``    → (url, key) ou (None, None)
- ``load_whatsapp_creds()``    → (token, phone_id) ou (None, None)

Todas as funcoes sao puras em termos de side effects: nao escrevem logs
no stdout, apenas via ``logging``. Erros de IO sao silenciados com warning.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Diretorios base (respeitam XDG Base Directory Specification)
# ---------------------------------------------------------------------------


def get_config_dir() -> Path:
    """Retorna o diretorio de config de geo-finops (cria se nao existir).

    Ordem de resolucao:
    1. ``GEO_FINOPS_CONFIG_DIR`` (override explicito)
    2. ``$XDG_CONFIG_HOME/geo-finops`` (Linux/Mac com XDG)
    3. ``~/.config/geo-finops`` (default universal)
    """
    override = os.environ.get("GEO_FINOPS_CONFIG_DIR")
    if override:
        p = Path(override).expanduser()
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
        p = base / "geo-finops"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_db_path() -> Path:
    """Retorna o path absoluto do calls.db (cria diretorio se nao existir).

    Override: ``GEO_FINOPS_DB_PATH`` (tem precedencia sobre ``GEO_FINOPS_CONFIG_DIR``).
    """
    override = os.environ.get("GEO_FINOPS_DB_PATH")
    if override:
        p = Path(override).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return get_config_dir() / "calls.db"


def get_logs_dir() -> Path:
    p = get_config_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_digests_dir() -> Path:
    p = get_config_dir() / "digests"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_project_root() -> Path:
    """Raiz do repositorio geo-finops (dois niveis acima deste arquivo)."""
    return Path(__file__).resolve().parents[1]


def get_workspace_root() -> Path | None:
    """Raiz do workspace de projetos (pai do repo geo-finops).

    Se o repo estiver no layout canonico ``<workspace>/geo-finops``,
    retorna ``<workspace>``. Senao retorna None.
    """
    parent = Path(__file__).resolve().parents[2]
    if not parent.exists():
        return None
    return parent


# ---------------------------------------------------------------------------
# Parsing de .env (robusto, tolerante a erros)
# ---------------------------------------------------------------------------


def parse_env_file(path: Path | str) -> dict[str, str]:
    """Le um arquivo .env e retorna dict ``{chave: valor}``.

    Robustez:
    - Ignora linhas vazias e comentarios (``#``).
    - Ignora linhas malformadas (sem ``=``).
    - Strippa aspas simples e duplas do valor.
    - Tolera IOError/UnicodeDecodeError (loga warning, retorna parcial).

    Args:
        path: Path ou str para o arquivo.

    Returns:
        Dict com as variaveis. Vazio se arquivo nao existe ou ilegivel.
    """
    p = Path(path)
    result: dict[str, str] = {}
    try:
        if not p.exists():
            return result
    except OSError:
        return result

    try:
        content = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("nao foi possivel ler %s: %s", p, exc)
        return result

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


# ---------------------------------------------------------------------------
# Candidate .env files (busca portavel, sem paths Windows hardcoded)
# ---------------------------------------------------------------------------


def candidate_env_files() -> list[Path]:
    """Lista ordenada de candidatos a arquivo .env.

    Ordem (primeira fonte com as chaves desejadas vence):

    1. ``GEO_FINOPS_ENV_FILE`` (override explicito, sempre primeiro)
    2. ``$XDG_CONFIG_HOME/geo-finops/.env`` (Linux/Mac com XDG)
    3. ``~/.config/geo-finops/.env`` (XDG default universal)
    4. ``~/.geo-finops.env`` (home dotfile alternativo)
    5. ``<workspace>/geo-orchestrator/.env`` (sibling repo no layout canonico)

    NENHUM path absoluto Windows hardcoded. Funciona em qualquer host
    desde que pelo menos uma fonte exista.
    """
    candidates: list[Path] = []

    explicit = os.environ.get("GEO_FINOPS_ENV_FILE")
    if explicit:
        candidates.append(Path(explicit))

    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        candidates.append(Path(xdg_config) / "geo-finops" / ".env")

    candidates.append(Path.home() / ".config" / "geo-finops" / ".env")
    candidates.append(Path.home() / ".geo-finops.env")

    workspace = get_workspace_root()
    if workspace:
        candidates.append(workspace / "geo-orchestrator" / ".env")

    return candidates


def load_env_chain() -> dict[str, str]:
    """Mescla todas as .env encontradas, de menor para maior prioridade.

    Itera os candidatos em ordem REVERSA (sibling -> home -> XDG -> override)
    e sobrepoe, de forma que a fonte mais especifica vence.
    """
    merged: dict[str, str] = {}
    for path in reversed(candidate_env_files()):
        parsed = parse_env_file(path)
        if parsed:
            merged.update(parsed)
    return merged


# ---------------------------------------------------------------------------
# Credenciais (Supabase, WhatsApp)
# ---------------------------------------------------------------------------


def load_supabase_creds() -> tuple[str | None, str | None]:
    """Retorna ``(SUPABASE_URL, SUPABASE_KEY)`` com fallback em cascata.

    Prioridade:
    1. Env vars (``SUPABASE_URL`` + ``SUPABASE_KEY`` ou ``SUPABASE_SERVICE_ROLE_KEY``)
    2. Arquivos .env em ``candidate_env_files()`` na ordem declarada

    Atualiza ``url``/``key`` parcialmente se uma fonte so tiver uma das
    chaves (permite, por exemplo, URL em env var + KEY em arquivo).

    Returns:
        Tupla (url, key). Cada valor pode ser None se nao encontrado.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if url and key:
        logger.debug("supabase creds: env vars")
        return url, key

    for env_file in candidate_env_files():
        parsed = parse_env_file(env_file)
        if not parsed:
            continue
        candidate_url = parsed.get("SUPABASE_URL") or url
        candidate_key = parsed.get("SUPABASE_KEY") or parsed.get("SUPABASE_SERVICE_ROLE_KEY") or key
        if candidate_url and candidate_key:
            logger.debug("supabase creds: %s", env_file)
            return candidate_url, candidate_key
        # Atualiza parciais (permite compor chaves de fontes diferentes)
        url = candidate_url
        key = candidate_key

    return url, key


def load_whatsapp_creds() -> tuple[str | None, str | None]:
    """Retorna ``(WHATSAPP_API_TOKEN, WHATSAPP_PHONE_ID)`` em cascata.

    Mesmo padrao de cascata de ``load_supabase_creds``. O caramaschi
    historicamente mantinha o ``.env`` em ``caramaschi/src/scripts/.env``;
    essa fonte agora entra no chain apenas se o workspace canonico existir.
    """
    token = os.environ.get("WHATSAPP_API_TOKEN")
    phone_id = os.environ.get("WHATSAPP_PHONE_ID")
    if token and phone_id:
        return token, phone_id

    # Fontes gerais
    for env_file in candidate_env_files():
        parsed = parse_env_file(env_file)
        if parsed:
            token = token or parsed.get("WHATSAPP_API_TOKEN")
            phone_id = phone_id or parsed.get("WHATSAPP_PHONE_ID")
            if token and phone_id:
                return token, phone_id

    # Fonte especifica do caramaschi (opcional)
    workspace = get_workspace_root()
    if workspace:
        caramaschi_env = workspace / "caramaschi" / "src" / "scripts" / ".env"
        parsed = parse_env_file(caramaschi_env)
        if parsed:
            token = token or parsed.get("WHATSAPP_API_TOKEN")
            phone_id = phone_id or parsed.get("WHATSAPP_PHONE_ID")

    return token, phone_id


# ---------------------------------------------------------------------------
# Constantes de projeto (configuraveis via env var)
# ---------------------------------------------------------------------------

# Custo cloud estimado (rateio semanal usado no weekly_digest).
# Overrides: GEO_FINOPS_FLY_USD_MONTH, GEO_FINOPS_VERCEL_USD_MONTH.
FLY_USD_PER_MONTH: float = float(os.environ.get("GEO_FINOPS_FLY_USD_MONTH", "2.50"))
VERCEL_USD_PER_MONTH: float = float(os.environ.get("GEO_FINOPS_VERCEL_USD_MONTH", "6.00"))

# Limiar de alerta no digest (pct sobre semana anterior).
ALERT_DELTA_PCT: float = float(os.environ.get("GEO_FINOPS_ALERT_DELTA_PCT", "30.0"))

# WhatsApp destino padrao (numero no formato Graph API, sem '+').
DEFAULT_WHATSAPP_OWNER: str = os.environ.get("GEO_FINOPS_WHATSAPP_OWNER", "556298141505")

# Endpoint caramaschi /finops (probing opcional do dashboard).
CARAMASCHI_FINOPS_URL: str = os.environ.get(
    "GEO_FINOPS_CARAMASCHI_URL", "https://caramaschi.fly.dev/finops"
)


# Output padrao do snapshot (onde a landing-page-geo le). Pode ser override
# via GEO_FINOPS_SNAPSHOT_PATH; senao deriva do workspace canonico.
def get_snapshot_path() -> Path | None:
    """Retorna o path onde ``export_snapshot`` deve escrever o JSON publico.

    Ordem:
    1. ``GEO_FINOPS_SNAPSHOT_PATH`` (override explicito)
    2. ``<workspace>/landing-page-geo/public/finops-snapshot.json`` (canonico)
    3. None (o caller pode fallback para stdout)
    """
    override = os.environ.get("GEO_FINOPS_SNAPSHOT_PATH")
    if override:
        return Path(override).expanduser()
    workspace = get_workspace_root()
    if workspace:
        candidate = workspace / "landing-page-geo" / "public" / "finops-snapshot.json"
        return candidate
    return None
