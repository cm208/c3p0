from __future__ import annotations

import pytest

from app.config import ConfigError, load_config


def _set_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "fake-internal-token")


def test_missing_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="DISCORD_TOKEN"):
        load_config()


def test_missing_internal_api_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")

    with pytest.raises(ConfigError, match="INTERNAL_API_TOKEN"):
        load_config()


def test_defaults_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)

    config = load_config()

    assert config.discord_token == "fake-token"
    assert config.discord_application_id is None
    assert config.internal_api_token == "fake-internal-token"
    assert config.dev_guild_ids == []
    assert config.database_url == "sqlite+aiosqlite:////data/c3p0.db"
    assert config.metrics_host == "0.0.0.0"
    assert config.metrics_port == 8000
    assert config.internal_api_host == "0.0.0.0"
    assert config.internal_api_port == 8100
    assert config.log_level == "INFO"
    assert config.log_human is False
    assert config.default_prefix == "!"


def test_overrides_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "123456789")
    monkeypatch.setenv("DISCORD_DEV_GUILD_IDS", "111, 222,333")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:////tmp/test.db")
    monkeypatch.setenv("METRICS_PORT", "9000")
    monkeypatch.setenv("INTERNAL_API_HOST", "127.0.0.1")
    monkeypatch.setenv("INTERNAL_API_PORT", "9100")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("LOG_HUMAN", "true")
    monkeypatch.setenv("DEFAULT_PREFIX", "?")

    config = load_config()

    assert config.discord_application_id == 123456789
    assert config.dev_guild_ids == [111, 222, 333]
    assert config.database_url == "sqlite+aiosqlite:////tmp/test.db"
    assert config.metrics_port == 9000
    assert config.internal_api_host == "127.0.0.1"
    assert config.internal_api_port == 9100
    assert config.log_level == "DEBUG"
    assert config.log_human is True
    assert config.default_prefix == "?"


def test_invalid_dev_guild_ids_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("DISCORD_DEV_GUILD_IDS", "not-a-number")

    with pytest.raises(ConfigError, match="DISCORD_DEV_GUILD_IDS"):
        load_config()


def test_invalid_metrics_port_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("METRICS_PORT", "not-a-number")

    with pytest.raises(ConfigError, match="METRICS_PORT"):
        load_config()


def test_invalid_internal_api_port_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("INTERNAL_API_PORT", "not-a-number")

    with pytest.raises(ConfigError, match="INTERNAL_API_PORT"):
        load_config()


def test_token_repr_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "super-secret-token")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "super-secret-internal-token")

    config = load_config()

    assert "super-secret-token" not in repr(config)
    assert "super-secret-internal-token" not in repr(config)
    assert "redacted" in repr(config)
