"""End-to-end: the exact journey a real operator follows to stand up
minicam — bootstrap the first admin via the CLI, log in, discover that
deny-by-default means even the admin can't stream until granted
permission, grant it from the admin page's backing API, and confirm a
stream token can then be issued.

Runs against the real app (main.app) and a real file-backed SQLite
database — see tests/e2e/conftest.py for why.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.cli.create_user import CreateUserArgs, create_user
from app.db.connection import get_connection
from app.repositories.user_repository import SQLiteUserRepository
from app.services.auth import PasswordHasher


async def test_full_admin_bootstrap_and_permission_grant_journey(
    e2e_client: TestClient,
) -> None:
    # 1. Bootstrap the first admin — exactly what `app.cli.create_user.main()`
    #    does, minus the subprocess/argv plumbing (already covered by
    #    tests/unit/test_cli_create_user.py).
    user_repository = SQLiteUserRepository(get_connection())
    admin = await create_user(
        CreateUserArgs(
            email="admin@example.com", password="supersecret123", is_admin=True
        ),
        user_repository,
        PasswordHasher(),
    )

    # 2. Log in as that admin.
    login_res = e2e_client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "supersecret123"},
    )
    assert login_res.status_code == 200
    assert "minicam_session" in login_res.cookies

    # 3. The dashboard shows the admin link, and /admin renders.
    dashboard_res = e2e_client.get("/")
    assert dashboard_res.status_code == 200
    assert 'href="/admin"' in dashboard_res.text

    admin_page_res = e2e_client.get("/admin")
    assert admin_page_res.status_code == 200

    # 4. /api/auth/users lists the admin (this is what admin.js renders).
    users_res = e2e_client.get("/api/auth/users")
    assert users_res.status_code == 200
    assert users_res.json() == [
        {"id": admin.id, "email": "admin@example.com", "is_admin": True}
    ]

    # 5. Being admin does NOT imply camera access — deny-by-default applies
    #    to everyone, including admins. This is the exact confusion a real
    #    operator hit: minting a stream token 404s before any permission
    #    has been granted.
    token_before_grant = e2e_client.post("/api/stream/office12/token")
    assert token_before_grant.status_code == 404

    # 6. Grant the permission through the same API the admin page's JS uses.
    grant_res = e2e_client.post(
        "/api/auth/permissions",
        json={
            "user_id": admin.id,
            "camera_name": "office12",
            "can_view": True,
            "can_use_ptz": True,
            "can_listen": False,
        },
    )
    assert grant_res.status_code == 200

    perms_res = e2e_client.get("/api/auth/permissions", params={"user_id": admin.id})
    assert perms_res.status_code == 200
    assert perms_res.json() == [
        {
            "user_id": admin.id,
            "camera_name": "office12",
            "can_view": True,
            "can_use_ptz": True,
            "can_listen": False,
        }
    ]

    # 7. Now minting a stream token succeeds.
    token_after_grant = e2e_client.post("/api/stream/office12/token")
    assert token_after_grant.status_code == 200
    assert token_after_grant.json()["token"]
