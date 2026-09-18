# 阅读导航 04｜后端业务与采集
# 职责：聚合相机、NI-DAQ、夹爪与 PICO 的 Python 驱动实例，并提供硬件状态查询。
# 先看：HardwareService。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

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
        self.force = NidaqForceDriver(logs)
        self.gripper = Rs485GripperDriver()
        self.pico = PicoAdbDriver()

    def status(self, *, include_gripper: bool = True) -> dict[str, Any]:
        config = self.settings.get_config()
        camera = self.cameras.probe(config)
        force_source = str(config.get("force", {}).get("source", "hkvl_serial")).lower()
        force = self.force.probe(config) if force_source == "nidaq" else None
        gripper = self.gripper.probe(config) if include_gripper else None
        pico = self.pico.status(config)
        return {
            "camera": {
                "ok": camera.ok,
                "message": camera.message,
                "cameras": [item.model_dump(mode="json") for item in camera.cameras],
            },
            "force": (
                {"ok": force.ok, "message": force.message, "source": "nidaq"}
                if force is not None
                else {
                    "ok": None,
                    "message": "managed by HAL-native HKVL serial runtime",
                    "source": "hkvl_serial",
                }
            ),
            "gripper": (
                {
                    "ok": gripper.ok,
                    "message": gripper.message,
                    "details": gripper.details,
                    "ports": gripper.details.get("ports", []),
                }
                if gripper is not None
                else {"ok": None, "message": "managed by HAL-native gripper"}
            ),
            "pico": {"ok": pico.ok, "message": pico.message, "stdout": pico.stdout, "stderr": pico.stderr},
        }
