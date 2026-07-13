"""End-to-end auth flow against a real SQLite database: login, me, logout,
and admin-only user/permission management.
"""

from __future__ import annotations

import aiosqlite
from fastapi.testclient import TestClient

from app.repositories.user_repository import SQLiteUserRepository
from app.services.auth import PasswordHasher


async def _create_user(
    db_connection: aiosqlite.Connection,
    email: str,
    password: str,
    is_admin: bool = False,
) -> int:
    repo = SQLiteUserRepository(db_connection)
    user = await repo.create(email, PasswordHasher().hash(password), is_admin)
    return user.id


async def test_login_sets_cookie_and_returns_user(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _create_user(db_connection, "user@example.com", "correct-password")

    res = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "correct-password"},
    )

    assert res.status_code == 200
    assert "minicam_session" in res.cookies
    assert res.json()["email"] == "user@example.com"


async def test_login_rejects_wrong_password(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _create_user(db_connection, "user2@example.com", "correct-password")

    res = client.post(
        "/api/auth/login", json={"email": "user2@example.com", "password": "wrong"}
    )

    assert res.status_code == 401


async def test_login_rejects_unknown_email(client: TestClient) -> None:
    res = client.post(
        "/api/auth/login", json={"email": "ghost@example.com", "password": "x"}
    )
    assert res.status_code == 401


async def test_me_requires_authentication(client: TestClient) -> None:
    res = client.get("/api/auth/me")
    assert res.status_code == 401


async def test_full_login_me_logout_cycle(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _create_user(db_connection, "cycle@example.com", "correct-password")

    login_res = client.post(
        "/api/auth/login",
        json={"email": "cycle@example.com", "password": "correct-password"},
    )
    assert login_res.status_code == 200

    me_res = client.get("/api/auth/me")
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "cycle@example.com"

    logout_res = client.post("/api/auth/logout")
    assert logout_res.status_code == 200

    # The session was revoked server-side, not just the cookie cleared —
    # replaying the same cookie value must not work either.
    client.cookies.set("minicam_session", login_res.cookies["minicam_session"])
    me_after_logout = client.get("/api/auth/me")
    assert me_after_logout.status_code == 401


async def test_admin_can_create_user_and_grant_camera_permission(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _create_user(db_connection, "admin@example.com", "adminpass", is_admin=True)
    client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "adminpass"}
    )

    create_res = client.post(
        "/api/auth/users",
        json={"email": "viewer@example.com", "password": "viewerpass"},
    )
    assert create_res.status_code == 201
    new_user_id = create_res.json()["id"]

    perm_res = client.post(
        "/api/auth/permissions",
        json={
            "user_id": new_user_id,
            "camera_name": "cam1",
            "can_view": True,
            "can_use_ptz": False,
        },
    )
    assert perm_res.status_code == 200
    assert perm_res.json() == {
        "user_id": new_user_id,
        "camera_name": "cam1",
        "can_view": True,
        "can_use_ptz": False,
    }


async def test_creating_user_with_duplicate_email_returns_409(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _create_user(db_connection, "admin2@example.com", "adminpass", is_admin=True)
    await _create_user(db_connection, "taken@example.com", "whatever")
    client.post(
        "/api/auth/login",
        json={"email": "admin2@example.com", "password": "adminpass"},
    )

    res = client.post(
        "/api/auth/users", json={"email": "taken@example.com", "password": "longenough"}
    )
    assert res.status_code == 409


async def test_non_admin_cannot_create_users(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _create_user(db_connection, "plain@example.com", "plainpass")
    client.post(
        "/api/auth/login", json={"email": "plain@example.com", "password": "plainpass"}
    )

    res = client.post(
        "/api/auth/users", json={"email": "x@example.com", "password": "longenough"}
    )
    assert res.status_code == 403


async def test_unauthenticated_cannot_create_users(client: TestClient) -> None:
    res = client.post(
        "/api/auth/users", json={"email": "x@example.com", "password": "longenough"}
    )
    assert res.status_code == 401


async def test_admin_can_list_users_and_their_permissions(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _create_user(db_connection, "admin3@example.com", "adminpass", is_admin=True)
    viewer_id = await _create_user(db_connection, "viewer3@example.com", "viewerpass")
    client.post(
        "/api/auth/login",
        json={"email": "admin3@example.com", "password": "adminpass"},
    )
    client.post(
        "/api/auth/permissions",
        json={
            "user_id": viewer_id,
            "camera_name": "cam1",
            "can_view": True,
            "can_use_ptz": True,
        },
    )

    users_res = client.get("/api/auth/users")
    assert users_res.status_code == 200
    emails = {u["email"] for u in users_res.json()}
    assert {"admin3@example.com", "viewer3@example.com"} <= emails

    perms_res = client.get("/api/auth/permissions", params={"user_id": viewer_id})
    assert perms_res.status_code == 200
    assert perms_res.json() == [
        {
            "user_id": viewer_id,
            "camera_name": "cam1",
            "can_view": True,
            "can_use_ptz": True,
        }
    ]


async def test_non_admin_cannot_list_users(
    client: TestClient, db_connection: aiosqlite.Connection
) -> None:
    await _create_user(db_connection, "plain2@example.com", "plainpass")
    client.post(
        "/api/auth/login", json={"email": "plain2@example.com", "password": "plainpass"}
    )
    res = client.get("/api/auth/users")
    assert res.status_code == 403
