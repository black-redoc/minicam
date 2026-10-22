"""Event recordings: settings, listing, playback and deletion of clips."""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse

from app.dependencies import (
    enforce_wan_access_gate,
    get_authorization_service,
    get_current_user,
    get_recording_repository,
    require_admin,
)
from app.models.config import (
    RecordingConfig,
    RecordingListResponse,
    RecordingResponse,
    RecordingSettingsResponse,
    RecordingStorageOption,
)
from app.models.domain import Recording, User
from app.repositories.recording_repository import RecordingRepository
from app.services.authorization import AuthorizationService
from app.services.config_writer import update_recording_config
from app.services.recording import runtime as recording_runtime
from app.services.recording.runtime import RecordingService
from app.services.recording.storage import is_option_available, storage_options
from app.settings import get_config

router = APIRouter(tags=["recordings"])

_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 200
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def get_recording_service() -> RecordingService:
    return recording_runtime.service


async def _settings_response(
    service: RecordingService, repository: RecordingRepository
) -> RecordingSettingsResponse:
    used = await repository.total_size_bytes()
    free = await asyncio.to_thread(service.free_bytes)
    return RecordingSettingsResponse(
        **get_config().recordings.model_dump(),
        used_bytes=used,
        free_bytes=free,
        options=[
            RecordingStorageOption(gb=option.gb, available=option.available)
            for option in storage_options(free, used)
        ],
    )


@router.get(
    "/api/recordings/settings",
    response_model=RecordingSettingsResponse,
    dependencies=[Depends(require_admin)],
)
async def get_recording_settings(
    service: RecordingService = Depends(get_recording_service),
    repository: RecordingRepository = Depends(get_recording_repository),
) -> RecordingSettingsResponse:
    """Return the recording settings and which storage limits fit on disk."""
    return await _settings_response(service, repository)


@router.put(
    "/api/recordings/settings",
    response_model=RecordingSettingsResponse,
    dependencies=[Depends(require_admin)],
)
async def update_recording_settings(
    body: RecordingConfig,
    service: RecordingService = Depends(get_recording_service),
    repository: RecordingRepository = Depends(get_recording_repository),
) -> RecordingSettingsResponse:
    """Persist the recording settings; applies immediately.

    A storage limit the disk can't hold is refused while recording is on.
    Lowering the limit deletes the oldest clips right away.
    """
    if body.enabled:
        used = await repository.total_size_bytes()
        free = await asyncio.to_thread(service.free_bytes)
        if not is_option_available(body.max_storage_gb, free, used):
            raise HTTPException(
                400, f"Not enough disk space for {body.max_storage_gb} GB"
            )
    update_recording_config(body)
    get_config.cache_clear()
    await service.enforce_retention()
    return await _settings_response(service, repository)


def _to_response(recording: Recording) -> RecordingResponse:
    return RecordingResponse(
        id=recording.id,
        camera_name=recording.camera_name,
        started_at=recording.started_at,
        duration_seconds=recording.duration_seconds,
        size_bytes=recording.size_bytes,
        classes=list(recording.classes),
    )


@router.get(
    "/api/recordings",
    response_model=RecordingListResponse,
    dependencies=[Depends(enforce_wan_access_gate)],
)
async def list_recordings(
    camera: str | None = None,
    before: int | None = Query(None, ge=1),
    limit: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    user: User = Depends(get_current_user),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    repository: RecordingRepository = Depends(get_recording_repository),
) -> RecordingListResponse:
    """Recordings of the cameras the user can view, newest first."""
    viewable = await authorization_service.list_viewable_camera_names(user.id)
    if camera is not None:
        viewable = [camera] if camera in viewable else []
    items = await repository.list_for_cameras(viewable, before, limit)
    return RecordingListResponse(
        items=[_to_response(r) for r in items],
        next_before=items[-1].id if len(items) == limit else None,
    )


async def _get_viewable_recording(
    recording_id: int,
    user: User,
    authorization_service: AuthorizationService,
    repository: RecordingRepository,
) -> Recording:
    # 404, not 403: a user without access must not learn the clip exists.
    recording = await repository.get(recording_id)
    if recording is None or not await authorization_service.can_view(
        user.id, recording.camera_name
    ):
        raise HTTPException(404, "Not found")
    return recording


def _download_name(recording: Recording) -> str:
    camera = _UNSAFE_FILENAME_CHARS.sub("-", recording.camera_name).strip("-")
    started = _UNSAFE_FILENAME_CHARS.sub("", recording.started_at[:19])
    return f"minicam-{camera or 'camera'}-{started}.mp4"


@router.get(
    "/api/recordings/{recording_id}/video",
    dependencies=[Depends(enforce_wan_access_gate)],
)
async def recording_video(
    recording_id: int,
    download: bool = False,
    user: User = Depends(get_current_user),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    repository: RecordingRepository = Depends(get_recording_repository),
    service: RecordingService = Depends(get_recording_service),
) -> FileResponse:
    """Serve a clip's MP4 (with Range support, for seeking in the player)."""
    recording = await _get_viewable_recording(
        recording_id, user, authorization_service, repository
    )
    try:
        path = service.path_for(recording)
    except ValueError as exc:
        raise HTTPException(404, "Not found") from exc
    if not await asyncio.to_thread(path.is_file):
        raise HTTPException(404, "Not found")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=_download_name(recording),
        content_disposition_type="attachment" if download else "inline",
        headers={"Cache-Control": "private, no-cache"},
    )


@router.delete(
    "/api/recordings/{recording_id}",
    status_code=204,
    dependencies=[Depends(require_admin)],
)
async def delete_recording(
    recording_id: int,
    repository: RecordingRepository = Depends(get_recording_repository),
    service: RecordingService = Depends(get_recording_service),
) -> Response:
    """Delete a clip and its file."""
    recording = await repository.get(recording_id)
    if recording is None:
        raise HTTPException(404, "Not found")
    try:
        path = service.path_for(recording)
    except ValueError:
        pass
    else:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    await repository.delete(recording_id)
    return Response(status_code=204)
