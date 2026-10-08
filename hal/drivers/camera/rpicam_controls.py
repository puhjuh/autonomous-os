"""Validated libcamera controls shared by the camera driver and HTTP API."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RpicamControls(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fps: int = Field(default=30, ge=1, le=40)
    exposure_compensation: float = Field(default=0, ge=-2, le=2)
    exposure_mode: Literal["normal", "sport"] = "normal"
    metering: Literal["centre", "spot", "average"] = "centre"
    shutter_us: int = Field(default=0, ge=0, le=100000)
    gain: float = Field(default=0, ge=0, le=16)
    autofocus_mode: Literal["continuous", "auto", "manual"] = "continuous"
    lens_position: float = Field(default=1, ge=0, le=32)
    awb: Literal["auto", "indoor", "daylight"] = "auto"
    flicker_hz: Literal[0, 50, 60] = 0
    hdr: Literal["off", "auto"] = "off"
    # Mount correction: clockwise rotation, then flips in the upright image.
    rotation: Literal[0, 90, 180, 270] = 0
    hflip: bool = False
    vflip: bool = False

    @field_validator("rotation", mode="before")
    @classmethod
    def validate_rotation(cls, value):
        if type(value) is not int:
            raise ValueError("rotation must be an integer: 0, 90, 180, or 270")
        return value

    def orient_frame(self, cv2, frame, base_rotation: float | None = None):
        """Correct the shared frame before zoom, tracking, or perception."""
        rotation = (self.rotation + (base_rotation or 0)) % 360
        rotations = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                     270: cv2.ROTATE_90_COUNTERCLOCKWISE}
        if rotation in rotations:
            frame = cv2.rotate(frame, rotations[rotation])
        if self.hflip or self.vflip:
            frame = cv2.flip(frame, -1 if self.hflip and self.vflip else 1 if self.hflip else 0)
        return frame

    def arguments(self) -> list[str]:
        values = [
            ("--ev", self.exposure_compensation),
            ("--exposure", self.exposure_mode),
            ("--metering", self.metering),
            ("--shutter", self.shutter_us),
            ("--gain", self.gain),
            ("--autofocus-mode", self.autofocus_mode),
            ("--awb", self.awb),
            ("--flicker-period", "0us" if not self.flicker_hz else f"{round(1_000_000 / (2 * self.flicker_hz))}us"),
            ("--hdr", self.hdr),
        ]
        if self.autofocus_mode == "manual":
            values.append(("--lens-position", self.lens_position))
        return [str(value) for pair in values for value in pair]
