from __future__ import annotations

from typing import Any

from backend.core.defaults import default_config
from backend.services.telemetry_hub import TelemetryHub


class FakeSettings:
    def __init__(self) -> None:
        self.config = default_config()
        self.config["hal"]["mode"] = "real"
        self.config["teleop"]["engine"] = "hal_native"

    def get_config(self) -> dict[str, Any]:
        return self.config


class FakeForce:
    def sample(self, _config: dict[str, Any]) -> object:
        raise RuntimeError("NI-DAQ resource reserved")


class FakeCameras:
    def probe(self, _config: dict[str, Any]) -> list[object]:
        return []


class FakeHardware:
    force = FakeForce()
    cameras = FakeCameras()


def test_real_hal_ok_is_not_reported_faulted_when_force_probe_is_unavailable() -> None:
    telemetry = TelemetryHub(FakeSettings(), FakeHardware())
    try:
        telemetry.force_ok = False

        frame = telemetry.next_frame(hal_ok=True)

        assert frame.halOk is True
    finally:
        telemetry.shutdown()


def test_hal_native_gripper_positions_do_not_fall_back_to_targets_when_feedback_is_missing() -> None:
    settings = FakeSettings()
    settings.config["gripper"]["targetLeftMm"] = 26.0
    settings.config["gripper"]["targetRightMm"] = 4.5
    telemetry = TelemetryHub(settings, FakeHardware())
    try:
        frame = telemetry.next_frame(hal_ok=True)

        assert frame.gripperPositions == [-1.0, -1.0]
        assert telemetry.gripper_samples == {}
    finally:
        telemetry.shutdown()
