"""Camera settings validation, persistence, privacy, and subprocess arguments."""
import threading
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from hal.drivers.camera.models import VideoCaptureDeviceInfo
from hal.drivers.camera.rpicam_capture_device import RpicamVideoCaptureDevice
from hal.drivers.camera.rpicam_controls import RpicamControls


@pytest.fixture
def camera(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("HAL_RPICAM_CONTROLS_PATH", raising=False)
    return RpicamVideoCaptureDevice(VideoCaptureDeviceInfo(device_id="csi"))


@pytest.mark.parametrize("patch", [
    {"fps": 0}, {"fps": 41}, {"fps": True}, {"fps": "30"},
    {"exposure_compensation": 3}, {"shutter_us": -1}, {"gain": 17},
    {"metering": "custom"}, {"autofocus_mode": "off"}, {"lens_position": 33},
    {"awb": "invalid"}, {"flicker_hz": 55}, {"hdr": True}, {"unknown": 1},
])
def test_invalid_settings_do_not_mutate_or_persist(camera, patch):
    before = camera.get_controls()["settings"]
    with pytest.raises(ValidationError):
        camera.set_controls(patch)
    assert camera.get_controls()["settings"] == before
    assert not camera._controls_path.exists()
    assert not camera._rate_changed.is_set()


def test_partial_update_persists_and_does_not_enable_camera(camera):
    camera._spawn = Mock(side_effect=AssertionError("camera must stay disabled"))
    result = camera.set_controls({"exposure_compensation": 1.25, "fps": 25})
    assert result["settings"]["exposure_compensation"] == 1.25
    assert result["settings"]["fps"] == 25
    assert camera._thread is None
    camera._spawn.assert_not_called()
    restored = RpicamVideoCaptureDevice(VideoCaptureDeviceInfo(device_id="csi"))
    assert restored.get_controls()["settings"] == result["settings"]


def test_reset_and_same_rate_settings_signal_restart(camera):
    camera.set_controls({"metering": "spot"})
    assert camera._rate_changed.is_set()
    camera._rate_changed.clear()
    result = camera.set_controls({"reset": True})
    assert result["settings"] == result["defaults"]
    assert camera._rate_changed.is_set()
    with pytest.raises(ValueError):
        camera.set_controls({"reset": "true"})


def test_cli_settings_and_flicker_units(camera, monkeypatch):
    camera.set_controls({"flicker_hz": 60, "autofocus_mode": "manual", "lens_position": 2})
    popen = Mock()
    monkeypatch.setattr("hal.drivers.camera.rpicam_capture_device.subprocess.Popen", popen)
    camera._spawn(30)
    args = popen.call_args.args[0]
    assert args[args.index("--flicker-period") + 1] == "8333us"
    assert args[args.index("--lens-position") + 1] == "2.0"
    assert args[args.index("--framerate") + 1] == "30"
    assert "--lens-position" not in RpicamControls().arguments()


def test_idle_capture_does_not_exceed_selected_rate(camera):
    camera.set_controls({"fps": 2})
    assert camera._target_fps() == 2
    camera.acquire_consumer()
    assert camera._target_fps() == 2


def test_failed_persistence_leaves_active_settings_unchanged(camera, monkeypatch):
    before = camera.get_controls()["settings"]
    monkeypatch.setattr("hal.drivers.camera.rpicam_capture_device.os.replace", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        camera.set_controls({"fps": 20})
    assert camera.get_controls()["settings"] == before
    assert not camera._rate_changed.is_set()
    assert not list(camera._controls_path.parent.glob(".camera-controls-*"))


def test_settings_while_spawn_in_progress_are_not_lost(camera, monkeypatch):
    """A settings writer cannot clear its signal behind an in-flight spawn."""
    entered = threading.Event()
    finish = threading.Event()
    spawned = []
    class Process:
        stdout = None
        returncode = 0
        def poll(self):
            return 0
        def terminate(self):
            pass
        def wait(self, timeout):
            pass
    def spawn(fps):
        spawned.append(camera._settings.metering)
        if len(spawned) == 1:
            entered.set()
            assert finish.wait(2)
        else:
            camera._stopped.set()
        return Process()
    monkeypatch.setattr(camera, "_spawn", spawn)
    monkeypatch.setattr("hal.drivers.camera.rpicam_capture_device._RESTART_DELAY_S", 0)
    worker = threading.Thread(target=camera._capture_loop)
    worker.start()
    assert entered.wait(2)
    writer = threading.Thread(target=lambda: camera.set_controls({"metering": "spot"}))
    writer.start()
    finish.set()
    writer.join(2)
    worker.join(2)
    assert not writer.is_alive()
    assert not worker.is_alive()
    assert camera._settings.metering == "spot"
    # Either the next spawn observes the update or a pending event requests it.
    assert spawned[-1] == "spot" or camera._rate_changed.is_set()


def test_telemetry_measures_delivered_frames_and_reports_staleness(camera, monkeypatch):
    camera._frame_times.extend([10.0, 10.05, 10.1])
    camera._last_frame_monotonic = 10.1
    monkeypatch.setattr("hal.drivers.camera.rpicam_capture_device.time.monotonic", lambda: 10.2)
    result = camera.get_controls()
    assert result["measured_fps"] == 20
    assert result["frame_age_ms"] == 100
    assert result["requested_fps"] == 5
    monkeypatch.setattr("hal.drivers.camera.rpicam_capture_device.time.monotonic", lambda: 15)
    assert camera.get_controls()["measured_fps"] == 0


def test_routes_reject_invalid_requests_and_preserve_disabled_state(camera, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from hal.routes import camera as routes
    monkeypatch.setattr(routes.state, "camera_capture", camera)
    monkeypatch.setattr(routes.state, "_camera_disabled", True)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        assert client.get("/camera/controls").json()["supported"] is True
        assert client.post("/camera/controls", json={"fps": 999}).status_code == 422
        assert client.post("/camera/controls", json={"new_setting": 1}).status_code == 422
        response = client.post("/camera/controls", json={"metering": "spot"})
        assert response.status_code == 200
        assert response.json()["settings"]["metering"] == "spot"
        assert routes.state._camera_disabled is True
        assert camera._thread is None
        monkeypatch.setattr(routes.state, "camera_capture", object())
        assert client.get("/camera/controls").json()["supported"] is False
        assert client.post("/camera/controls", json={"fps": 30}).status_code == 400


def test_durable_default_and_explicit_path_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_RPICAM_CONTROLS_PATH", raising=False)
    monkeypatch.delenv("HAL_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    device = RpicamVideoCaptureDevice(VideoCaptureDeviceInfo(device_id="csi"))
    assert device._controls_path == tmp_path / "lamp/rpicam-controls.json"
    monkeypatch.setenv("HAL_RPICAM_CONTROLS_PATH", str(tmp_path / "explicit.json"))
    monkeypatch.setenv("HAL_STATE_DIR", str(tmp_path / "other"))
    device = RpicamVideoCaptureDevice(VideoCaptureDeviceInfo(device_id="csi"))
    assert device._controls_path == tmp_path / "explicit.json"


def test_stop_clears_frames_and_delivery_telemetry(camera):
    import numpy as np
    from hal.drivers.camera.models import VideoCaptureDeviceResponse
    camera.last_response = VideoCaptureDeviceResponse(frame=np.zeros((2, 2, 3), dtype=np.uint8))
    camera.stop()
    assert camera.last_frame is None
    assert camera.last_frame_ts == 0
    telemetry = camera.get_controls()
    assert telemetry["measured_fps"] == 0
    assert telemetry["frame_age_ms"] is None
