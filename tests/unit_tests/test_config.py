"""Unit tests for typed settings.

Settings parse once at the edge, the app only reads proven values.
"""

import pytest
from pydantic import ValidationError

from core.config import get_settings


def test_defaults_load_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_ENV", "LOG_LEVEL", "APP_HOST", "APP_PORT"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.app_host == "127.0.0.1"
    assert settings.app_port == 8000


def test_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("APP_PORT", "9000")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.app_env == "staging"
    assert settings.app_port == 9000


def test_bad_values_fail_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PORT", "99999")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        get_settings()
    get_settings.cache_clear()
