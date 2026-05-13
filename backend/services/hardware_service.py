from __future__ import annotations

from typing import Any

from backend.core.config import SettingsService
from backend.core.logging import LogService
from backend.drivers.camera_opencv import OpenCVCameraDriver
from backend.drivers.force_nidaq import NidaqForceDriver
from backend.drivers.gripper_rs485 import Rs485GripperDriver
from backend.drivers.pico_adb import PicoAdbDriver


class HardwareService:
    def __init__(self, settings: SettingsService, logs: LogService) -> None:
        self.settings = settings
        self.logs = logs
        self.cameras = OpenCVCameraDriver(logs)
        self.force = NidaqForceDriver()
        self.gripper = Rs485GripperDriver()
        self.pico = PicoAdbDriver()

    def status(self, *, include_gripper: bool = True) -> dict[str, Any]:
        config = self.settings.get_config()
        camera = self.cameras.probe(config)
        force = self.force.probe(config)
        gripper = self.gripper.probe(config) if include_gripper else None
        pico = self.pico.status(config)
        return {
            "camera": {
                "ok": camera.ok,
                "message": camera.message,
                "cameras": [item.model_dump(mode="json") for item in camera.cameras],
            },
            "force": {"ok": force.ok, "message": force.message},
            "gripper": (
                {"ok": gripper.ok, "message": gripper.message}
                if gripper is not None
                else {"ok": None, "message": "managed by gripper workers"}
            ),
            "pico": {"ok": pico.ok, "message": pico.message, "stdout": pico.stdout, "stderr": pico.stderr},
        }
