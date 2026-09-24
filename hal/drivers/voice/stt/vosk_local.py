"""Optional, offline Vosk speech recognition for 16 kHz mono PCM."""
import importlib.util
import json
import logging
import os
from pathlib import Path
import threading

from .provider import STTProvider, STTSession

logger = logging.getLogger("hal.voice.stt.vosk")


class VoskSession(STTSession):
    def __init__(self, model, sample_rate=16000):
        self._model = model
        self._sample_rate = sample_rate
        self._recognizer = None
        self._on_transcript_cb = None
        self._closed = False
        self._last_partial = ""
        self._lock = threading.RLock()

    def start(self, on_transcript):
        with self._lock:
            if self._closed:
                return False
            from vosk import KaldiRecognizer
            self._on_transcript_cb = on_transcript
            if self._recognizer is None:
                self._recognizer = KaldiRecognizer(self._model, self._sample_rate)
            return True

    def _emit(self, result, final):
        text = json.loads(result).get("text" if final else "partial", "").strip()
        if final:
            self._last_partial = ""
        elif text == self._last_partial:
            return
        else:
            self._last_partial = text
        if text and self._on_transcript_cb:
            self._on_transcript_cb(text, final)

    def send_audio(self, data):
        with self._lock:
            if self._closed or self._recognizer is None:
                raise RuntimeError("Vosk session is not open")
            if self._recognizer.AcceptWaveform(data):
                self._emit(self._recognizer.Result(), True)
            else:
                self._emit(self._recognizer.PartialResult(), False)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._recognizer is not None:
                # VoiceService assembles the final transcript immediately after close.
                # Flush synchronously so the final words cannot arrive too late.
                try:
                    self._emit(self._recognizer.FinalResult(), True)
                finally:
                    self._recognizer = None

    def is_closed(self):
        return self._closed


class VoskSTT(STTProvider):
    def __init__(self, model_path=None):
        self._path = Path(model_path or os.environ.get("HAL_VOSK_MODEL", ""))
        self._model = None
        self._lock = threading.Lock()

    @property
    def available(self):
        return importlib.util.find_spec("vosk") is not None and (self._path / "am/final.mdl").is_file()

    def create_session(self):
        if not self.available:
            raise RuntimeError("Local STT requires vosk and a model directory in HAL_VOSK_MODEL")
        with self._lock:
            if self._model is None:
                from vosk import Model
                self._model = Model(str(self._path))
                logger.info("Local speech model loaded: %s", self._path.name)
        return VoskSession(self._model)

    @property
    def name(self):
        return "Vosk (local English)"
