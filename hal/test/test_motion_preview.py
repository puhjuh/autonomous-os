"""Pi preview provenance and affect validation without physical actuation."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal.routes import servo
from hal.drivers.motors.mock_service import MockMotionService
from hal.drivers.tracking.affect import AffectState, STYLES


@pytest.fixture
def preview(monkeypatch):
    motion = MockMotionService()
    motion.start()
    affect = AffectState()

    class Tracker:
        @property
        def status(self):
            return {"tracking": False, "affect": affect.status}

        def set_affect(self, *args):
            affect.set(*args)

    monkeypatch.setattr(servo.state, "animation_service", motion)
    monkeypatch.setattr(servo.state, "tracker_service", Tracker())
    app = FastAPI()
    app.include_router(servo.router)
    yield TestClient(app), motion
    motion.stop()


def test_output_is_pi_state_not_motor_feedback(preview):
    client, motion = preview
    joint = next(iter(motion.get_joint_names()))
    motion.send_positions({joint: 12.5})
    output = client.get("/servo/output").json()
    assert output["positions"][joint] == 12.5
    assert output["driver"] == "mock"
    assert output["position_kind"] == "simulated"
    assert output["physical_feedback_verified"] is False
    assert output["units"] == "degrees"


def test_style_selection_does_not_start_tracking_or_move(preview):
    client, motion = preview
    before = motion.get_positions()
    calls = list(motion.calls)
    for name in STYLES:
        response = client.post("/servo/affect", json={"name": name, "transition_s": 0})
        assert response.status_code == 200
        assert response.json()["affect"]["target"] == name
    assert motion.get_positions() == before
    assert motion.calls == calls
    assert client.get("/servo/output").json()["tracking"]["tracking"] is False


@pytest.mark.parametrize("body", [{"name": "bad"}, {"name": "calm", "intensity": 2},
                                  {"name": "happy", "transition_s": -1}])
def test_invalid_styles_do_not_mutate(preview, body):
    client, _ = preview
    before = client.get("/servo/affect").json()
    assert client.post("/servo/affect", json=body).status_code == 422
    assert client.get("/servo/affect").json() == before
