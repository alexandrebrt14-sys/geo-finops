"""Testes do novo modulo ``geo_finops.config``.

Cobre:

- ``get_config_dir`` respeita overrides ``GEO_FINOPS_CONFIG_DIR`` e ``XDG_CONFIG_HOME``
- ``get_db_path`` respeita override explicito de ``GEO_FINOPS_DB_PATH``
- ``parse_env_file`` tolerante a erros (missing, malformed, encoding)
- ``candidate_env_files`` sem hardcode Windows (sentinela contra regressao F11)
- ``load_env_chain`` mescla por precedencia correta
- ``load_supabase_creds`` com cascata env > override > XDG > home > sibling
- ``load_whatsapp_creds`` com fallback graceful
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from geo_finops import config  # noqa: E402

# ---------------------------------------------------------------------------
# get_config_dir / get_db_path / dirs auxiliares
# ---------------------------------------------------------------------------


def test_get_config_dir_respects_override(monkeypatch, tmp_path):
    target = tmp_path / "custom-config"
    monkeypatch.setenv("GEO_FINOPS_CONFIG_DIR", str(target))
    result = config.get_config_dir()
    assert result == target
    assert result.exists()


def test_get_config_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("GEO_FINOPS_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = config.get_config_dir()
    assert result == tmp_path / "geo-finops"


def test_get_config_dir_defaults_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("GEO_FINOPS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    result = config.get_config_dir()
    assert ".config" in str(result).replace("\\", "/")
    assert result.name == "geo-finops"


def test_get_db_path_override(monkeypatch, tmp_path):
    target = tmp_path / "nested" / "custom.db"
    monkeypatch.setenv("GEO_FINOPS_DB_PATH", str(target))
    result = config.get_db_path()
    assert result == target
    # Diretorio pai deve ter sido criado
    assert target.parent.exists()


def test_get_db_path_inside_config_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("GEO_FINOPS_DB_PATH", raising=False)
    monkeypatch.setenv("GEO_FINOPS_CONFIG_DIR", str(tmp_path))
    result = config.get_db_path()
    assert result == tmp_path / "calls.db"


def test_get_logs_and_digests_dirs_under_config(monkeypatch, tmp_path):
    monkeypatch.setenv("GEO_FINOPS_CONFIG_DIR", str(tmp_path))
    assert config.get_logs_dir() == tmp_path / "logs"
    assert config.get_digests_dir() == tmp_path / "digests"
    assert (tmp_path / "logs").exists()
    assert (tmp_path / "digests").exists()


# ---------------------------------------------------------------------------
# parse_env_file
# ---------------------------------------------------------------------------


def test_parse_env_file_basic(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\nB=2\n")
    assert config.parse_env_file(env) == {"A": "1", "B": "2"}


def test_parse_env_file_handles_unicode(tmp_path):
    env = tmp_path / ".env"
    env.write_text('NOME="Alexandre"\nCIDADE=São Paulo\n', encoding="utf-8")
    result = config.parse_env_file(env)
    assert result["NOME"] == "Alexandre"
    assert result["CIDADE"] == "São Paulo"


def test_parse_env_file_accepts_str_path(tmp_path):
    env = tmp_path / ".env"
    env.write_text("X=y\n")
    # Passar string em vez de Path
    assert config.parse_env_file(str(env)) == {"X": "y"}


def test_parse_env_file_missing_returns_empty(tmp_path):
    assert config.parse_env_file(tmp_path / "nope.env") == {}


def test_parse_env_file_malformed_skipped(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OK=1\nlinha sem igual\nOUTRO=ok2\n")
    assert config.parse_env_file(env) == {"OK": "1", "OUTRO": "ok2"}


# ---------------------------------------------------------------------------
# candidate_env_files (sentinela F11)
# ---------------------------------------------------------------------------


def test_candidate_env_files_no_windows_hardcoded():
    """Garantia F11: source do modulo nao pode ter Path('C:...') literal."""
    source = inspect.getsource(config.candidate_env_files)
    forbidden = [
        'Path("C:',
        "Path('C:",
        'Path("c:',
        "Path('c:",
        '"C:/Sandyboxclaude',
        "'C:/Sandyboxclaude",
        '"C:\\\\Sandyboxclaude',
    ]
    for pattern in forbidden:
        assert pattern not in source, f"Hardcode Windows detectado: '{pattern}'"


def test_candidate_env_files_includes_xdg_default():
    paths = [str(p).replace("\\", "/") for p in config.candidate_env_files()]
    assert any(".config/geo-finops/.env" in p for p in paths)


def test_candidate_env_files_includes_home_dotfile():
    paths = [str(p).replace("\\", "/") for p in config.candidate_env_files()]
    assert any(".geo-finops.env" in p for p in paths)


def test_candidate_env_files_explicit_override_first(monkeypatch, tmp_path):
    custom = tmp_path / "custom.env"
    custom.touch()
    monkeypatch.setenv("GEO_FINOPS_ENV_FILE", str(custom))
    assert config.candidate_env_files()[0] == custom


# ---------------------------------------------------------------------------
# load_env_chain + load_supabase_creds
# ---------------------------------------------------------------------------


def test_load_env_chain_merges_multiple_sources(monkeypatch, tmp_path):
    high_prio = tmp_path / "explicit.env"
    high_prio.write_text("A=high\nB=high\n")
    low_prio = tmp_path / "low.env"
    low_prio.write_text("B=low\nC=low\n")

    # explicit tem maior prioridade (primeiro em candidates)
    monkeypatch.setenv("GEO_FINOPS_ENV_FILE", str(high_prio))
    # XDG_CONFIG_HOME aponta pra um dir sem o arquivo; baseline nao interfere
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))

    merged = config.load_env_chain()
    # A so existe em high_prio
    assert merged.get("A") == "high"
    # B colidiu: high_prio vence
    assert merged.get("B") == "high"


def test_load_supabase_creds_prefers_env_vars(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://env.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "env-key")
    monkeypatch.delenv("GEO_FINOPS_ENV_FILE", raising=False)
    url, key = config.load_supabase_creds()
    assert url == "https://env.supabase.co"
    assert key == "env-key"


def test_load_supabase_creds_service_role_alias(monkeypatch, tmp_path):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text("SUPABASE_URL=https://x.supabase.co\nSUPABASE_SERVICE_ROLE_KEY=role-key\n")
    monkeypatch.setenv("GEO_FINOPS_ENV_FILE", str(env_file))
    url, key = config.load_supabase_creds()
    assert url == "https://x.supabase.co"
    assert key == "role-key"


def test_load_whatsapp_creds_from_env(monkeypatch):
    monkeypatch.setenv("WHATSAPP_API_TOKEN", "tok123")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "phone123")
    token, phone_id = config.load_whatsapp_creds()
    assert token == "tok123"
    assert phone_id == "phone123"


def test_load_whatsapp_creds_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("WHATSAPP_API_TOKEN", raising=False)
    monkeypatch.delenv("WHATSAPP_PHONE_ID", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("WHATSAPP_API_TOKEN=file-tok\nWHATSAPP_PHONE_ID=file-phone\n")
    monkeypatch.setenv("GEO_FINOPS_ENV_FILE", str(env_file))
    token, phone_id = config.load_whatsapp_creds()
    assert token == "file-tok"
    assert phone_id == "file-phone"


# ---------------------------------------------------------------------------
# Constantes configuraveis (parse correto de env vars)
# ---------------------------------------------------------------------------


def test_constants_are_floats():
    assert isinstance(config.FLY_USD_PER_MONTH, float)
    assert isinstance(config.VERCEL_USD_PER_MONTH, float)
    assert isinstance(config.ALERT_DELTA_PCT, float)
    assert config.FLY_USD_PER_MONTH > 0
    assert config.VERCEL_USD_PER_MONTH > 0


def test_get_snapshot_path_override(monkeypatch, tmp_path):
    target = tmp_path / "snap.json"
    monkeypatch.setenv("GEO_FINOPS_SNAPSHOT_PATH", str(target))
    assert config.get_snapshot_path() == target
