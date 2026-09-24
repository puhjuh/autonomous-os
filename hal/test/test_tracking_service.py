"""Focused lifecycle tests for object tracking."""

import threading

from hal.drivers.tracking import constants as C
from hal.drivers.tracking import tracker_service
from hal.drivers.tracking.tracker_service import TrackerService, TrackingState


class _FakeBus:
    def __init__(self):
        self.writes: list[tuple[str, dict[str, int]]] = []

    def sync_write(self, register: str, values: dict[str, int]) -> None:
        self.writes.append((register, dict(values)))


class _FakeRobot:
    def __init__(self):
        self.bus = _FakeBus()
        self.actions: list[dict[str, float]] = []

    def send_action(self, action: dict[str, float]) -> None:
        self.actions.append(dict(action))


class _FakeAnimationService:
    def __init__(self):
        self.bus_lock = threading.RLock()
        self.robot = _FakeRobot()
        self._hold_mode = False
        self._tracking_active = False
        self._running = threading.Event()
        self._running.set()
        self.dispatched: list[tuple[str, str]] = []
        self.idle_recording = "idle"
        self.positions = {
            "base_yaw.pos": 18.0,
            "base_pitch.pos": 7.0,
            "elbow_pitch.pos": -4.0,
            "wrist_roll.pos": 3.0,
            "wrist_pitch.pos": -12.0,
        }
        self._current_state = {"base_yaw.pos": -30.0}

    def dispatch(self, event: str, recording: str) -> None:
        self.dispatched.append((event, recording))

    def get_positions(self) -> dict[str, float]:
        return dict(self.positions)


class _FakeFollower:
    def __init__(self):
        self.joined = False
        self.goal_cleared = False

    def read_initial_positions(self, _service) -> None:
        pass

    def seed_goal_current(self) -> None:
        pass

    def start(self, _service, _running) -> None:
        pass

    def join(self, timeout: float) -> None:
        self.joined = timeout == 2.0

    def clear_goal(self) -> None:
        self.goal_cleared = True


class _FakePID:
    def reset(self) -> None:
        pass


class _NoFrameCamera:
    last_frame = None


def test_tracking_timeout_stops_even_when_camera_has_no_frames(monkeypatch):
    service = object.__new__(TrackerService)
    state = TrackingState(target_label="object")
    state.running.set()
    service._state = state
    service._follower = _FakeFollower()
    service._yaw_pid = _FakePID()
    service._pitch_pid = _FakePID()

    animation = _FakeAnimationService()
    clock = iter((0.0, C.MAX_TRACK_DURATION_S + 0.1, C.MAX_TRACK_DURATION_S + 0.1))
    monkeypatch.setattr(tracker_service.time, "perf_counter", lambda: next(clock))
    monkeypatch.setattr(tracker_service.time, "sleep", lambda _seconds: None)

    service._track_loop(_NoFrameCamera(), animation)

    assert not state.running.is_set()
    assert not animation._tracking_active
    assert not animation._hold_mode
    assert service._follower.joined
    assert service._follower.goal_cleared
    assert animation.robot.actions == []
    assert animation._current_state == animation.positions
    assert animation.dispatched == [("play", "idle")]


def test_tracking_timeout_releases_virtual_motors(monkeypatch):
    from hal.drivers.motors.mock_service import MockMotionService

    service = object.__new__(TrackerService)
    state = TrackingState(target_label="object")
    state.running.set()
    service._state = state
    service._follower = _FakeFollower()
    service._yaw_pid = _FakePID()
    service._pitch_pid = _FakePID()
    motion = MockMotionService()
    # Exercise the physical timeout contract even with an in-memory test bus.
    motion.tracking_fixed_camera = False
    motion.start()
    clock = iter((0.0, C.MAX_TRACK_DURATION_S + 0.1, C.MAX_TRACK_DURATION_S + 0.1))
    monkeypatch.setattr(tracker_service.time, "perf_counter", lambda: next(clock))
    try:
        service._track_loop(_NoFrameCamera(), motion)
        assert not state.running.is_set()
        assert not motion._tracking_active
        assert not motion._hold_mode
        assert all(v == 0 for v in motion.robot.bus.sync_read("Goal_Velocity").values())
        assert ("dispatch", "play", "idle") in motion.calls
    finally:
        motion.stop()


def test_fixed_camera_tracking_continues_past_physical_timeout(monkeypatch):
    from hal.drivers.motors.mock_service import MockMotionService

    service = object.__new__(TrackerService)
    state = TrackingState(target_label="person")
    state.running.set()
    service._state = state
    service._follower = _FakeFollower()
    service._yaw_pid = _FakePID()
    service._pitch_pid = _FakePID()
    reads = []

    class Camera:
        @property
        def last_frame(self):
            reads.append(True)
            state.running.clear()
            return None

    motion = MockMotionService()
    motion.start()
    clock = iter((0.0, C.MAX_TRACK_DURATION_S + 100))
    monkeypatch.setattr(tracker_service.time, "perf_counter", lambda: next(clock))
    try:
        service._track_loop(Camera(), motion)
        assert reads == [True]
        assert not motion._tracking_active
    finally:
        motion.stop()


def test_face_watch_retries_absence_and_loss_and_stops(monkeypatch):
    import time

    service = TrackerService()
    attempts = []
    acquired = threading.Event()
    lose_face = threading.Event()

    def acquire(bbox, label, camera, animation):
        attempts.append(label)
        if len(attempts) == 1:
            return False  # Empty room when the user first enables face tracking.
        state = TrackingState(target_label=label, bbox=(1, 2, 30, 40))
        state.running.set()
        service._state = state

        def track():
            acquired.set()
            while state.running.is_set() and not lose_face.wait(.01):
                pass
            state.running.clear()

        state.thread = threading.Thread(target=track)
        state.thread.start()
        return True

    monkeypatch.setattr(service, '_start_locked', acquire)
    try:
        assert service.start(target_label='face', camera_capture=object(), animation_service=object())
        assert service.status['tracking']
        assert service.status['searching']
        assert service.status['bbox'] is None
        assert acquired.wait(3)
        assert not service.status['searching']
        lose_face.set()
        deadline = time.monotonic() + 3
        while len(attempts) < 3 and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(attempts) >= 3  # Loss automatically triggers another acquisition.
        service.stop()
        count = len(attempts)
        time.sleep(.05)
        assert len(attempts) == count
        assert not service.is_tracking
        assert service.status['bbox'] is None
    finally:
        service.stop()


def test_object_tracking_remains_one_shot(monkeypatch):
    service = TrackerService()
    calls = []
    monkeypatch.setattr(service, '_start_locked', lambda *args: calls.append(args) or False)
    assert not service.start(target_label='cup', camera_capture=object(), animation_service=object())
    assert len(calls) == 1
    assert not service.is_tracking
