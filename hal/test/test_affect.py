"""Affect inheritance, transitions and safety ordering without hardware."""

import pytest

from hal.drivers.tracking.affect import AffectState
from hal.drivers.tracking import constants as C
from hal.drivers.tracking import tracker_service
from hal.drivers.tracking.tracker_service import TrackerService


def test_neutral_and_zero_intensity_preserve_baseline():
    affect = AffectState()
    for baseline in [(0.32, 55.0), (0.12, 100.0)]:
        assert affect.resolve(*baseline) == baseline
        affect.set("fearful", intensity=0, transition_s=0)
        assert affect.resolve(*baseline) == baseline


def test_transition_retarget_is_continuous_and_repeated_event_does_not_restart():
    now = [0.0]
    affect = AffectState(clock=lambda: now[0])
    affect.set("fearful", transition_s=2)
    assert affect.resolve(1, 1) == (1, 1)
    now[0] = 1
    halfway = affect.resolve(1, 1)
    assert halfway == pytest.approx((0.825, 1.125))
    affect.set("fearful", transition_s=2)
    now[0] = 2
    assert affect.resolve(1, 1) == pytest.approx((0.65, 1.25))
    affect.set("neutral", transition_s=2)
    now[0] = 3
    midway_home = affect.resolve(1, 1)
    affect.set("curious", transition_s=1)
    assert affect.resolve(1, 1) == midway_home
    now[0] = 4
    assert affect.resolve(1, 1) == pytest.approx((0.85, 1))
    assert not affect.status["transitioning"]


@pytest.mark.parametrize("kwargs", [
    {"name": "unknown"}, {"name": "curious", "intensity": -0.1},
    {"name": "curious", "intensity": 1.1},
    {"name": "curious", "intensity": float("nan")},
    {"name": "curious", "transition_s": float("inf")},
    {"name": "curious", "transition_s": -1},
])
def test_invalid_request_leaves_state_unchanged(kwargs):
    affect = AffectState()
    before = affect.status
    with pytest.raises(ValueError):
        affect.set(**kwargs)
    assert affect.status == before


@pytest.mark.parametrize("saccade", [False, True])
def test_tracker_inherits_live_baseline_and_caps_after_affect(monkeypatch, saccade):
    tracker = TrackerService()
    calls = []
    monkeypatch.setattr(tracker._follower, "set_profile", lambda *p: calls.append(p))
    requested = []

    def cap(policy, speed):
        requested.append(speed)
        return min(40.0, speed)

    monkeypatch.setattr(tracker_service, "cap_speed_dps", cap)
    smooth_key = "SACCADE_SMOOTH_TIME" if saccade else "SERVO_SMOOTH_TIME"
    speed_key = "SACCADE_MAX_SPEED_DPS" if saccade else "SERVO_MAX_SPEED_DPS"
    monkeypatch.setattr(C, smooth_key, 0.4)
    monkeypatch.setattr(C, speed_key, 50.0)
    tracker._apply_motion_profile(saccade)
    assert calls[-1] == (0.4, 40.0)
    tracker.set_affect("fearful", intensity=0.5, transition_s=0)
    tracker._apply_motion_profile(saccade)
    assert requested[-1] == 56.25
    assert calls[-1] == pytest.approx((0.33, 40.0))
    monkeypatch.setattr(C, smooth_key, 0.8)
    tracker._apply_motion_profile(saccade)
    assert calls[-1] == pytest.approx((0.66, 40.0))
    assert not tracker.is_tracking
    assert tracker._follower._goal is None
    assert tracker.status["affect"]["target"] == "fearful"
