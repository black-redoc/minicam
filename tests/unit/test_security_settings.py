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
