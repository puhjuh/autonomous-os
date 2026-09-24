"""Validated libcamera controls shared by the camera driver and HTTP API."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
