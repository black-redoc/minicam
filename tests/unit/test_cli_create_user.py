"""Tests for app.cli.create_user."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.cli.create_user import CreateUserArgs, _main, _parse_args, create_user
from app.models.domain import User
from app.services.auth import PasswordHasher


class TestParseArgs:
    def test_parses_email_and_admin_flag(self) -> None:
        args = _parse_args(["--email", "a@example.com", "--password", "x", "--admin"])
        assert args.email == "a@example.com"
        assert args.password == "x"
        assert args.admin is True

    def test_admin_defaults_to_false(self) -> None:
        args = _parse_args(["--email", "a@example.com", "--password", "x"])
        assert args.admin is False


class TestCreateUser:
    async def test_raises_value_error_when_email_taken(self) -> None:
        user_repository = AsyncMock()
        user_repository.get_by_email.return_value = User(
            id=1,
            email="dup@example.com",
            password_hash="h",
            is_admin=False,
            is_active=True,
            created_at="t",
            updated_at="t",
        )
        args = CreateUserArgs(email="dup@example.com", password="x", is_admin=False)
        with pytest.raises(ValueError, match="already exists"):
            await create_user(args, user_repository, PasswordHasher())

    async def test_creates_and_hashes_password(self) -> None:
        user_repository = AsyncMock()
        user_repository.get_by_email.return_value = None
        created = User(
            id=1,
            email="new@example.com",
            password_hash="hashed",
            is_admin=True,
            is_active=True,
            created_at="t",
            updated_at="t",
        )
        user_repository.create.return_value = created
        args = CreateUserArgs(email="new@example.com", password="plain", is_admin=True)

        result = await create_user(args, user_repository, PasswordHasher())

        assert result is created
        call_args = user_repository.create.call_args.args
        assert call_args[0] == "new@example.com"
        assert call_args[1] != "plain"  # never stores the plaintext password
        assert call_args[2] is True


class TestMain:
    async def test_creates_user_using_provided_password(self) -> None:
        created = User(
            id=1,
            email="cli@example.com",
            password_hash="hashed",
            is_admin=False,
            is_active=True,
            created_at="t",
            updated_at="t",
        )
        fake_conn = object()
        with (
            patch("app.cli.create_user.init_db", AsyncMock(return_value=fake_conn)),
            patch("app.cli.create_user.close_db", AsyncMock()) as mock_close,
            patch("app.cli.create_user.SQLiteUserRepository") as mock_repo_cls,
        ):
            mock_repo_cls.return_value.get_by_email = AsyncMock(return_value=None)
            mock_repo_cls.return_value.create = AsyncMock(return_value=created)
            await _main(["--email", "cli@example.com", "--password", "cli-pass"])

        mock_repo_cls.return_value.create.assert_called_once()
        mock_close.assert_called_once()

    async def test_prompts_for_password_when_not_provided(self) -> None:
        created = User(
            id=1,
            email="prompted@example.com",
            password_hash="hashed",
            is_admin=False,
            is_active=True,
            created_at="t",
            updated_at="t",
        )
        fake_conn = object()
        with (
            patch("app.cli.create_user.init_db", AsyncMock(return_value=fake_conn)),
            patch("app.cli.create_user.close_db", AsyncMock()),
            patch(
                "app.cli.create_user.getpass.getpass", return_value="typed-pass"
            ) as mock_getpass,
            patch("app.cli.create_user.SQLiteUserRepository") as mock_repo_cls,
        ):
            mock_repo_cls.return_value.get_by_email = AsyncMock(return_value=None)
            mock_repo_cls.return_value.create = AsyncMock(return_value=created)
            await _main(["--email", "prompted@example.com"])

        mock_getpass.assert_called_once()
