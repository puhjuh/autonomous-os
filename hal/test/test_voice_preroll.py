"""VAD holdoff must not evict the beginning of an addressed utterance."""

from types import SimpleNamespace

import pytest

from hal.drivers.tracking import gaze
from hal.drivers.voice import voice_service as voice


@pytest.mark.parametrize("configured,quiet_frames", [(8, 8), (8, 2), (0, 8)])
def test_vad_keeps_pre_trigger_history_during_holdoff(monkeypatch, configured, quiet_frames):
    for name, value in {
        "STT_KEEPALIVE": False,
        "PRE_ROLL_FRAMES": configured,
        "BARGE_IN_ENABLED": False,
        "RMS_THRESHOLD": 100,
        "SPEECH_HOLDOFF_S": 0.2,
        "SESSION_COOLDOWN_S": 0,
        "WARM_MIC": False,
    }.items():
        monkeypatch.setattr(voice.voice_cfg, name, value)
    clock = SimpleNamespace(now=1.0)
    monkeypatch.setattr(voice, "time", SimpleNamespace(time=lambda: clock.now, sleep=lambda _: None))
    monkeypatch.setattr(voice, "rms", lambda data, _: data)
    monkeypatch.setattr(voice, "resample_to_stt", lambda data, *_: data)
    monkeypatch.setattr(gaze, "on_speech_start", lambda: False)

    service = object.__new__(voice.VoiceService)
    service._running = True
    service._np = None
    service._silero_vad = None
    service._backchannel = SimpleNamespace(self_audio_active=False)
    service._tts_is_speaking = lambda: False
    service._music_is_playing = lambda: False
    service._webrtcvad_is_speech = lambda *_: True
    service._silero_reset_state = lambda: None
    captured = []

    def stream(_mic, _frame_size, _rate, **kwargs):
        captured.extend(kwargs["speech_pre_buffer"])
        service._running = False

    service._stream_session = stream
    quiet = list(range(1, quiet_frames + 1))
    frames = iter(quiet + [500] * 8)

    class Mic:
        def read(self, _size):
            clock.now += 0.064
            return next(frames), False

    service._vad_loop(Mic(), 1024, 16000)
    expected = quiet[-configured:] if configured else []
    assert captured[:len(expected)] == expected
    assert captured[len(expected):] == [500] * 5
