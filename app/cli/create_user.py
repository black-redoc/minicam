"""Bootstrap the first admin account.

There is no admin UI yet, and every admin-only endpoint requires an existing
admin to call it — so the very first account must be created out-of-band.

Usage:
    uv run python -m app.cli.create_user --email admin@example.com --admin
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
from dataclasses import dataclass

from app.db.connection import close_db, init_db
from app.models.domain import User
from app.repositories.user_repository import SQLiteUserRepository, UserRepository
from app.security_settings import get_security_settings
from app.services.auth import PasswordHasher


@dataclass(frozen=True, slots=True)
class CreateUserArgs:
    email: str
    password: str
    is_admin: bool


async def create_user(
    args: CreateUserArgs,
    user_repository: UserRepository,
    password_hasher: PasswordHasher,
) -> User:
    """Create a user, raising ValueError if the email is already taken."""
    if await user_repository.get_by_email(args.email) is not None:
        raise ValueError(f"A user with email '{args.email}' already exists")
    password_hash = password_hasher.hash(args.password)
    return await user_repository.create(args.email, password_hash, args.is_admin)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Login email for the new user")
    parser.add_argument(
        "--password",
        help="Plaintext password. Prompted interactively if omitted.",
    )
    parser.add_argument(
        "--admin", action="store_true", help="Grant admin privileges to the new user"
    )
    return parser.parse_args(argv)


async def _main(argv: list[str] | None = None) -> None:
    parsed = _parse_args(argv)
    password = parsed.password or getpass.getpass("Password: ")
    args = CreateUserArgs(email=parsed.email, password=password, is_admin=parsed.admin)

    settings = get_security_settings()
    conn = await init_db(settings.db_path)
    try:
        user_repository = SQLiteUserRepository(conn)
        user = await create_user(args, user_repository, PasswordHasher())
        print(f"Created user #{user.id} ({user.email}), admin={user.is_admin}")
    finally:
        await close_db()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
