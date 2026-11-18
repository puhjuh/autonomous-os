"""Opt-in single-frame YuNet observations, independent of the LCD tracker.

A request reads one atomic capture, performs face detection on that exact frame,
and reports the detector score and capture-arrival timestamp together. It never
moves motors, runs a background thread, changes the existing tracker, or saves
an image. No-face, multiple-face, clipped, stale, and busy results fail closed.
"""
from __future__ import annotations

import math
from pathlib import Path
import threading
import time

import cv2

from hal.config import YUNET_CONFIDENCE_THRESHOLD
from hal.drivers.tracking.frame_utils import downscale, scale_bbox

MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
MAX_CAPTURE_AGE = 0.35
MIN_INFERENCE_INTERVAL = 0.20  # Cap extra detector load at five inferences/s.
# Multiple candidates need an unambiguous, strong winner. A singleton still
# uses the ordinary YuNet floor; these are not tracker-quality thresholds.
DOMINANT_FACE_MIN_SCORE = 0.60
DOMINANT_FACE_MIN_GAP = 0.20


def _create_detector():
    if not MODEL_PATH.is_file():
        raise FileNotFoundError("YuNet model not available")
    return cv2.FaceDetectorYN.create(
        str(MODEL_PATH), "", (320, 320),
        score_threshold=YUNET_CONFIDENCE_THRESHOLD, nms_threshold=0.3, top_k=50,
    )


class YuNetMeasurement:
    """Separate lazy detector; at most one inference is in flight per process."""

    def __init__(self, *, factory=_create_detector, clock=time.monotonic):
        self._factory = factory
        self._clock = clock
        self._detector = None
        self._lock = threading.Lock()
        self._last_capture = None
        self._last_result = None
        self._last_inference_at = None

    @staticmethod
    def _result(status, measurement=None):
        return {"status": status, "source": "yunet", "measurement": measurement}

    def cached(self, camera):
        """Read a recent detection without running inference or blocking it."""
        if camera is None:
            return self._result("camera_unavailable")
        # Detection publishes a new result object in one assignment. Reading
        # that reference does not acquire the detector's nonblocking lock,
        # which could otherwise cause a motor caller to receive "busy".
        result = self._last_result
        if not result or result.get("status") != "ok" or not result.get("measurement"):
            return self._result("no_cached_face")
        sample = dict(result["measurement"])
        age = self._clock() - sample["captured_monotonic"]
        if not 0 <= age <= MAX_CAPTURE_AGE:
            return self._result("stale_capture")
        sample["bbox"] = list(sample["bbox"])
        sample["age_seconds"] = age
        return self._result("ok", sample)

    def measure(self, camera):
        if camera is None:
            return self._result("camera_unavailable")
        if not self._lock.acquire(blocking=False):
            return self._result("busy")
        try:
            snapshot = getattr(camera, "last_frame_snapshot", None)
            if not isinstance(snapshot, tuple) or len(snapshot) != 2:
                return self._result("atomic_capture_unavailable")
            frame, captured = snapshot
            now = self._clock()
            if (frame is None or type(captured) not in (int, float)
                    or not math.isfinite(captured) or not 0 < captured <= now
                    or now - captured > MAX_CAPTURE_AGE):
                return self._result("stale_capture")
            if self._last_capture is not None and captured < self._last_capture:
                # A capture restart or out-of-order timestamp must not look
                # like forward progress to a motor consumer.
                self._last_capture = self._last_result = None
                return self._result("capture_regressed")
            if self._last_capture == captured:
                result = self._last_result
                if result and result["measurement"]:
                    measurement = dict(result["measurement"])
                    measurement["age_seconds"] = now - captured
                    return self._result(result["status"], measurement)
                return result or self._result("no_face")
            if (self._last_inference_at is not None
                    and now - self._last_inference_at < MIN_INFERENCE_INTERVAL):
                # Return only the last *actual* detection, with its original
                # capture time. Never relabel it as a new frame or let it live
                # beyond the normal freshness window.
                result = self._last_result
                if result and result["measurement"]:
                    measurement = dict(result["measurement"])
                    age = now - measurement["captured_monotonic"]
                    if 0 <= age <= MAX_CAPTURE_AGE:
                        measurement["age_seconds"] = age
                        return self._result(result["status"], measurement)
                return self._result("throttled")
            if self._detector is None:
                try:
                    self._detector = self._factory()
                    if self._detector is None:
                        raise RuntimeError("YuNet detector unavailable")
                except Exception:
                    return self._result("detector_unavailable")
            try:
                height, width = frame.shape[:2]
                if width <= 0 or height <= 0:
                    return self._result("invalid_frame")
                small, scale = downscale(frame)
                if not math.isfinite(scale) or scale <= 0:
                    return self._result("invalid_frame")
                self._detector.setInputSize((small.shape[1], small.shape[0]))
                self._last_inference_at = self._clock()
                _, rows = self._detector.detect(small)
                faces = []
                for row in (() if rows is None else rows):
                    try:
                        x, y, w, h = (float(v) for v in row[:4])
                        score = float(row[-1])
                    except (TypeError, ValueError, IndexError):
                        continue
                    if (not all(math.isfinite(v) for v in (x,y,w,h,score))
                            or not YUNET_CONFIDENCE_THRESHOLD <= score <= 1
                            or w <= 0 or h <= 0):
                        continue
                    faces.append((x,y,w,h,score))
                if self._clock() - captured > MAX_CAPTURE_AGE:
                    return self._result("stale_capture")
                if not faces:
                    result = self._result("no_face")
                else:
                    ranked = sorted(faces, key=lambda candidate: candidate[4], reverse=True)
                    dominant = (len(ranked) == 1 or
                                (ranked[0][4] >= DOMINANT_FACE_MIN_SCORE and
                                 ranked[0][4] - ranked[1][4] >= DOMINANT_FACE_MIN_GAP))
                    if not dominant:
                        result = self._result("multiple_faces")
                    else:
                        x,y,w,h,score = ranked[0]
                        box = scale_bbox((x,y,w,h), 1.0/scale)
                        bx,by,bw,bh = box
                        if bx <= 0 or by <= 0 or bx+bw >= width or by+bh >= height:
                            result = self._result("clipped_face")
                        else:
                            result = self._result("ok", {
                                "bbox": list(box), "width": width, "height": height,
                                "confidence": score, "confidence_kind": "face_detection",
                                "candidate_count": len(ranked),
                                "selection": "single" if len(ranked) == 1 else "dominant",
                                "captured_monotonic": captured,
                                "age_seconds": self._clock() - captured,
                            })
                self._last_capture, self._last_result = captured, result
                return result
            except Exception:
                # Detection errors never reuse an old box.
                self._last_capture = self._last_result = None
                return self._result("detector_error")
        finally:
            self._lock.release()
