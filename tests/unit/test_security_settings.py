"""Tests for get_security_settings: required secret, and refusing to start
with the placeholder values shipped in .env.example."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.security_settings import get_security_settings

_REAL_SECRET = "a" * 64
_REAL_TEAM = "https://bold-hall-7812.cloudflareaccess.com"
_REAL_AUD = "521ed5e37227c253af62315c3bcfe501f5ae43bed0e9452f4399c3a83daaae8a"


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("MINICAM_JWT_SECRET", _REAL_SECRET)
    monkeypatch.delenv("MINICAM_CF_ACCESS_TEAM_DOMAIN", raising=False)
    monkeypatch.delenv("MINICAM_CF_ACCESS_AUD", raising=False)
    monkeypatch.delenv("MINICAM_CENTRAL_URL", raising=False)
    monkeypatch.delenv("MINICAM_DEVICE_TOKEN", raising=False)
    get_security_settings.cache_clear()
    yield
    get_security_settings.cache_clear()


def test_missing_secret_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINICAM_JWT_SECRET")
    with pytest.raises(RuntimeError, match="MINICAM_JWT_SECRET is not set"):
        get_security_settings()


@pytest.mark.parametrize(
    ("env_var", "placeholder"),
    [
        ("MINICAM_JWT_SECRET", "changeme-generate-a-random-64-char-hex-string"),
        ("MINICAM_CF_ACCESS_TEAM_DOMAIN", "https://myteam.cloudflareaccess.com"),
        ("MINICAM_CF_ACCESS_AUD", "changeme-aud-tag-from-access-application"),
    ],
)
def test_env_example_placeholders_refuse_to_start(
    monkeypatch: pytest.MonkeyPatch, env_var: str, placeholder: str
) -> None:
    monkeypatch.setenv(env_var, placeholder)
    with pytest.raises(RuntimeError, match=f"{env_var} still has the example value"):
        get_security_settings()


def test_empty_access_settings_mean_access_is_not_configured() -> None:
    settings = get_security_settings()
    assert settings.cf_access_team_domain is None
    assert settings.cf_access_aud is None


def test_real_access_settings_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINICAM_CF_ACCESS_TEAM_DOMAIN", _REAL_TEAM)
    monkeypatch.setenv("MINICAM_CF_ACCESS_AUD", _REAL_AUD)
    settings = get_security_settings()
    assert settings.cf_access_team_domain == _REAL_TEAM
    assert settings.cf_access_aud == _REAL_AUD


_TOKEN = "mcd_00000000-0000-4000-8000-000000000000_" + "s" * 43


def test_central_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """minicam-admin specs/011 AC5: both settings or neither, HTTPS only, and
    the device token never shows up in repr() (and so in logs)."""
    settings = get_security_settings()
    assert settings.central_url is None
    assert not settings.central_mode

    monkeypatch.setenv("MINICAM_CENTRAL_URL", "https://admin.example.com/")
    monkeypatch.setenv("MINICAM_DEVICE_TOKEN", _TOKEN)
    get_security_settings.cache_clear()
    settings = get_security_settings()
    assert settings.central_mode
    assert settings.central_url == "https://admin.example.com"
    assert settings.device_token == _TOKEN
    assert _TOKEN not in repr(settings)

    for url in ("http://localhost:5173", "http://127.0.0.1:8787"):
        monkeypatch.setenv("MINICAM_CENTRAL_URL", url)
        get_security_settings.cache_clear()
        assert get_security_settings().central_url == url


@pytest.mark.parametrize(
    ("url", "token", "message"),
    [
        ("https://admin.example.com", "", "must be set together"),
        ("", _TOKEN, "must be set together"),
        ("http://admin.example.com", _TOKEN, "must be an https:// URL"),
        ("ftp://admin.example.com", _TOKEN, "must be an https:// URL"),
        ("https://", _TOKEN, "must be an https:// URL"),
        ("admin.example.com", _TOKEN, "must be an https:// URL"),
    ],
)
def test_invalid_central_config_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch, url: str, token: str, message: str
) -> None:
    monkeypatch.setenv("MINICAM_CENTRAL_URL", url)
    monkeypatch.setenv("MINICAM_DEVICE_TOKEN", token)
    with pytest.raises(RuntimeError, match=message) as exc_info:
        get_security_settings()
    assert _TOKEN not in str(exc_info.value)
