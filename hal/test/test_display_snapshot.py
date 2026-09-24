"""LCD snapshots use exactly the framebuffer sent to the display driver."""

import io
import sys
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from hal.drivers.display import display_service
from hal.routes import display


@pytest.fixture
def service(monkeypatch):
    driver = mock.Mock()
    # Never import or initialize the real SPI/display hardware during tests.
    monkeypatch.setitem(sys.modules, "gc9a01", SimpleNamespace(GC9A01=lambda **_: driver))
    monkeypatch.setitem(sys.modules, "spidev", SimpleNamespace())
    return display_service.DisplayService()


@pytest.fixture
def client(service, monkeypatch):
    monkeypatch.setattr(display.state, "display_service", service)
    app = FastAPI()
    app.include_router(display.router)
    with TestClient(app) as test_client:
        yield test_client


def assert_matches_hardware(service, data):
    hardware_frame = service._driver.display.call_args.args[0]
    with Image.open(io.BytesIO(data)) as snapshot:
        assert snapshot.format == "PNG"
        assert snapshot.size == hardware_frame.size == (240, 240)
        assert snapshot.mode == hardware_frame.mode
        assert snapshot.tobytes() == hardware_frame.tobytes()


@pytest.mark.parametrize("expression", display_service.EXPRESSIONS)
@pytest.mark.parametrize("openness", [1.0, 0.05])
def test_png_matches_eye_and_blink_frame_sent_to_hardware(service, expression, openness):
    service.set_expression(expression, pupil_x=0.75, pupil_y=-0.5)
    service._eye_state.openness = openness
    service._render()

    assert_matches_hardware(service, service.get_frame_png_bytes())


def test_png_endpoint_matches_info_frame_and_disables_caching(service, client):
    service.set_info("12:34", "Ready")
    service._render()

    response = client.get("/display/frame.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"
    assert_matches_hardware(service, response.content)


def test_snapshot_does_not_render_pending_state(service, client):
    service._render()
    previous = service.get_frame_png_bytes()
    service.set_info("Pending", "Not rendered yet")

    with mock.patch.object(display_service, "render_eye") as eye_renderer, \
            mock.patch.object(display_service, "render_info") as info_renderer:
        assert client.get("/display/frame.png").content == previous
        eye_renderer.assert_not_called()
        info_renderer.assert_not_called()
    assert service._driver.display.call_count == 1
    assert service._dirty


def test_existing_jpeg_endpoint_remains_compatible(service, client):
    service._render()

    response = client.get("/display/snapshot")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == service.get_snapshot_bytes()
    with Image.open(io.BytesIO(response.content)) as snapshot:
        assert snapshot.format == "JPEG"
        assert snapshot.size == (240, 240)


def test_snapshot_before_first_render(service, client):
    assert service.get_frame_png_bytes() is None
    assert service.get_snapshot_bytes() is None
    assert client.get("/display/frame.png").status_code == 404


def test_snapshot_when_display_unavailable(client, monkeypatch):
    monkeypatch.setattr(display.state, "display_service", None)
    assert client.get("/display/frame.png").status_code == 503


def test_framebuffer_only_mode_never_initializes_hardware(monkeypatch):
    constructor = mock.Mock(side_effect=AssertionError("Hardware must not initialize"))
    monkeypatch.setitem(sys.modules, "gc9a01", SimpleNamespace(GC9A01=constructor))
    monkeypatch.setitem(sys.modules, "spidev", SimpleNamespace())

    service = display_service.DisplayService(hardware_enabled=False)
    service._render()

    constructor.assert_not_called()
    assert service.get_state()["hardware"] is False
    with Image.open(io.BytesIO(service.get_frame_png_bytes())) as snapshot:
        assert snapshot.format == "PNG"
        assert snapshot.size == (240, 240)
        assert snapshot.tobytes() == service._last_frame.tobytes()
