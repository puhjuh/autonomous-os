"""Offline Whisper STT using quantized Sherpa-ONNX models on the CPU."""
import importlib.util
import logging
import os
from pathlib import Path
import threading

import numpy as np

from .provider import STTProvider, STTSession

logger = logging.getLogger("hal.voice.stt.whisper")


class WhisperSession(STTSession):
    """Buffer one VAD turn; deliver final text synchronously when closed."""
    MAX_BYTES = 16000 * 2 * 120
    CHUNK_BYTES = 16000 * 2 * 25

    def __init__(self, provider):
        self._provider = provider
        self._audio = bytearray()
        self._on_transcript_cb = None
        self._closed = False
        self._lock = threading.RLock()

    def start(self, on_transcript):
        with self._lock:
            if self._closed:
                return False
            self._on_transcript_cb = on_transcript
            return True

    def send_audio(self, data):
        with self._lock:
            if self._closed or self._on_transcript_cb is None:
                raise RuntimeError("Whisper session is not open")
            if len(data) % 2:
                raise ValueError("Expected 16-bit PCM audio")
            if len(self._audio) + len(data) > self.MAX_BYTES:
                raise ValueError("Whisper turn exceeds 120 seconds")
            self._audio.extend(data)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            audio = bytes(self._audio)
            self._audio.clear()
            # VoiceService reads final text immediately after close returns.
            for offset in range(0, len(audio), self.CHUNK_BYTES):
                chunk = audio[offset:offset + self.CHUNK_BYTES]
                if len(chunk) < 16000:  # Ignore fragments shorter than 0.5 s.
                    continue
                samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                if np.max(np.abs(samples)) < 1e-4:
                    continue
                text = self._provider.transcribe(samples).strip()
                if text and self._on_transcript_cb:
                    self._on_transcript_cb(text, True)

    def is_closed(self):
        return self._closed


class WhisperSTT(STTProvider):
    def __init__(self, model_path=None):
        self._path = Path(model_path or os.environ.get("HAL_WHISPER_MODEL", ""))
        self._recognizer = None
        self._lock = threading.Lock()

    @property
    def available(self):
        return importlib.util.find_spec("sherpa_onnx") is not None and all(
            (self._path / ("base.en-" + suffix)).is_file()
            for suffix in ("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt")
        )

    def create_session(self):
        if not self.available:
            raise RuntimeError("Local Whisper requires sherpa-onnx and HAL_WHISPER_MODEL")
        return WhisperSession(self)

    def transcribe(self, samples):
        with self._lock:
            if self._recognizer is None:
                import sherpa_onnx
                prefix = str(self._path / "base.en-")
                self._recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
                    encoder=prefix + "encoder.int8.onnx",
                    decoder=prefix + "decoder.int8.onnx",
                    tokens=prefix + "tokens.txt", num_threads=3,
                    language="en", task="transcribe", provider="cpu", tail_paddings=500,
                )
                logger.info("Local Whisper model loaded: %s", self._path.name)
            stream = self._recognizer.create_stream()
            stream.accept_waveform(16000, samples)
            self._recognizer.decode_stream(stream)
            return stream.result.text

    @property
    def name(self):
        return "Whisper base.en (local English)"
