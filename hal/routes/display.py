"""Display route handlers -- /display/* endpoints."""

import json
import os
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

import hal.app_state as state
from hal.safety.policy import audio_quiet_now
from hal.models import (
    DisplayEyesRequest,
    DisplayInfoRequest,
    DisplayStateResponse,
    StatusResponse,
)

router = APIRouter(tags=["Display"])

# A video request may arrive while the acknowledgement is still speaking.
# Keep its wait cancellable without holding the speaker-operation lock.
_pending_video_start = None
_pending_video_error = None
VIDEO_SPEECH_WAIT_SECONDS = 10.0


def cancel_pending_video_start():
    global _pending_video_start, _pending_video_error
    with state.audio_operation_lock:
        if _pending_video_start is not None:
            _pending_video_start.set()
            _pending_video_start = None
        _pending_video_error = None


class VideoPlayRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)


def _video_request(path: str, payload: dict | None = None) -> dict:
    """Forward to the separate, loopback-only player; never accept a remote host."""
    port = os.environ.get("LAMP_VIDEO_PORT", "8878")
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise HTTPException(503, "Invalid video player port")
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"http://127.0.0.1:{int(port)}{path}", data=body,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        timeout = 8 if payload is not None else 0.25
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(16384))
    except HTTPError as exc:
        raise HTTPException(exc.code, "Video player rejected the request") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise HTTPException(503, "Video player unavailable") from exc


def video_active() -> bool:
    """The optional player must not prevent ordinary speech when absent."""
    with state.audio_operation_lock:
        if _pending_video_start is not None:
            return True
    try:
        return bool(_video_request("/display/video").get("active"))
    except HTTPException as exc:
        if exc.status_code == 503:
            return False
        raise


@router.get("/display/video")
def video_status():
    with state.audio_operation_lock:
        if _pending_video_start is not None:
            return {"active": True, "state": "waiting_for_speech"}
        if _pending_video_error is not None:
            return {"active": False, "state": "error", "error": _pending_video_error}
    return _video_request("/display/video")


@router.post("/display/video/play")
def video_play(req: VideoPlayRequest):
    global _pending_video_start
    if not req.query.strip():
        raise HTTPException(422, "A search query is required")
    with state.audio_operation_lock:
        if state._speaker_muted or state.music_cancel_active() or audio_quiet_now(state.safety_policy):
            raise HTTPException(409, "Video audio is unavailable right now")
        # Check availability before waiting for speech or interrupting music.
        _video_request("/display/video")
        cancel_pending_video_start()
        pending = threading.Event()
        _pending_video_start = pending
        worker = threading.Thread(
            target=_complete_video_start, args=(pending, req.query.strip()),
            daemon=True, name="video-start",
        )
        worker.start()
    return {"active": True, "state": "waiting_for_speech"}


def _complete_video_start(pending, query):
    global _pending_video_start, _pending_video_error
    deadline = time.monotonic() + VIDEO_SPEECH_WAIT_SECONDS
    failure = None
    try:
        while True:
            with state.audio_operation_lock:
                if _pending_video_start is not pending or pending.is_set():
                    raise HTTPException(409, "Video request cancelled")
                if state._speaker_muted or state.music_cancel_active() or audio_quiet_now(state.safety_policy):
                    raise HTTPException(409, "Video audio is unavailable right now")
                if not state.tts_service or not state.tts_service.speaking:
                    if state.music_service and state.music_service.playing:
                        state.music_service.stop()
                        from hal.routes.music import _on_music_complete
                        _on_music_complete()
                    if state.tts_service and hasattr(state.tts_service, "release_stream"):
                        state.tts_service.release_stream()
                    try:
                        result = _video_request("/display/video/play", {"query": query})
                    except HTTPException:
                        # A timed-out request may still execute in the player.
                        try:
                            _video_request("/display/video/stop", {})
                        except HTTPException as exc:
                            state.logger.warning("Failed to cancel pending video start: %s", exc)
                        raise
                    if result.get("state") == "error":
                        raise HTTPException(503, "Video playback could not start")
                    return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HTTPException(409, "Speech did not finish before video playback timed out")
            pending.wait(min(0.05, remaining))
    except HTTPException as exc:
        failure = str(exc.detail)
        if not pending.is_set():
            state.logger.warning("Video start failed: %s", failure)
    except Exception as exc:
        failure = "Video playback could not start"
        state.logger.exception("Unexpected video start error: %s", exc)
    finally:
        with state.audio_operation_lock:
            if _pending_video_start is pending:
                _pending_video_start = None
                _pending_video_error = failure


@router.post("/display/video/stop")
@state.serialized_audio_operation
def video_stop():
    cancel_pending_video_start()
    return _video_request("/display/video/stop", {})


@router.get("/display", response_model=DisplayStateResponse)
def get_display_state():
    """Get current display state."""
    if not state.display_service:
        return {"mode": "unavailable", "hardware": False, "available_expressions": []}
    return state.display_service.get_state()


@router.post("/display/eyes", response_model=StatusResponse)
def set_display_eyes(req: DisplayEyesRequest):
    """Set eye expression on the round LCD display."""
    if not state.display_service:
        raise HTTPException(503, "Display not available")
    state.display_service.set_expression(req.expression, req.pupil_x, req.pupil_y)
    return {"status": "ok"}


@router.post("/display/info", response_model=StatusResponse)
def set_display_info(req: DisplayInfoRequest):
    """Switch display to info mode with text content."""
    if not state.display_service:
        raise HTTPException(503, "Display not available")
    state.display_service.set_info(req.text, req.subtitle)
    return {"status": "ok"}


@router.post("/display/eyes-mode", response_model=StatusResponse)
def switch_to_eyes_mode():
    """Switch display back to eyes mode (default)."""
    if not state.display_service:
        raise HTTPException(503, "Display not available")
    state.display_service.set_eyes_mode()
    return {"status": "ok"}


@router.get("/display/snapshot")
def display_snapshot():
    """Get current display frame as JPEG."""
    if not state.display_service:
        raise HTTPException(503, "Display not available")
    data = state.display_service.get_snapshot_bytes()
    if not data:
        raise HTTPException(404, "No frame rendered yet")
    return Response(content=data, media_type="image/jpeg")


@router.get("/display/frame.png")
def display_frame_png():
    """Get the last rendered LCD framebuffer as a lossless PNG."""
    if not state.display_service:
        raise HTTPException(503, "Display not available")
    data = state.display_service.get_frame_png_bytes()
    if not data:
        raise HTTPException(404, "No frame rendered yet")
    return Response(
        content=data,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
