"""Camera driver for Raspberry Pi CSI sensors driven by libcamera.

Why this exists: `LocalVideoCaptureDevice` opens a V4L2 node with OpenCV, which
works for UVC webcams (Lamp) but not for a CSI sensor behind Raspberry Pi's
unicam + libcamera pipeline. On a Reachy Mini the imx708 exposes /dev/video0 as
a raw Bayer node: `cv2.VideoCapture(0)` reports isOpened() == True and then every
read() times out, and the wheel-built `opencv-python` is compiled with
`GStreamer: NO`, so a `libcamerasrc` pipeline is not available either.

Approach: run `rpicam-vid` (rpicam-apps, shipped by Raspberry Pi OS and present
on Pollen OS) as a child process emitting MJPEG on stdout, split the stream on
JPEG markers, and decode the newest frame with cv2.imdecode. That keeps the
libcamera ISP — exposure, white balance, lens shading — tuned by the vendor,
and needs no Python binding that must match the interpreter ABI (python3-picamera2
is built for the system interpreter, which is not the one HAL's venv runs).

Measured on a Reachy Mini Wireless (CM4, daemon running its control loop):
1280x720 MJPEG at 15 fps requested delivers ~14 fps for ~21% of one core.

The device must own the camera before this starts: on Reachy the Pollen daemon
holds it until `POST /api/media/release`.
"""
from __future__ import annotations

import logging
import select
import tempfile
from collections import deque
from pathlib import Path
import os
import shutil
import subprocess
import threading
import time
from typing import override

import numpy as np
import numpy.typing as npt

from .rpicam_controls import RpicamControls
from .models import VideoCaptureDeviceInfo, VideoCaptureDeviceResponse
from .video_capture_device import VideoCaptureDeviceBase

# JPEG frame delimiters. MJPEG over a pipe is just concatenated JPEGs, so the
# reader re-syncs on these rather than trusting any framing.
_SOI = b"\xff\xd8\xff"
_EOI = b"\xff\xd9"

_READ_CHUNK = 65536
# Discard a partial buffer that never terminates — a truncated frame must not
# grow without bound if the child wedges mid-write.
_MAX_BUFFER = 8 * 1024 * 1024
# Restart backoff when the child exits (camera taken away, ISP fault, OOM).
_RESTART_DELAY_S = 2.0
# No frame for this long with the child still alive = wedged pipeline; respawn.
_STALL_RESTART_S = 10.0

logger = logging.getLogger(__name__)


class RpicamVideoCaptureDevice(VideoCaptureDeviceBase):
    """MJPEG-over-pipe capture from `rpicam-vid`, for CSI/libcamera sensors."""

    runable: bool = True
    # libcamera selects the sensor through its own pipeline; there is no V4L2
    # index to resolve (and /dev/video0 here is a raw Bayer node that would only
    # mislead). See VideoCaptureDeviceBase.requires_v4l2_index.
    requires_v4l2_index: bool = False

    # Idle frame rate. Sensing polls every couple of seconds and the tracker is
    # the only fast consumer, so the child runs slow until someone calls
    # acquire_consumer() — same throttling contract the OpenCV driver honours,
    # except here the rate is a child-process argument, so changing it means
    # respawning rather than sleeping in the read loop.
    _IDLE_FPS: int = 5
    _ACTIVE_FPS: int = 30

    def __init__(
        self,
        device_info: VideoCaptureDeviceInfo,
        name: str | None = None,
    ):
        super().__init__(device_info, name)

        self._last_response: VideoCaptureDeviceResponse | None = None
        self._last_frame_monotonic: float = 0.0

        self._thread: threading.Thread | None = None
        self._lock: threading.Lock = threading.Lock()
        self._stopped: threading.Event = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._process_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._settings_lock = threading.RLock()
        self._frame_times = deque(maxlen=90)
        state_dir = Path(os.environ.get("HAL_STATE_DIR") or
                         str(Path(os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")) / "lamp"))
        self._controls_path = Path(os.environ.get("HAL_RPICAM_CONTROLS_PATH") or
                                   str(state_dir / "rpicam-controls.json"))
        self._settings = RpicamControls()
        try:
            self._settings = RpicamControls.model_validate_json(self._controls_path.read_text())
        except FileNotFoundError:
            pass
        except (ValueError, OSError) as exc:
            logger.warning("Ignoring invalid saved camera controls: %s", exc)

        # Persisted controls define the active delivery rate.
        self._active_fps = self._settings.fps
        self._active_consumers: int = 0
        self._consumers_lock: threading.Lock = threading.Lock()
        # Set when the consumer count crosses 0 so the loop respawns the child
        # at the other frame rate instead of waiting for the next fault.
        self._rate_changed: threading.Event = threading.Event()

        self.zoom: float = 1.0
        self.actual_width: int | None = None
        self.actual_height: int | None = None
        self.actual_fps: float | None = None

        self._logger: logging.Logger = logging.getLogger(self.__class__.__name__)

    # --- state exposed to consumers (mirrors LocalVideoCaptureDevice) --------

    @property
    def last_frame(self) -> npt.NDArray[np.uint8] | None:
        with self._lock:
            if self._last_response and self._last_response.frame is not None:
                return self._last_response.frame.copy()
            return None

    @property
    def last_frame_ts(self) -> float:
        """Monotonic capture time of last_frame (0.0 until the first frame)."""
        with self._lock:
            return self._last_frame_monotonic

    @property
    def last_frame_description(self) -> str | None:
        with self._lock:
            return self._last_response.frame_description if self._last_response else None

    @property
    def last_response(self) -> VideoCaptureDeviceResponse | None:
        with self._lock:
            return self._last_response.model_copy(deep=True) if self._last_response else None

    @last_response.setter
    def last_response(self, new_frame_info: VideoCaptureDeviceResponse | None):
        with self._lock:
            if new_frame_info:
                self._last_response = new_frame_info.model_copy(deep=True)
                self._last_frame_monotonic = time.monotonic()
                self._frame_times.append(self._last_frame_monotonic)
            else:
                self._last_response = None
                self._last_frame_monotonic = 0.0

    def acquire_consumer(self):
        """Register an active consumer (e.g. MJPEG stream) for full-FPS capture."""
        with self._consumers_lock:
            self._active_consumers += 1
            crossed = self._active_consumers == 1
        if crossed:
            self._rate_changed.set()

    def release_consumer(self):
        """Unregister an active consumer — throttles capture when none remain."""
        with self._consumers_lock:
            self._active_consumers = max(0, self._active_consumers - 1)
            crossed = self._active_consumers == 0
        if crossed:
            self._rate_changed.set()

    def get_controls(self) -> dict:
        with self._settings_lock:
            settings = self._settings.model_dump()
        with self._lock:
            times = list(self._frame_times)
            last = self._last_frame_monotonic
        now = time.monotonic()
        fresh = not self._stopped.is_set() and last > 0 and now - last < 2
        measured = ((len(times) - 1) / (times[-1] - times[0])
                    if fresh and len(times) > 1 and times[-1] > times[0] else 0.0)
        return {"supported": True, "settings": settings,
                "defaults": RpicamControls().model_dump(),
                "requested_fps": self._target_fps(), "measured_fps": round(measured, 2),
                "frame_age_ms": round((now - last) * 1000, 1) if last else None}

    def set_controls(self, patch: dict) -> dict:
        patch = dict(patch)
        reset = patch.pop("reset", False)
        if not isinstance(reset, bool):
            raise ValueError("reset must be a boolean")
        with self._settings_lock:
            values = RpicamControls().model_dump() if reset else self._settings.model_dump()
            settings = RpicamControls.model_validate(values | patch)
            self._controls_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=self._controls_path.parent, prefix=".camera-controls-")
            try:
                with os.fdopen(fd, "w") as output:
                    output.write(settings.model_dump_json())
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self._controls_path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            self._settings = settings
            with self._consumers_lock:
                self._active_fps = settings.fps
            # The capture thread alone spawns children. A stopped camera stays stopped.
            self._rate_changed.set()
        return self.get_controls()

    # --- lifecycle ----------------------------------------------------------

    @override
    def capture(
        self, need_description: bool = False
    ) -> VideoCaptureDeviceResponse | None:
        if self._thread is None:
            msg = f"{self.__class__.__name__} has not started"
            self._logger.info(msg)
            raise RuntimeError(msg)
        return self.last_response

    @override
    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                self._logger.info(f"{self.__class__.__name__} has already started")
                return
            if not shutil.which(self._binary()):
                raise RuntimeError(
                    f"{self._binary()} not found — install rpicam-apps (or libcamera-apps)"
                )
            self._stopped.clear()
            self._thread = threading.Thread(
                target=self._capture_loop,
                name=f"{self.__class__.__name__} capture loop",
                daemon=True,
            )
            self._thread.start()

    @override
    def stop(self):
        with self._lifecycle_lock:
            super().stop()
            self._stopped.set()
            self._kill_child()
            if self._thread is not None:
                self._thread.join(timeout=5)
                if not self._thread.is_alive():
                    self._thread = None
            with self._lock:
                self._last_response = None
                self._last_frame_monotonic = 0.0
                self._frame_times.clear()

    # --- internals ----------------------------------------------------------

    @staticmethod
    def _binary() -> str:
        # rpicam-apps renamed the binaries; older images still ship libcamera-vid.
        return "rpicam-vid" if shutil.which("rpicam-vid") else "libcamera-vid"

    def _target_fps(self) -> int:
        with self._consumers_lock:
            return self._active_fps if self._active_consumers > 0 else min(self._IDLE_FPS, self._active_fps)

    def _spawn(self, fps: int) -> subprocess.Popen:
        width = self._max_width or 1280
        height = self._max_height or 720
        cmd = [
            self._binary(),
            "--codec", "mjpeg",
            "--width", str(width),
            "--height", str(height),
            "--framerate", str(fps),
            # 0 = run until killed. Without it rpicam-vid stops after 5s.
            "-t", "0",
            "--nopreview",
            "-o", "-",
        ]
        cmd += self._settings.arguments()
        if self._rotate in (90, 180, 270):
            cmd += ["--rotation", str(int(self._rotate))]
        self._logger.info("starting %s at %dx%d@%dfps", cmd[0], width, height, fps)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
        )
        self.actual_width, self.actual_height, self.actual_fps = width, height, float(fps)
        return proc

    def _kill_child(self) -> None:
        with self._process_lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass

    def _capture_loop(self) -> None:
        import cv2  # imported here so the module loads on machines without cv2

        buf = bytearray()
        while not self._stopped.is_set():
            fps = self._target_fps()
            try:
                with self._settings_lock, self._process_lock:
                    if self._stopped.is_set():
                        break
                    self._rate_changed.clear()
                    fps = self._target_fps()
                    proc = self._spawn(fps)
                    self._proc = proc
                with self._lock:
                    self._frame_times.clear()
            except Exception as e:
                self._logger.warning("spawn failed: %s", e)
                self._stopped.wait(_RESTART_DELAY_S)
                continue

            buf.clear()
            last_frame_at = time.monotonic()

            while not self._stopped.is_set():
                # Camera controls and frame rate are child-process launch arguments.
                if self._rate_changed.is_set():
                    self._logger.info("camera settings or consumer count changed — restarting capture")
                    break
                if proc.poll() is not None:
                    self._logger.warning(
                        "%s exited (rc=%s) — restarting", self._binary(), proc.returncode
                    )
                    break
                if not proc.stdout:
                    break
                readable, _, _ = select.select([proc.stdout], [], [], 0.25)
                if not readable:
                    if time.monotonic() - last_frame_at > _STALL_RESTART_S:
                        self._logger.warning("Camera capture stalled — restarting")
                        break
                    continue
                chunk = os.read(proc.stdout.fileno(), _READ_CHUNK)
                if not chunk:
                    self._logger.warning("capture pipe closed — restarting")
                    break
                buf += chunk

                # Drain every complete JPEG in the buffer, keeping only the last:
                # decoding intermediate frames would burn CPU on images nobody reads.
                newest: bytes | None = None
                while True:
                    start = buf.find(_SOI)
                    if start < 0:
                        break
                    end = buf.find(_EOI, start + len(_SOI))
                    if end < 0:
                        break
                    newest = bytes(buf[start : end + len(_EOI)])
                    del buf[: end + len(_EOI)]

                if newest is not None:
                    frame = cv2.imdecode(np.frombuffer(newest, np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None:
                        if self.zoom and self.zoom > 1.0:
                            frame = self._apply_zoom(cv2, frame, self.zoom)
                        self.last_response = VideoCaptureDeviceResponse(frame=frame)
                        last_frame_at = time.monotonic()
                elif len(buf) > _MAX_BUFFER:
                    self._logger.warning("no JPEG boundary in %d bytes — resyncing", len(buf))
                    buf.clear()

                # Alive but silent: libcamera can wedge with the process still up,
                # which no exit code reports. Treat prolonged silence as a fault.
                if time.monotonic() - last_frame_at > _STALL_RESTART_S:
                    self._logger.warning("no frame for %.0fs — restarting", _STALL_RESTART_S)
                    break

            self._kill_child()
            if not self._stopped.is_set() and not self._rate_changed.is_set():
                self._stopped.wait(_RESTART_DELAY_S)

    @staticmethod
    def _apply_zoom(cv2, frame: npt.NDArray[np.uint8], zoom: float) -> npt.NDArray[np.uint8]:
        """Centre crop by `zoom` then scale back, so consumers see one FOV."""
        h, w = frame.shape[:2]
        cw, ch = int(w / zoom), int(h / zoom)
        x, y = (w - cw) // 2, (h - ch) // 2
        return cv2.resize(frame[y : y + ch, x : x + cw], (w, h), interpolation=cv2.INTER_LINEAR)
