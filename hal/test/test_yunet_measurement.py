"""The optional direct face observer remains independent and fails closed."""
import threading
import numpy as np
import pytest

from hal.drivers.tracking.yunet_measurement import YuNetMeasurement
from hal.routes import servo


class Clock:
    now = 100.0
    def __call__(self): return self.now


class Camera:
    def __init__(self, stamp=99.9):
        self.stamp = stamp
        self.frame = np.zeros((240, 320, 3), dtype=np.uint8)
    @property
    def last_frame_snapshot(self):
        return self.frame.copy(), self.stamp


class Detector:
    def __init__(self, rows):
        self.rows, self.calls = rows, 0
    def setInputSize(self, size): self.size = size
    def detect(self, frame):
        self.calls += 1
        return 1, self.rows


def row(x=40, y=50, w=70, h=80, score=.9):
    return [x, y, w, h, 0, 0, 0, 0, 0, 0, score]


def test_detection_result_is_capture_bound_and_score_is_yunet():
    clock, camera, detector = Clock(), Camera(), Detector([row(score=.83)])
    service = YuNetMeasurement(factory=lambda: detector, clock=clock)
    result = service.measure(camera)
    assert result['status'] == 'ok' and result['source'] == 'yunet'
    sample = result['measurement']
    assert sample['bbox'] == [40, 50, 70, 80]
    assert sample['confidence'] == .83 and sample['confidence_kind'] == 'face_detection'
    assert sample['captured_monotonic'] == camera.stamp
    assert abs(sample['age_seconds']-.1) < 1e-8
    clock.now += .1
    repeated = service.measure(camera)
    assert detector.calls == 1
    assert repeated['measurement']['captured_monotonic'] == camera.stamp
    assert abs(repeated['measurement']['age_seconds']-.2) < 1e-8


def test_stale_camera_and_slow_inference_do_not_reuse_a_face():
    clock, camera, detector = Clock(), Camera(), Detector([row()])
    service = YuNetMeasurement(factory=lambda: detector, clock=clock)
    assert service.measure(camera)['status'] == 'ok'
    clock.now += .4
    assert service.measure(camera) == {'status':'stale_capture','source':'yunet','measurement':None}
    camera.stamp = clock.now
    def slow(frame):
        clock.now += .5
        return 1, [row()]
    detector.detect = slow
    assert service.measure(camera)['status'] == 'stale_capture'
    assert service.measure(camera)['measurement'] is None


@pytest.mark.parametrize('rows,status', [
    ([], 'no_face'),
    ([row(score=.01)], 'no_face'),
    ([row(), row(x=160)], 'multiple_faces'),
    ([row(x=0)], 'clipped_face'),
    ([row(x=300, w=30)], 'clipped_face'),
    ([row(x=float('nan'))], 'no_face'),
])
def test_misses_ambiguity_and_clipping_never_reuse_a_previous_result(rows, status):
    clock, camera, detector = Clock(), Camera(), Detector([row()])
    service = YuNetMeasurement(factory=lambda: detector, clock=clock)
    assert service.measure(camera)['status'] == 'ok'
    clock.now += .21
    camera.stamp = clock.now - .01
    detector.rows = rows
    result = service.measure(camera)
    assert result['status'] == status and result['measurement'] is None


def test_legacy_capture_and_concurrent_inference_fail_closed():
    clock, camera = Clock(), Camera()
    service = YuNetMeasurement(factory=lambda: Detector([row()]), clock=clock)
    assert service.measure(object())['status'] == 'atomic_capture_unavailable'
    service._lock.acquire()
    try:
        assert service.measure(camera)['status'] == 'busy'
    finally:
        service._lock.release()


def test_detector_failure_and_requested_fallback_are_explicit(monkeypatch):
    clock, camera = Clock(), Camera()
    def unavailable(): raise OSError('model missing')
    service = YuNetMeasurement(factory=unavailable, clock=clock)
    monkeypatch.setattr(servo, '_yunet_measurement', service)
    monkeypatch.setattr(servo.state, 'camera_capture', camera)
    monkeypatch.setattr(servo.state, '_camera_disabled', False)
    class Tracker:
        face_measurement = {'bbox':[10,20,30,40], 'confidence':.8, 'captured_monotonic':99.9}
    monkeypatch.setattr(servo.state, 'tracker_service', Tracker())
    assert servo.get_direct_face_measurement()['status'] == 'detector_unavailable'
    fallback = servo.get_direct_face_measurement(fallback=True)
    assert fallback['source'] == 'tracker' and fallback['status'] == 'fallback'
    assert fallback['measurement']['confidence_kind'] == 'tracker_quality'
    # A real detection miss must not fall back to a potentially stale tracker.
    service._factory = lambda: Detector([])
    miss = servo.get_direct_face_measurement(fallback=True)
    assert miss['status'] == 'no_face' and miss['measurement'] is None


def test_detector_exception_never_returns_cached_face():
    clock, camera, detector = Clock(), Camera(), Detector([row()])
    service = YuNetMeasurement(factory=lambda:detector, clock=clock)
    assert service.measure(camera)['status'] == 'ok'
    clock.now += .21
    camera.stamp = clock.now - .01
    def fail(_): raise RuntimeError('inference failure')
    detector.detect = fail
    assert service.measure(camera)['status'] == 'detector_error'


def test_throttle_preserves_original_timestamp_and_bounds_cpu():
    clock, camera, detector = Clock(), Camera(), Detector([row()])
    service = YuNetMeasurement(factory=lambda: detector, clock=clock)
    first = service.measure(camera)['measurement']
    clock.now += .1
    camera.stamp = clock.now - .01
    held = service.measure(camera)['measurement']
    assert detector.calls == 1
    assert held['captured_monotonic'] == first['captured_monotonic']
    clock.now += .11
    camera.stamp = clock.now - .01
    fresh = service.measure(camera)['measurement']
    assert detector.calls == 2
    assert fresh['captured_monotonic'] > first['captured_monotonic']


def test_tracking_recovers_after_real_detector_loss():
    clock, camera, detector = Clock(), Camera(), Detector([row()])
    service = YuNetMeasurement(factory=lambda: detector, clock=clock)
    assert service.measure(camera)['status'] == 'ok'
    clock.now += .21; camera.stamp = clock.now - .01; detector.rows = []
    assert service.measure(camera)['status'] == 'no_face'
    clock.now += .21; camera.stamp = clock.now - .01; detector.rows = [row(x=90)]
    recovered = service.measure(camera)
    assert recovered['measurement']['bbox'][0] == 90
    assert recovered['measurement']['captured_monotonic'] == camera.stamp


def test_capture_regression_clears_old_face_instead_of_reusing_it():
    clock, camera, detector = Clock(), Camera(), Detector([row()])
    service = YuNetMeasurement(factory=lambda: detector, clock=clock)
    assert service.measure(camera)['status'] == 'ok'
    camera.stamp -= .01
    result = service.measure(camera)
    assert result['status'] == 'capture_regressed'
    assert result['measurement'] is None


def test_original_frame_coordinates_are_restored_after_downscale():
    clock, camera, detector = Clock(), Camera(), Detector([row(x=40, y=50, w=70, h=80)])
    camera.frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    service = YuNetMeasurement(factory=lambda: detector, clock=clock)
    result = service.measure(camera)['measurement']
    assert detector.size == (640, 360)
    assert result['bbox'] == [80, 100, 140, 160]
    assert (result['width'], result['height']) == (1280, 720)


def test_clearly_dominant_face_beats_background_candidates():
    clock, camera = Clock(), Camera()
    detector = Detector([row(x=160, score=.53), row(x=40, score=.88), row(x=220, score=.43)])
    result = YuNetMeasurement(factory=lambda:detector, clock=clock).measure(camera)
    assert result['status'] == 'ok'
    assert result['measurement']['bbox'] == [40, 50, 70, 80]
    assert result['measurement']['confidence'] == .88
    assert result['measurement']['candidate_count'] == 3
    assert result['measurement']['selection'] == 'dominant'


@pytest.mark.parametrize('scores', [(.80, .65), (.59, .40), (.90, .90)])
def test_multiple_candidates_remain_rejected_if_winner_is_weak_or_unclear(scores):
    clock, camera = Clock(), Camera()
    detector = Detector([row(x=40, score=scores[0]), row(x=160, score=scores[1])])
    result = YuNetMeasurement(factory=lambda:detector, clock=clock).measure(camera)
    assert result['status'] == 'multiple_faces'
    assert result['measurement'] is None


def test_single_low_confidence_face_still_uses_detector_floor():
    clock, camera = Clock(), Camera()
    detector = Detector([row(score=.48)])
    result = YuNetMeasurement(factory=lambda:detector, clock=clock).measure(camera)
    assert result['status'] == 'ok'
    assert result['measurement']['selection'] == 'single'
    assert result['measurement']['confidence'] == .48


def test_dominant_clipped_face_never_falls_back_to_a_weaker_candidate():
    clock, camera = Clock(), Camera()
    detector = Detector([row(x=0, score=.93), row(x=160, score=.50)])
    result = YuNetMeasurement(factory=lambda:detector, clock=clock).measure(camera)
    assert result['status'] == 'clipped_face'
    assert result['measurement'] is None


def test_cached_reader_never_runs_or_blocks_inference_and_expires():
    clock, camera, detector = Clock(), Camera(), Detector([row()])
    service = YuNetMeasurement(factory=lambda: detector, clock=clock)
    assert service.cached(camera)['measurement'] is None
    assert service.measure(camera)['status'] == 'ok'
    # Reading cannot take the detector lock or trigger another detection.
    service._lock.acquire()
    try:
        result = service.cached(camera)
    finally:
        service._lock.release()
    assert result['status'] == 'ok' and detector.calls == 1
    assert result['measurement']['captured_monotonic'] == camera.stamp
    result['measurement']['bbox'][0] = -1
    assert service.cached(camera)['measurement']['bbox'][0] == 40
    clock.now += .3
    assert service.cached(camera)['measurement'] is None
    assert service.cached(None)['status'] == 'camera_unavailable'


def test_cached_reader_does_not_return_face_after_detection_miss(monkeypatch):
    clock, camera, detector = Clock(), Camera(), Detector([row()])
    service = YuNetMeasurement(factory=lambda: detector, clock=clock)
    service.measure(camera)
    clock.now += .21
    camera.stamp = clock.now - .01
    detector.rows = []
    assert service.measure(camera)['status'] == 'no_face'
    assert service.cached(camera)['measurement'] is None
    monkeypatch.setattr(servo, '_yunet_measurement', service)
    monkeypatch.setattr(servo.state, 'camera_capture', camera)
    monkeypatch.setattr(servo.state, '_camera_disabled', True)
    assert servo.get_cached_direct_face_measurement()['status'] == 'camera_unavailable'
