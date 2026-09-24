"""Display smoothing and frame-matched detector corrections."""
from dataclasses import dataclass
import math


class BoxSmoother:
    """Smooth center/size; require two consistent outliers before relocation.

    Display only: raw boxes remain available to the motor safety gates.
    """
    def __init__(self, time_constant=0.12):
        self.time_constant = time_constant
        self.reset()

    def reset(self):
        self.value = self.timestamp = self.pending = None

    @staticmethod
    def _distant(a, b):
        distance = math.hypot(a[0] - b[0], a[1] - b[1])
        scale = max(a[2] / b[2], b[2] / a[2], a[3] / b[3], b[3] / a[3])
        return distance > max(80, min(b[2], b[3])) or scale > 1.8

    def update(self, bbox, now):
        x, y, w, h = bbox
        if not all(math.isfinite(v) for v in bbox) or w <= 0 or h <= 0:
            return self._box() if self.value is not None else None
        measured = (x + w / 2, y + h / 2, float(w), float(h))
        if self.value is None or now - self.timestamp > 0.5:
            self.value = measured
            self.pending = None
        elif self._distant(measured, self.value):
            if self.pending is not None and not self._distant(measured, self.pending):
                self.value = measured
                self.pending = None
            else:
                self.pending = measured
        else:
            self.pending = None
            alpha = 1 - math.exp(-max(0, now - self.timestamp) / self.time_constant)
            self.value = tuple(a + alpha * (b - a) for a, b in zip(self.value, measured))
        self.timestamp = now
        return self._box()

    def _box(self):
        cx, cy, w, h = self.value
        return round(cx - w / 2), round(cy - h / 2), max(1, round(w)), max(1, round(h))


@dataclass(frozen=True)
class DetectionResult:
    bbox: object
    frame: object
    captured_at: float


def corrected_tracker(detection, current_frame, create, initialize, update, now):
    """Seed on the detection frame, then advance to the current image."""
    if detection.bbox is None or now - detection.captured_at > 2.0:
        return None
    tracker = create()
    if tracker is None:
        return None
    try:
        if initialize(tracker, detection.frame, detection.bbox) is False:
            return None
        ok, bbox = update(tracker, current_frame)
        if not ok or not all(math.isfinite(v) for v in bbox) or bbox[2] <= 0 or bbox[3] <= 0:
            return None
        return tracker, tuple(int(v) for v in bbox)
    except Exception:
        return None
