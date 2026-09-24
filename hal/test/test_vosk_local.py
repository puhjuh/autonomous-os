"""Session boundary tests without downloading a speech model."""
import sys
import types
import unittest
from unittest.mock import patch
from hal.drivers.voice.stt.vosk_local import VoskSession, VoskSTT


class Recognizer:
    def __init__(self, model, rate):
        self.final_count = 0
    def AcceptWaveform(self, data):
        return data == b'final'
    def PartialResult(self):
        return '{"partial":"hello"}'
    def Result(self):
        return '{"text":"hello lamp"}'
    def FinalResult(self):
        self.final_count += 1
        return '{"text":"what time is it"}'


class VoskSessionTests(unittest.TestCase):
    def test_segments_and_close_flush_once(self):
        events = []
        with patch.dict(sys.modules, vosk=types.SimpleNamespace(KaldiRecognizer=Recognizer)):
            s = VoskSession(object())
            self.assertTrue(s.start(lambda *x: events.append(x)))
            s.send_audio(b'partial'); s.send_audio(b'partial')
            s.send_audio(b'final')
            s.close(); s.close()
            self.assertEqual(events, [('hello', False), ('hello lamp', True), ('what time is it', True)])
            self.assertTrue(s.is_closed())
            self.assertFalse(s.start(lambda *_: None))
            with self.assertRaises(RuntimeError): s.send_audio(b'audio')

    def test_keepalive_callback_replacement(self):
        events = []
        with patch.dict(sys.modules, vosk=types.SimpleNamespace(KaldiRecognizer=Recognizer)):
            s = VoskSession(object()); s.start(lambda *_: None)
            s._on_transcript_cb = lambda *x: events.append(x)
            s.send_audio(b'final'); s.close()
        self.assertEqual(events[0], ('hello lamp', True))

    def test_missing_model_does_not_fall_back(self):
        provider = VoskSTT('/nonexistent/vosk-model')
        self.assertFalse(provider.available)
        with self.assertRaises(RuntimeError): provider.create_session()


class LocalVoiceRequestTests(unittest.TestCase):
    def test_local_start_needs_no_cloud_key(self):
        from hal.models import VoiceStartRequest
        req = VoiceStartRequest(tts_provider="piper", tts_voice="en_US-kristin-medium")
        self.assertEqual(req.llm_api_key, "")
        self.assertEqual(req.llm_base_url, "")
