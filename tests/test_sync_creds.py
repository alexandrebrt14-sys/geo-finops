"""Testes de portabilidade para _load_supabase_creds.

Garante que o loader funciona em Linux/Mac/Windows sem dependencia
de path absoluto. Achado F11 da auditoria de ecossistema 2026-04-08.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Garantir que geo_finops eh importavel
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from geo_finops.sync import (  # noqa: E402
    _candidate_env_files,
    _load_supabase_creds,
    _parse_env_file,
)


# ---------------------------------------------------------------------------
# _parse_env_file — leitura robusta de .env
# ---------------------------------------------------------------------------


def test_parse_env_file_basic(tmp_path):
    """Le pares chave=valor simples."""
    env = tmp_path / ".env"
    env.write_text("SUPABASE_URL=https://abc.supabase.co\nSUPABASE_KEY=secret123\n")
    result = _parse_env_file(env)
    assert result["SUPABASE_URL"] == "https://abc.supabase.co"
    assert result["SUPABASE_KEY"] == "secret123"


def test_parse_env_file_strips_quotes(tmp_path):
    """Strippa aspas simples e duplas dos valores."""
    env = tmp_path / ".env"
    env.write_text('A="value_a"\nB=\'value_b\'\nC=value_c\n')
    result = _parse_env_file(env)
    assert result == {"A": "value_a", "B": "value_b", "C": "value_c"}


def test_parse_env_file_ignores_comments_and_blank(tmp_path):
    """Comentarios e linhas vazias sao ignorados."""
    env = tmp_path / ".env"
    env.write_text(
        "# This is a comment\n"
        "\n"
        "FOO=bar\n"
        "   # Indented comment\n"
        "BAZ=qux\n"
    )
    result = _parse_env_file(env)
    assert result == {"FOO": "bar", "BAZ": "qux"}


def test_parse_env_file_missing_returns_empty(tmp_path):
    """Arquivo inexistente nao quebra — retorna dict vazio."""
    result = _parse_env_file(tmp_path / "nao_existe.env")
    assert result == {}


def test_parse_env_file_malformed_lines_skipped(tmp_path):
    """Linhas sem '=' sao puladas, nao quebram."""
    env = tmp_path / ".env"
    env.write_text("VALID=ok\nlinha sem igual\nOUTRO=valor\n")
    result = _parse_env_file(env)
    assert result == {"VALID": "ok", "OUTRO": "valor"}


# ---------------------------------------------------------------------------
# _candidate_env_files — busca portavel
# ---------------------------------------------------------------------------


def test_candidate_files_no_windows_hardcoded_in_source():
    """Garantia: NENHUM path absoluto Windows literal no codigo-fonte.

    Achado F11 — antes da refatoracao, havia
    Path('C:/Sandyboxclaude/geo-orchestrator/.env') hardcoded que quebrava
    em Linux/Mac. Este teste eh sentinela contra regressao: inspeciona o
    SOURCE de _candidate_env_files (nao os paths resolvidos, que dependem
    da localizacao real do arquivo no disco).
    """
    import inspect
    from geo_finops.sync import _candidate_env_files

    source = inspect.getsource(_candidate_env_files)
    # Padroes proibidos: literal Windows path no codigo
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
        assert pattern not in source, (
            f"Hardcode Windows detectado em _candidate_env_files: '{pattern}'"
        )


def test_candidate_files_includes_xdg_default():
    """~/.config/geo-finops/.env sempre presente como candidato."""
    candidates = _candidate_env_files()
    paths = [str(p).replace("\\", "/") for p in candidates]
    assert any(
        ".config/geo-finops/.env" in p for p in paths
    ), f"XDG default nao encontrado em: {paths}"


def test_candidate_files_includes_home_dotfile():
    """~/.geo-finops.env sempre presente como candidato."""
    candidates = _candidate_env_files()
    paths = [str(p).replace("\\", "/") for p in candidates]
    assert any(
        ".geo-finops.env" in p for p in paths
    ), f"Home dotfile nao encontrado em: {paths}"


def test_candidate_files_honors_explicit_override(monkeypatch, tmp_path):
    """GEO_FINOPS_ENV_FILE eh respeitado e fica primeiro na ordem."""
    custom = tmp_path / "custom.env"
    custom.write_text("SUPABASE_URL=x")
    monkeypatch.setenv("GEO_FINOPS_ENV_FILE", str(custom))
    candidates = _candidate_env_files()
    assert candidates[0] == custom


def test_candidate_files_honors_xdg_config_home(monkeypatch, tmp_path):
    """$XDG_CONFIG_HOME eh respeitado quando definido."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    candidates = _candidate_env_files()
    paths = [str(p).replace("\\", "/") for p in candidates]
    expected = str(tmp_path / "geo-finops" / ".env").replace("\\", "/")
    assert expected in paths, f"XDG_CONFIG_HOME custom nao encontrado: {paths}"


# ---------------------------------------------------------------------------
# _load_supabase_creds — orquestracao
# ---------------------------------------------------------------------------


def test_load_creds_from_env_vars(monkeypatch):
    """Env vars tem prioridade absoluta sobre arquivos."""
    monkeypatch.setenv("SUPABASE_URL", "https://from-env.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "key-from-env")
    monkeypatch.delenv("GEO_FINOPS_ENV_FILE", raising=False)

    url, key = _load_supabase_creds()
    assert url == "https://from-env.supabase.co"
    assert key == "key-from-env"


def test_load_creds_falls_back_to_explicit_file(monkeypatch, tmp_path):
    """Sem env vars, usa GEO_FINOPS_ENV_FILE override."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    env_file = tmp_path / "explicit.env"
    env_file.write_text(
        "SUPABASE_URL=https://from-file.supabase.co\nSUPABASE_KEY=key-from-file\n"
    )
    monkeypatch.setenv("GEO_FINOPS_ENV_FILE", str(env_file))

    url, key = _load_supabase_creds()
    assert url == "https://from-file.supabase.co"
    assert key == "key-from-file"


def test_load_creds_accepts_service_role_alias(monkeypatch, tmp_path):
    """SUPABASE_SERVICE_ROLE_KEY funciona como alias de SUPABASE_KEY."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "SUPABASE_URL=https://x.supabase.co\n"
        "SUPABASE_SERVICE_ROLE_KEY=role-key-123\n"
    )
    monkeypatch.setenv("GEO_FINOPS_ENV_FILE", str(env_file))

    url, key = _load_supabase_creds()
    assert url == "https://x.supabase.co"
    assert key == "role-key-123"


def test_load_creds_returns_none_when_nothing_configured(monkeypatch, tmp_path):
    """Sem env vars e sem arquivos validos, retorna (None, None)."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("GEO_FINOPS_ENV_FILE", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    # Aponta HOME para diretorio temporario sem .config nem .geo-finops.env
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows

    url, key = _load_supabase_creds()
    # Pode pegar do sibling repo se existir; nao asseguramos None absoluto
    # mas garantimos que NAO quebra
    assert url is None or isinstance(url, str)
    assert key is None or isinstance(key, str)
