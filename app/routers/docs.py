"""Documentation router — serves Markdown files rendered as HTML."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import markdown
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/doc", tags=["docs"])
templates = Jinja2Templates(directory="app/templates")

_DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

_TOPIC_TITLES: dict[str, str] = {
    "overview": "Overview",
    "auth": "Authentication & Authorization",
    "configuration": "Configuration",
    "cloudflare": "Cloudflare Access & Tunnel",
    "cameras": "Multi-Camera Setup",
    "stream": "Video Streaming",
    "ptz": "PTZ Control",
    "api": "API Reference",
    "discovery": "Camera Discovery",
    "detection": "Object Detection (YOLO)",
    "notifications": "Detection Notifications",
}


def _load_topics() -> list[dict[str, str]]:
    """Return ordered list of {slug, title} for all available topics."""
    return [
        {"slug": slug, "title": title}
        for slug, title in _TOPIC_TITLES.items()
        if (_DOCS_DIR / f"{slug}.md").exists()
    ]


def _render_markdown(slug: str) -> str:
    """Read a .md file and return it as an HTML string."""
    path = _DOCS_DIR / f"{slug}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Topic '{slug}' not found")
    text = path.read_text(encoding="utf-8")
    return cast(
        str, markdown.markdown(text, extensions=["fenced_code", "tables", "toc"])
    )


@router.get("/", response_class=HTMLResponse)
async def docs_index(request: Request) -> HTMLResponse:
    """Render documentation index."""
    return templates.TemplateResponse(
        request=request,
        name="docs_index.html",
        context={"topics": _load_topics()},
    )


@router.get("/{slug}", response_class=HTMLResponse)
async def docs_topic(request: Request, slug: str) -> HTMLResponse:
    """Render a single documentation topic."""
    if slug not in _TOPIC_TITLES:
        raise HTTPException(status_code=404, detail=f"Topic '{slug}' not found")
    content_html = _render_markdown(slug)
    return templates.TemplateResponse(
        request=request,
        name="docs.html",
        context={
            "topics": _load_topics(),
            "current_slug": slug,
            "current_title": _TOPIC_TITLES[slug],
            "content": content_html,
        },
    )
