"""Voice stop only interrupts video on deliberate matching commands."""
from unittest.mock import Mock
import threading

import pytest

from hal.drivers.voice._internal.video_interrupt import FinalVideoStop, interrupt_video
from hal.drivers.voice._internal.realtime_turn import is_noise_turn


@pytest.mark.parametrize("phrase", ["stop", "Enough!", "Enough. Enough.", "Stop, stop!", "off off", "off", "Lampy, stop.", "Lampy. Enough.",
                                    "hey Lampy enough", "Lampy, turn off the video"])
def test_stops_active_video(phrase):
    active, stop = Mock(return_value=True), Mock()
    assert interrupt_video(phrase, active, stop)
    active.assert_called_once()
    stop.assert_called_once()


@pytest.mark.parametrize("phrase", ["turn off the light", "stop tracking", "there is enough light", "enough light enough", "stop off", "off to work", "Lampy off to work", ""])
def test_unrelated_text_never_checks_or_stops_video(phrase):
    active, stop = Mock(), Mock()
    assert not interrupt_video(phrase, active, stop)
    active.assert_not_called()
    stop.assert_not_called()


def test_idle_or_unreachable_video_preserves_normal_voice_route():
    stop = Mock()
    assert not interrupt_video("stop", Mock(return_value=False), stop)
    assert not interrupt_video("stop", Mock(side_effect=OSError("offline")), stop)
    assert not interrupt_video("stop", Mock(return_value=True), Mock(side_effect=OSError("offline")))
    stop.assert_not_called()


def test_short_noise_classified_stop_still_interrupts_active_video(monkeypatch):
    # This is the observed STT / classifier result while the video played.
    from hal import config as hal_config
    monkeypatch.setattr(hal_config, "REALTIME_NOISE_GUARD_MAX_WORDS", 3)
    assert is_noise_turn("Enough. Enough.", 2.0, audio_is_speech=False)
    stop = Mock()
    assert interrupt_video("Enough. Enough.", Mock(return_value=True), stop)
    stop.assert_called_once()


def test_confirmed_segment_stops_active_video_before_session_finishes():
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def stop():
        calls.append("stop")
        entered.set()
        assert release.wait(2)

    handler = FinalVideoStop(lambda: True, stop)
    handler.on_final("Enough.")
    assert entered.wait(1)
    # Callback has already returned even though stopping is still in progress.
    assert handler.accepted()
    handler.on_final("Enough. Enough.")
    assert calls == ["stop"]
    release.set()


def test_confirmed_segment_leaves_idle_or_unrelated_turn_alone():
    active, stop = Mock(return_value=False), Mock()
    handler = FinalVideoStop(active, stop)
    handler.on_final("turn off the light")
    active.assert_not_called()
    handler.on_final("Enough.")
    assert not handler.accepted(timeout=1)
    active.assert_called_once()
    stop.assert_not_called()
