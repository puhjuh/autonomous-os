"""Relative motion style, independent of targets, calibration and hardware I/O."""

from dataclasses import asdict, dataclass
import math
import threading
import time
from types import MappingProxyType


@dataclass(frozen=True)
class MotionStyle:
    smooth_time: float = 1.0
    max_speed: float = 1.0

    def blend(self, other: "MotionStyle", amount: float) -> "MotionStyle":
        return MotionStyle(
            self.smooth_time + (other.smooth_time - self.smooth_time) * amount,
            self.max_speed + (other.max_speed - self.max_speed) * amount,
        )


# Experimental relative styles. Absolute calibration remains in constants.py.
STYLES = MappingProxyType({
    "neutral": MotionStyle(),
    "curious": MotionStyle(smooth_time=0.85),
    "calm": MotionStyle(smooth_time=1.35, max_speed=0.7),
    "happy": MotionStyle(smooth_time=0.9, max_speed=1.05),
    "sad": MotionStyle(smooth_time=1.5, max_speed=0.55),
    "excited": MotionStyle(smooth_time=0.75, max_speed=1.15),
    "fearful": MotionStyle(smooth_time=0.65, max_speed=1.25),
})


class AffectState:
    """Thread-safe style transitions; retarget from the currently blended style.

    Sampling uses a monotonic clock and never changes a goal or writes hardware.
    Safety limits must be applied to the resolved speed by the caller.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._name = "neutral"
        self._intensity = 0.0
        self._source = self._target = STYLES["neutral"]
        self._start = clock()
        self._duration = 0.0

    def _sample(self, now):
        progress = 1.0 if self._duration == 0 else min(
            1.0, max(0.0, (now - self._start) / self._duration))
        eased = progress * progress * (3.0 - 2.0 * progress)
        return self._source.blend(self._target, eased), progress

    def set(self, name: str, intensity: float = 1.0,
            transition_s: float = 0.5) -> None:
        if name not in STYLES:
            raise ValueError(f"Unknown affect: {name!r}")
        if not math.isfinite(intensity) or not 0 <= intensity <= 1:
            raise ValueError("intensity must be finite and within [0, 1]")
        if not math.isfinite(transition_s) or transition_s < 0:
            raise ValueError("transition_s must be finite and non-negative")
        with self._lock:
            # Repeated event delivery must not keep restarting a transition.
            intensity = 0.0 if name == "neutral" else intensity
            if (name, intensity) == (self._name, self._intensity):
                return
            now = self._clock()
            self._source, _ = self._sample(now)
            self._name, self._intensity = name, intensity
            self._target = STYLES["neutral"].blend(STYLES[name], intensity)
            self._start, self._duration = now, transition_s

    def resolve(self, smooth_time: float, max_speed: float) -> tuple[float, float]:
        with self._lock:
            style, _ = self._sample(self._clock())
        return smooth_time * style.smooth_time, max_speed * style.max_speed

    @property
    def status(self) -> dict:
        with self._lock:
            style, progress = self._sample(self._clock())
            return {"target": self._name, "intensity": self._intensity,
                    "transitioning": progress < 1.0,
                    "motion_factors": asdict(style)}
