"""Video commands coordinate with existing audio and fail closed on policy."""

from types import SimpleNamespace
from unittest.mock import Mock
import threading
import time

import pytest
from fastapi import HTTPException

from hal.routes import display, music


def wait_until(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        threading.Event().wait(.01)
    return False


@pytest.fixture
def ready(monkeypatch):
    display.cancel_pending_video_start()
    monkeypatch.setattr(display.state, "_speaker_muted", False)
    monkeypatch.setattr(display.state, "music_cancel_active", lambda: False)
    monkeypatch.setattr(display, "audio_quiet_now", lambda _: False)
    monkeypatch.setattr(display.state, "music_service", None)
    monkeypatch.setattr(display.state, "tts_service", None)
    yield
    display.cancel_pending_video_start()


def test_video_play_checks_availability_before_stopping_music(monkeypatch, ready):
    song = SimpleNamespace(playing=True, stop=Mock())
    monkeypatch.setattr(display.state, "music_service", song)
    monkeypatch.setattr(display, "_video_request", Mock(side_effect=HTTPException(503, "offline")))
    with pytest.raises(HTTPException) as caught:
        display.video_play(display.VideoPlayRequest(query="sea otters"))
    assert caught.value.status_code == 503
    song.stop.assert_not_called()


def test_video_play_stops_song_then_forwards_trimmed_query(monkeypatch, ready):
    events = []
    song = SimpleNamespace(playing=True, stop=lambda: events.append("stop music"))
    monkeypatch.setattr(display.state, "music_service", song)
    monkeypatch.setattr(music, "_on_music_complete", lambda: events.append("restore music"))
    def request(path, payload=None):
        events.append((path, payload))
        return {"active": False}
    monkeypatch.setattr(display, "_video_request", request)
    display.video_play(display.VideoPlayRequest(query=" sea otters "))
    assert wait_until(lambda: ('/display/video/play', {'query': 'sea otters'}) in events)
    assert events == [('/display/video', None), 'stop music', 'restore music',
                      ('/display/video/play', {'query': 'sea otters'})]


def test_video_play_obeys_speaker_mute(monkeypatch, ready):
    monkeypatch.setattr(display.state, "_speaker_muted", True)
    request = Mock()
    monkeypatch.setattr(display, "_video_request", request)
    with pytest.raises(HTTPException) as caught:
        display.video_play(display.VideoPlayRequest(query="sea otters"))
    assert caught.value.status_code == 409
    request.assert_not_called()


def test_music_stops_active_video(monkeypatch):
    request = Mock(side_effect=[{"active": True}, {"active": False}])
    monkeypatch.setattr(display, "_video_request", request)
    music._stop_video_if_active()
    assert request.call_args_list[0].args == ("/display/video",)
    assert request.call_args_list[1].args == ("/display/video/stop", {})


def test_music_continues_if_optional_player_unavailable(monkeypatch):
    monkeypatch.setattr(display, "_video_request", Mock(side_effect=HTTPException(503, "offline")))
    music._stop_video_if_active()


def test_stop_cancels_video_even_if_status_is_idle(monkeypatch):
    request = Mock(side_effect=[{"active": False}, {"active": False}])
    monkeypatch.setattr(display, "_video_request", request)
    music._stop_video_if_active()
    assert request.call_args_list[1].args == ("/display/video/stop", {})


def test_mute_waits_for_start_and_then_stops_new_video(monkeypatch, ready):
    starting = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    errors = []
    monkeypatch.setattr(display.state, "_persist_speaker_state", lambda: None)
    monkeypatch.setattr(music, "_stop_video_if_active", stopped.set)

    def request(path, payload=None):
        if path.endswith("/play"):
            starting.set()
            assert release.wait(2)
            return {"active": True, "state": "starting"}
        return {"active": False}

    monkeypatch.setattr(display, "_video_request", request)

    def invoke(fn):
        try:
            fn()
        except Exception as exc:
            errors.append(exc)

    play = threading.Thread(target=invoke, args=(lambda: display.video_play(display.VideoPlayRequest(query="cats")),))
    play.start()
    assert starting.wait(1)
    mute = threading.Thread(target=invoke, args=(music.mute_speaker,))
    mute.start()
    assert not stopped.wait(.1)
    release.set()
    play.join(2)
    mute.join(2)
    assert not play.is_alive() and not mute.is_alive()
    assert not errors
    assert display.state._speaker_muted
    assert stopped.is_set()


def test_video_waits_for_filler_then_starts(monkeypatch, ready):
    checked = threading.Event()
    calls = []
    tts = SimpleNamespace(speaking=True, release_stream=Mock())
    monkeypatch.setattr(display.state, "tts_service", tts)

    def request(path, payload=None):
        calls.append(path)
        checked.set()
        return {"active": path.endswith("/play"), "state": "starting"}

    monkeypatch.setattr(display, "_video_request", request)
    errors = []

    def start():
        try:
            display.video_play(display.VideoPlayRequest(query="cats"))
        except Exception as exc:
            errors.append(exc)

    start()
    assert checked.wait(1)
    assert display.video_status() == {"active": True, "state": "waiting_for_speech"}
    assert "/display/video/play" not in calls
    tts.speaking = False
    assert wait_until(lambda: "/display/video/play" in calls)
    assert not errors
    assert calls == ["/display/video", "/display/video/play"]
    tts.release_stream.assert_called_once()


def test_stop_cancels_video_waiting_for_filler(monkeypatch, ready):
    checked = threading.Event()
    calls = []
    monkeypatch.setattr(display.state, "tts_service", SimpleNamespace(speaking=True))

    def request(path, payload=None):
        calls.append(path)
        checked.set()
        return {"active": False}

    monkeypatch.setattr(display, "_video_request", request)
    errors = []

    def start():
        try:
            display.video_play(display.VideoPlayRequest(query="cats"))
        except HTTPException as exc:
            errors.append(exc.status_code)

    start()
    assert checked.wait(1)
    assert display.video_status()["state"] == "waiting_for_speech"
    display.video_stop()
    assert errors == []
    assert display.video_status()["active"] is False
    assert "/display/video/play" not in calls
    assert "/display/video/stop" in calls


def test_video_filler_wait_is_bounded(monkeypatch, ready):
    monkeypatch.setattr(display.state, "tts_service", SimpleNamespace(speaking=True))
    monkeypatch.setattr(display, "VIDEO_SPEECH_WAIT_SECONDS", .05)
    request = Mock(return_value={"active": False})
    monkeypatch.setattr(display, "_video_request", request)
    result = display.video_play(display.VideoPlayRequest(query="cats"))
    assert result["active"] is True
    assert wait_until(lambda: display.video_status().get("state") == "error")
    assert [call.args[0] for call in request.call_args_list] == ["/display/video"]
