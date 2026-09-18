from types import SimpleNamespace

from backend.core.defaults import default_config
from backend.services.hardware_service import HardwareService


def test_record_probe_does_not_wait_for_pico_and_keeps_camera_failure() -> None:
    hardware = object.__new__(HardwareService)
    hardware.settings = SimpleNamespace(get_config=default_config)
    hardware.cameras = SimpleNamespace(probe=lambda _config: SimpleNamespace(
        ok=False, message="camera unavailable", cameras=[],
    ))

    def unexpected_probe(_config):
        raise AssertionError("录制预检查不应调用 PICO 或 Python 夹爪")

    hardware.pico = SimpleNamespace(status=unexpected_probe)
    hardware.gripper = SimpleNamespace(probe=unexpected_probe)
    status = hardware.status(include_gripper=False, include_pico=False)
    assert status["camera"] == {"ok": False, "message": "camera unavailable", "cameras": []}
    assert status["pico"]["ok"] is None
