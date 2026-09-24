from hal.drivers.tracking.box_stability import BoxSmoother, DetectionResult, corrected_tracker


def test_jitter_smooths_position_and_size():
    s = BoxSmoother()
    assert s.update((100, 100, 100, 100), 0) == (100, 100, 100, 100)
    x, y, w, h = s.update((112, 108, 120, 110), 1 / 30)
    assert 100 < x < 112 and 100 < y < 108
    assert 100 < w < 120 and 100 < h < 110


def test_single_teleport_is_held_but_confirmed_motion_relocates():
    s = BoxSmoother()
    original = s.update((100, 100, 100, 100), 0)
    assert s.update((600, 100, 300, 300), 0.03) == original
    assert s.update((100, 100, 100, 100), 0.06) == original
    assert s.update((600, 100, 100, 100), 0.09) == original
    assert s.update((605, 100, 100, 100), 0.12)[0] == 605


def test_smoothing_is_frame_rate_independent():
    def sample(fps):
        s = BoxSmoother()
        s.update((0, 0, 100, 100), 0)
        for n in range(1, fps + 1):
            box = s.update((30, 30, 120, 120), n / fps)
        return box
    assert sample(15) == sample(30)


def test_detection_initializes_source_then_updates_current():
    source, current, tracker = object(), object(), object()
    calls = []
    result = DetectionResult((1, 2, 30, 40), source, 10)
    def initialize(t, frame, box):
        calls.append((t, frame, box))
        return True
    def update(t, frame):
        assert t is tracker and frame is current
        return True, (8, 9, 30, 40)
    assert corrected_tracker(result, current, lambda: tracker, initialize, update, 10.1) == (tracker, (8, 9, 30, 40))
    assert calls == [(tracker, source, result.bbox)]


def test_stale_or_failed_detection_cannot_replace_tracker():
    detection = DetectionResult((0, 0, 20, 20), object(), 0)
    def forbidden():
        raise AssertionError('stale result must not construct a tracker')
    assert corrected_tracker(detection, object(), forbidden, None, None, 3) is None
    assert corrected_tracker(detection, object(), object, lambda *a: True, lambda *a: (False, (0, 0, 0, 0)), 1) is None


def test_status_publishes_smoothed_box_without_changing_raw_safety_box():
    from hal.drivers.tracking.tracker_service import TrackerService, TrackingState
    service = TrackerService()
    state = TrackingState(bbox=(0, 0, 200, 200), display_bbox=(10, 10, 100, 100))
    state.running.set()
    service._state = state
    assert service.status['bbox'] == [10, 10, 100, 100]
    assert state.bbox == (0, 0, 200, 200)
    state.running.clear()
    assert service.status['bbox'] is None


def test_tracking_keeps_camera_active_without_preview_and_releases_on_failure():
    import pytest
    from hal.drivers.tracking.tracker_service import TrackerService
    class Camera:
        consumers = 0
        def acquire_consumer(self):
            self.consumers += 1
        def release_consumer(self):
            self.consumers -= 1
    camera = Camera()
    service = TrackerService()
    def run(cam, animation):
        assert cam.consumers == 1
        raise RuntimeError("test failure")
    service._track_loop_active = run
    with pytest.raises(RuntimeError, match="test failure"):
        service._track_loop(camera, None)
    assert camera.consumers == 0
