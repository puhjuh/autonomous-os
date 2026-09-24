"""Loopback speaker embedding API backed by a preinstalled Sherpa-ONNX model.

Run with HAL_SPEAKER_MODEL=/path/model.onnx python -m
hal.drivers.voice.speaker_recognizer.local_server. No model downloads at runtime.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import threading
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class EmbedRequest(BaseModel):
    audios_b64: list[str] = Field(min_length=1, max_length=8)
    preprocess: bool = False
    use_sliding_window: bool = False


class LocalEmbedder:
    def __init__(self, model_path: str):
        import sherpa_onnx

        path = Path(model_path)
        self.version = "wespeaker-local-" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(path), num_threads=2, debug=False, provider="cpu"
        )
        if not config.validate():
            raise ValueError("Invalid speaker model configuration")
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self.lock = threading.Lock()

    def compute(self, samples: np.ndarray, rate: int) -> np.ndarray:
        stream = self.extractor.create_stream()
        stream.accept_waveform(sample_rate=rate, waveform=np.ascontiguousarray(samples))
        stream.input_finished()
        if not self.extractor.is_ready(stream):
            raise ValueError("Not enough speech for speaker identification")
        vector = np.asarray(self.extractor.compute(stream), dtype=np.float32)
        norm = np.linalg.norm(vector)
        if not np.isfinite(vector).all() or norm < 1e-12:
            raise ValueError("Invalid speaker embedding")
        return vector / norm

    def embed(self, request: EmbedRequest) -> dict:
        if request.preprocess:
            raise ValueError("Audio must be preprocessed by HAL (preprocess=false)")
        chunks = []
        total_seconds = 0.0
        with self.lock:
            for encoded in request.audios_b64:
                if len(encoded) > 6_000_000:
                    raise ValueError("Audio exceeds size limit")
                raw = base64.b64decode(encoded, validate=True)
                with sf.SoundFile(io.BytesIO(raw)) as audio:
                    rate = audio.samplerate
                    seconds = len(audio) / rate
                    total_seconds += seconds
                    if audio.format != "WAV" or audio.channels != 1 or rate != 16000:
                        raise ValueError("Expected mono 16 kHz WAV from HAL")
                    if seconds < 0.5 or total_seconds > 120:
                        raise ValueError("Expected 0.5–120 seconds of audio per request")
                    samples = audio.read(dtype="float32")
                if not np.isfinite(samples).all() or np.max(np.abs(samples)) < 1e-6:
                    raise ValueError("Audio is silent or invalid")
                if request.use_sliding_window and len(samples) > 3 * rate:
                    starts = list(range(0, len(samples) - 3 * rate + 1, rate))
                    if starts[-1] != len(samples) - 3 * rate:
                        starts.append(len(samples) - 3 * rate)
                    windows = [samples[start:start + 3 * rate] for start in starts]
                else:
                    windows = [samples]
                chunks.extend(self.compute(window, rate) for window in windows)
        mean = np.mean(chunks, axis=0)
        norm = np.linalg.norm(mean)
        if norm < 1e-12:
            raise ValueError("Invalid aggregate embedding")
        return {"embedding": (mean / norm).tolist(),
                "chunk_embeddings": [chunk.tolist() for chunk in chunks],
                "embed_model_version": self.version}


def create_app(embedder: LocalEmbedder) -> FastAPI:
    app = FastAPI(title="Local speaker embeddings", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health():
        return {"status": "ok", "audio_embedder_version": embedder.version}

    @app.post("/embed")
    def embed(request: EmbedRequest):
        try:
            return embedder.embed(request)
        except (ValueError, RuntimeError, sf.LibsndfileError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


if __name__ == "__main__":
    import uvicorn

    model = os.environ["HAL_SPEAKER_MODEL"]
    uvicorn.run(create_app(LocalEmbedder(model)), host="127.0.0.1", port=5002)
