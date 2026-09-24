"""Whisper session boundaries and callback behavior without a model download."""
import unittest
from unittest.mock import Mock
import numpy as np
from hal.drivers.voice.stt.whisper_local import WhisperSession, WhisperSTT


class WhisperSessionTests(unittest.TestCase):
    def test_final_is_synchronous_and_once(self):
        provider = Mock(); provider.transcribe.return_value = 'Hello lamp.'
        session = WhisperSession(provider); events = []
        session.start(lambda *_: self.fail('stale callback'))
        session._on_transcript_cb = lambda *args: events.append(args)
        session.send_audio(np.full(16000, 1000, dtype=np.int16).tobytes())
        self.assertEqual(events, [])
        session.close(); session.close()
        self.assertEqual(events, [('Hello lamp.', True)])
        provider.transcribe.assert_called_once()
        self.assertTrue(session.is_closed())
        self.assertFalse(session.start(lambda *_: None))
        with self.assertRaises(RuntimeError): session.send_audio(b'00')

    def test_silence_and_keepalive_do_not_hallucinate(self):
        provider = Mock()
        for data in (b'', bytes(32000)):
            session = WhisperSession(provider); session.start(lambda *_: self.fail('silent output'))
            session.send_audio(data); session.close()
        provider.transcribe.assert_not_called()

    def test_long_turns_are_split_with_bounded_buffer(self):
        provider = Mock(); provider.transcribe.return_value = 'text'
        session = WhisperSession(provider); session.start(lambda *_: None)
        session.send_audio(np.full(16000 * 30, 100, dtype=np.int16).tobytes())
        session.close()
        self.assertEqual([len(call.args[0]) for call in provider.transcribe.call_args_list], [16000 * 25, 16000 * 5])
        self.assertEqual(len(session._audio), 0)

    def test_missing_model_and_malformed_pcm(self):
        self.assertFalse(WhisperSTT('/nonexistent').available)
        with self.assertRaises(RuntimeError): WhisperSTT('/nonexistent').create_session()
        session = WhisperSession(Mock()); session.start(lambda *_: None)
        with self.assertRaises(ValueError): session.send_audio(b'1')

if __name__ == '__main__':
    unittest.main()
