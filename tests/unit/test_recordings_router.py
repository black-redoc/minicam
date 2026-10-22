"""Tests for the recordings router: settings, listing, playback, deletion."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.dependencies import (
    enforce_wan_access_gate,
    get_authorization_service,
    get_current_user,
    get_recording_repository,
    require_admin,
)
from app.models.config import AppConfig, RecordingConfig
from app.models.domain import Recording, User
from app.routers import recordings as recordings_router
from app.services.config_writer import load_config
from app.services.recording import runtime as recording_runtime
from app.services.recording.storage import GIB, RESERVE_BYTES

_USER = User(
    id=7,
    email="user@example.com",
    password_hash="h",
    is_admin=False,
    is_active=True,
    created_at="t",
    updated_at="t",
)


class FakeRepository:
    def __init__(self) -> None:
        self.rows: dict[int, Recording] = {}
        self.deleted: list[int] = []

    def add(self, camera: str, file_name: str, size: int = 1000) -> Recording:
        recording = Recording(
            id=len(self.rows) + 1,
            camera_name=camera,
            file_name=file_name,
            started_at="2026-10-22T12:00:05.250000+00:00",
            duration_seconds=12.5,
            size_bytes=size,
            classes=("person",),
            created_at="t",
        )
        self.rows[recording.id] = recording
        return recording

    async def get(self, recording_id: int) -> Recording | None:
        return self.rows.get(recording_id)

    async def list_for_cameras(
        self, camera_names: list[str], before_id: int | None, limit: int
    ) -> list[Recording]:
        rows = sorted(self.rows.values(), key=lambda r: r.id, reverse=True)
        rows = [r for r in rows if r.camera_name in camera_names]
        if before_id is not None:
            rows = [r for r in rows if r.id < before_id]
        return rows[:limit]

    async def total_size_bytes(self) -> int:
        return sum(r.size_bytes for r in self.rows.values())

    async def delete(self, recording_id: int) -> bool:
        self.deleted.append(recording_id)
        return self.rows.pop(recording_id, None) is not None


class FakeAuthz:
    def __init__(self, viewable: list[str]) -> None:
        self.viewable = viewable

    async def list_viewable_camera_names(self, user_id: int) -> list[str]:
        return self.viewable

    async def can_view(self, user_id: int, camera_name: str) -> bool:
        return camera_name in self.viewable


@pytest.fixture
def repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def authz() -> FakeAuthz:
    return FakeAuthz(["entrance"])


@pytest.fixture
def service(tmp_path: Path) -> MagicMock:
    mock = MagicMock()
    mock.free_bytes.return_value = 20 * GIB
    mock.enforce_retention = AsyncMock(return_value=0)
    real = recording_runtime.RecordingService(
        tmp_path, connection=MagicMock(), config=MagicMock()
    )
    mock.path_for.side_effect = real.path_for
    return mock


@pytest.fixture
def app(repository: FakeRepository, authz: FakeAuthz, service: MagicMock) -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(recordings_router.router)
    test_app.dependency_overrides[get_current_user] = lambda: _USER
    test_app.dependency_overrides[require_admin] = lambda: _USER
    test_app.dependency_overrides[enforce_wan_access_gate] = lambda: None
    test_app.dependency_overrides[get_recording_repository] = lambda: repository
    test_app.dependency_overrides[get_authorization_service] = lambda: authz
    test_app.dependency_overrides[recordings_router.get_recording_service] = lambda: (
        service
    )
    return test_app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    yield TestClient(app)


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import app.services.config_writer as config_writer_module

    path = tmp_path / "config.toml"
    monkeypatch.setattr(
        recordings_router,
        "update_recording_config",
        lambda body: config_writer_module.update_recording_config(body, path),
    )
    return path


class TestSettings:
    def test_get_returns_settings_usage_and_options(
        self, client: TestClient, repository: FakeRepository, service: MagicMock
    ) -> None:
        repository.add("entrance", "a.mp4", size=2 * GIB)
        service.free_bytes.return_value = RESERVE_BYTES + 4 * GIB
        config = AppConfig(recordings=RecordingConfig(enabled=True, max_storage_gb=5))
        with patch.object(recordings_router, "get_config", return_value=config):
            res = client.get("/api/recordings/settings")

        assert res.status_code == 200
        assert res.json() == {
            "enabled": True,
            "max_storage_gb": 5,
            "used_bytes": 2 * GIB,
            "free_bytes": RESERVE_BYTES + 4 * GIB,
            "options": [
                {"gb": 1, "available": True},
                {"gb": 5, "available": True},
                {"gb": 10, "available": False},
                {"gb": 50, "available": False},
            ],
        }

    def test_put_persists_and_applies_retention(
        self, client: TestClient, config_path: Path, service: MagicMock
    ) -> None:
        res = client.put(
            "/api/recordings/settings", json={"enabled": True, "max_storage_gb": 10}
        )
        assert res.status_code == 200
        assert load_config(config_path).recordings == RecordingConfig(
            enabled=True, max_storage_gb=10
        )
        service.enforce_retention.assert_awaited_once()

    def test_put_rejects_a_limit_that_does_not_fit(
        self, client: TestClient, config_path: Path
    ) -> None:
        res = client.put(
            "/api/recordings/settings", json={"enabled": True, "max_storage_gb": 50}
        )
        assert res.status_code == 400
        assert res.json()["detail"] == "Not enough disk space for 50 GB"
        assert not config_path.exists()

    def test_put_allows_saving_a_disabled_state_with_any_limit(
        self, client: TestClient, config_path: Path
    ) -> None:
        res = client.put(
            "/api/recordings/settings", json={"enabled": False, "max_storage_gb": 50}
        )
        assert res.status_code == 200
        assert load_config(config_path).recordings.max_storage_gb == 50

    @pytest.mark.parametrize("gb", [0, 2, 100])
    def test_put_rejects_unknown_limits(self, client: TestClient, gb: int) -> None:
        res = client.put(
            "/api/recordings/settings", json={"enabled": False, "max_storage_gb": gb}
        )
        assert res.status_code == 422

    def test_settings_require_admin(self, app: FastAPI) -> None:
        def deny() -> User:
            raise HTTPException(403, "Admin privileges required")

        app.dependency_overrides[require_admin] = deny
        client = TestClient(app)
        assert client.get("/api/recordings/settings").status_code == 403
        res = client.put("/api/recordings/settings", json={"enabled": True})
        assert res.status_code == 403


class TestList:
    def test_lists_only_viewable_cameras_newest_first(
        self, client: TestClient, repository: FakeRepository
    ) -> None:
        first = repository.add("entrance", "a.mp4")
        repository.add("garage", "b.mp4")
        second = repository.add("entrance", "c.mp4")

        res = client.get("/api/recordings")

        assert res.status_code == 200
        body = res.json()
        assert [item["id"] for item in body["items"]] == [second.id, first.id]
        assert body["items"][0] == {
            "id": second.id,
            "camera_name": "entrance",
            "started_at": "2026-10-22T12:00:05.250000+00:00",
            "duration_seconds": 12.5,
            "size_bytes": 1000,
            "classes": ["person"],
        }
        assert body["next_before"] is None

    def test_paginates(self, client: TestClient, repository: FakeRepository) -> None:
        ids = [repository.add("entrance", f"{i}.mp4").id for i in range(3)]

        page = client.get("/api/recordings", params={"limit": 2}).json()
        assert [i["id"] for i in page["items"]] == [ids[2], ids[1]]
        assert page["next_before"] == ids[1]

        page = client.get(
            "/api/recordings", params={"limit": 2, "before": page["next_before"]}
        ).json()
        assert [i["id"] for i in page["items"]] == [ids[0]]
        assert page["next_before"] is None

    def test_filters_by_camera_without_leaking_others(
        self, client: TestClient, authz: FakeAuthz, repository: FakeRepository
    ) -> None:
        authz.viewable = ["entrance", "patio"]
        repository.add("entrance", "a.mp4")
        patio = repository.add("patio", "b.mp4")
        repository.add("garage", "c.mp4")

        res = client.get("/api/recordings", params={"camera": "patio"})
        assert [i["id"] for i in res.json()["items"]] == [patio.id]
        res = client.get("/api/recordings", params={"camera": "garage"})
        assert res.json()["items"] == []

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"before": 0}])
    def test_rejects_invalid_paging(
        self, client: TestClient, params: dict[str, int]
    ) -> None:
        assert client.get("/api/recordings", params=params).status_code == 422


class TestVideo:
    def test_serves_the_clip_inline(
        self, client: TestClient, repository: FakeRepository, tmp_path: Path
    ) -> None:
        recording = repository.add("entrance", "a.mp4")
        (tmp_path / "a.mp4").write_bytes(b"0123456789")

        res = client.get(f"/api/recordings/{recording.id}/video")

        assert res.status_code == 200
        assert res.content == b"0123456789"
        assert res.headers["content-type"] == "video/mp4"
        assert res.headers["cache-control"] == "private, no-cache"
        assert res.headers["content-disposition"].startswith("inline;")

    def test_supports_range_requests(
        self, client: TestClient, repository: FakeRepository, tmp_path: Path
    ) -> None:
        recording = repository.add("entrance", "a.mp4")
        (tmp_path / "a.mp4").write_bytes(b"0123456789")

        res = client.get(
            f"/api/recordings/{recording.id}/video", headers={"Range": "bytes=2-4"}
        )

        assert res.status_code == 206
        assert res.content == b"234"

    def test_download_uses_a_safe_attachment_name(
        self, client: TestClient, repository: FakeRepository, tmp_path: Path
    ) -> None:
        recording = repository.add("entrance", "a.mp4")
        (tmp_path / "a.mp4").write_bytes(b"x")

        res = client.get(
            f"/api/recordings/{recording.id}/video", params={"download": "true"}
        )

        assert res.headers["content-disposition"] == (
            'attachment; filename="minicam-entrance-2026-10-22T120005.mp4"'
        )

    def test_download_name_strips_unsafe_camera_characters(self) -> None:
        recording = Recording(
            1, '../"Front door"', "a.mp4", "2026-10-22T12:00:05+00:00", 1, 1, (), "t"
        )
        assert (
            recordings_router._download_name(recording)
            == "minicam-Front-door-2026-10-22T120005.mp4"
        )
        recording = Recording(1, "///", "a.mp4", "2026-10-22", 1, 1, (), "t")
        assert recordings_router._download_name(recording) == (
            "minicam-camera-2026-10-22.mp4"
        )

    def test_not_viewable_is_404(
        self, client: TestClient, repository: FakeRepository, tmp_path: Path
    ) -> None:
        recording = repository.add("garage", "a.mp4")
        (tmp_path / "a.mp4").write_bytes(b"x")
        assert client.get(f"/api/recordings/{recording.id}/video").status_code == 404

    def test_missing_row_or_file_is_404(
        self, client: TestClient, repository: FakeRepository
    ) -> None:
        assert client.get("/api/recordings/99/video").status_code == 404
        recording = repository.add("entrance", "gone.mp4")
        assert client.get(f"/api/recordings/{recording.id}/video").status_code == 404

    def test_invalid_file_name_is_404(
        self, client: TestClient, repository: FakeRepository
    ) -> None:
        recording = repository.add("entrance", "../config.toml")
        assert client.get(f"/api/recordings/{recording.id}/video").status_code == 404


class TestDelete:
    def test_deletes_the_row_and_file(
        self, client: TestClient, repository: FakeRepository, tmp_path: Path
    ) -> None:
        recording = repository.add("entrance", "a.mp4")
        (tmp_path / "a.mp4").write_bytes(b"x")

        res = client.delete(f"/api/recordings/{recording.id}")

        assert res.status_code == 204
        assert repository.rows == {}
        assert not (tmp_path / "a.mp4").exists()

    def test_deletes_a_row_with_an_invalid_file_name(
        self, client: TestClient, repository: FakeRepository, tmp_path: Path
    ) -> None:
        (tmp_path / "config.toml").write_text("keep me")
        recording = repository.add("entrance", "../config.toml")

        assert client.delete(f"/api/recordings/{recording.id}").status_code == 204
        assert repository.rows == {}
        assert (tmp_path / "config.toml").read_text() == "keep me"

    def test_missing_is_404(self, client: TestClient) -> None:
        assert client.delete("/api/recordings/99").status_code == 404

    def test_requires_admin(self, app: FastAPI, repository: FakeRepository) -> None:
        def deny() -> User:
            raise HTTPException(403, "Admin privileges required")

        recording = repository.add("entrance", "a.mp4")
        app.dependency_overrides[require_admin] = deny
        res = TestClient(app).delete(f"/api/recordings/{recording.id}")
        assert res.status_code == 403
        assert recording.id in repository.rows


def test_default_service_is_the_runtime_singleton() -> None:
    assert recordings_router.get_recording_service() is recording_runtime.service
