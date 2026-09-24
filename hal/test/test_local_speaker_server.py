"""Local embedding HTTP contract and invalid-audio handling (no model needed)."""
import base64
import io
import threading
import unittest

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from hal.drivers.voice.speaker_recognizer.local_server import LocalEmbedder, create_app


class LocalSpeakerServerTest(unittest.TestCase):
    def setUp(self):
        self.embedder = LocalEmbedder.__new__(LocalEmbedder)
        self.embedder.version = 'test-model'
        self.embedder.lock = threading.Lock()
        self.lengths = []
        def compute(samples, rate):
            self.lengths.append(len(samples))
            return np.array([0.6, 0.8], dtype=np.float32)
        self.embedder.compute = compute
        self.client = TestClient(create_app(self.embedder))

    def wav(self, seconds, silent=False, rate=16000):
        buf = io.BytesIO()
        samples = np.zeros(int(seconds * rate)) if silent else np.ones(int(seconds * rate)) * 0.1
        sf.write(buf, samples, rate, format='WAV')
        return base64.b64encode(buf.getvalue()).decode()

    def test_whole_enrollment_and_sliding_recognition(self):
        data = {'audios_b64': [self.wav(5)]}
        response = self.client.post('/embed', json=data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.lengths, [80000])
        self.assertEqual(response.json()['embed_model_version'], 'test-model')
        self.lengths.clear()
        response = self.client.post('/embed', json={**data, 'use_sliding_window': True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.lengths, [48000] * 3)
        self.assertEqual(len(response.json()['chunk_embeddings']), 3)

    def test_rejects_invalid_audio(self):
        for wav in ['invalid!', self.wav(1, silent=True), self.wav(0.1), self.wav(1, rate=8000)]:
            with self.subTest(wav=wav[:12]):
                self.assertEqual(self.client.post('/embed', json={'audios_b64': [wav]}).status_code, 400)

    def test_health_and_empty_batch(self):
        self.assertEqual(self.client.get('/health').json()['audio_embedder_version'], 'test-model')
        self.assertEqual(self.client.post('/embed', json={'audios_b64': []}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
