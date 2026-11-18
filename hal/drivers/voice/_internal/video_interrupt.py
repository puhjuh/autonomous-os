"""Recognize the small set of direct stop requests during video playback."""
import logging
import re
import threading

logger = logging.getLogger("hal.voice")

_PREFIX = re.compile(r"^(?:(?:hey|hello|hi|okay|ok|alo|wake up)\s+)?lampy(?:[\s,.:!?]+)", re.I)
_PHRASES = frozenset({
    "stop", "enough", "off", "please stop", "stop please", "that's enough",
    "that is enough", "ok stop", "okay stop", "stop playing", "stop playback",
    "stop the video", "stop video", "video off", "turn off the video",
})


def is_video_stop_phrase(transcript: str) -> bool:
    text = transcript.strip().lower().strip(" \t\r\n.,!?;:")
    text = _PREFIX.sub("", text).strip(" \t\r\n.,!?;:")
    if text in _PHRASES:
        return True
    # STT may duplicate a short instruction across final segments. Accept
    # repeated identical stop words, but never turn a longer sentence into a
    # stop by simply finding a stop word inside it.
    words = re.split(r"[\s.,!?;:]+", text)
    return 2 <= len(words) <= 5 and words[0] in {"stop", "enough", "off"} and all(word == words[0] for word in words)


def interrupt_video(transcript: str, is_active, stop) -> bool:
    """Stop only for an exact request while video is active; fail open on error."""
    if not is_video_stop_phrase(transcript):
        return False
    try:
        if not is_active():
            return False
        stop()
        return True
    except Exception as exc:
        logger.warning("Video voice interruption failed: %s", exc)
        return False


class FinalVideoStop:
    """Handle a confirmed STT segment without blocking its provider callback."""
    def __init__(self, is_active, stop):
        self._is_active = is_active
        self._stop = stop
        self._lock = threading.Lock()
        self._accepted = threading.Event()
        self._decided = threading.Event()
        self._in_flight = False

    def on_final(self, text):
        if not is_video_stop_phrase(text):
            return
        with self._lock:
            if self._in_flight or self._accepted.is_set():
                return
            self._in_flight = True
            self._decided.clear()
        threading.Thread(target=self._attempt, daemon=True, name="video-voice-stop").start()

    def _attempt(self):
        try:
            if self._is_active():
                # Once the active player accepted the command, the completed
                # turn must not also be sent to the conversation agent.
                self._accepted.set()
                self._decided.set()
                self._stop()
        except Exception as exc:
            logger.warning("Video voice interruption failed: %s", exc)
        finally:
            self._decided.set()
            with self._lock:
                self._in_flight = False

    def accepted(self, timeout=0.35):
        with self._lock:
            pending = self._in_flight
        if pending:
            self._decided.wait(timeout)
        return self._accepted.is_set()
