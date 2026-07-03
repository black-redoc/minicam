"""Tests for the docs router: GET /doc/ and GET /doc/{slug}"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

import app.routers.docs as docs_module
from app.routers import docs as docs_router


def _make_client() -> TestClient:
    test_app = FastAPI()
    test_app.mount("/static", StaticFiles(directory="app/static"), name="static")
    test_app.include_router(docs_router.router)
    return TestClient(test_app)


@pytest.fixture()
def client() -> TestClient:
    return _make_client()


class TestDocsIndex:
    def test_returns_200(self, client: TestClient) -> None:
        res = client.get("/doc/")
        assert res.status_code == 200

    def test_returns_html(self, client: TestClient) -> None:
        res = client.get("/doc/")
        assert "text/html" in res.headers["content-type"]

    def test_contains_documentation_text(self, client: TestClient) -> None:
        res = client.get("/doc/")
        assert res.status_code == 200
        assert "doc" in res.text.lower()


class TestDocsTopic:
    def test_returns_200_for_overview(self, client: TestClient) -> None:
        res = client.get("/doc/overview")
        assert res.status_code == 200

    def test_returns_html_for_valid_slug(self, client: TestClient) -> None:
        res = client.get("/doc/overview")
        assert "text/html" in res.headers["content-type"]

    def test_returns_404_for_unknown_slug(self, client: TestClient) -> None:
        res = client.get("/doc/nonexistent-topic")
        assert res.status_code == 404

    def test_all_registered_slugs_return_200(self, client: TestClient) -> None:
        for slug in docs_module._TOPIC_TITLES:
            res = client.get(f"/doc/{slug}")
            assert res.status_code == 200, f"Slug '{slug}' returned {res.status_code}"

    def test_missing_md_file_returns_404(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(docs_module, "_DOCS_DIR", tmp_path)
        res = _make_client().get("/doc/overview")
        assert res.status_code == 404


class TestLoadTopics:
    def test_excludes_missing_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(docs_module, "_DOCS_DIR", tmp_path)
        topics = docs_module._load_topics()
        assert topics == []

    def test_includes_existing_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "overview.md").write_text("# Overview", encoding="utf-8")
        monkeypatch.setattr(docs_module, "_DOCS_DIR", tmp_path)
        topics = docs_module._load_topics()
        assert any(t["slug"] == "overview" for t in topics)
