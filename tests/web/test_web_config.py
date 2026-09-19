from __future__ import annotations

import pytest

from app.config import ConfigError
from app.web.config import load_web_config


def _set_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "123456789")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "fake-secret")
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://c3p0.example.com")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "fake-internal-token")


def test_missing_application_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="DISCORD_APPLICATION_ID"):
        load_web_config()


def test_missing_client_secret_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "123456789")

    with pytest.raises(ConfigError, match="DISCORD_CLIENT_SECRET"):
        load_web_config()


def test_missing_public_base_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "123456789")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "fake-secret")
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")

    with pytest.raises(ConfigError, match="PUBLIC_BASE_URL"):
        load_web_config()


def test_missing_internal_api_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "123456789")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "fake-secret")
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://c3p0.example.com")

    with pytest.raises(ConfigError, match="INTERNAL_API_TOKEN"):
        load_web_config()


def test_defaults_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)

    config = load_web_config()

    assert config.discord_client_id == 123456789
    assert config.discord_client_secret == "fake-secret"
    assert config.public_base_url == "https://c3p0.example.com"
    assert config.internal_api_token == "fake-internal-token"
    assert config.database_url == "sqlite+aiosqlite:////data/c3p0.db"
    assert config.web_host == "0.0.0.0"
    assert config.web_port == 8080
    assert config.bot_internal_base_url == "http://c3p0:8100"
    assert config.cookie_secure is True


def test_oauth_redirect_uri_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://c3p0.example.com/")

    config = load_web_config()

    assert config.oauth_redirect_uri == "https://c3p0.example.com/auth/callback"


def test_invalid_web_port_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("WEB_PORT", "not-a-number")

    with pytest.raises(ConfigError, match="WEB_PORT"):
        load_web_config()


def test_bot_internal_base_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("BOT_INTERNAL_BASE_URL", "http://localhost:8100")

    config = load_web_config()

    assert config.bot_internal_base_url == "http://localhost:8100"


def test_repr_redacts_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)

    config = load_web_config()

    assert "fake-secret" not in repr(config)
    assert "fake-token" not in repr(config)
    assert "fake-internal-token" not in repr(config)
    assert "redacted" in repr(config)
