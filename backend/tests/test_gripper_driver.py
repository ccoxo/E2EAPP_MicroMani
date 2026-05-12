from __future__ import annotations

from pytest import MonkeyPatch

from backend.core.defaults import default_config
from backend.drivers.gripper_rs485 import Rs485GripperDriver


def test_jodell_gripper_target_position_mapping() -> None:
    driver = Rs485GripperDriver()

    assert driver._motion_params("open", None, 26) == (0, 10, 1, 26)
    assert driver._motion_params("close", None, 26) == (255, 10, 1, 0)
    assert driver._motion_params("target", 13, 26) == (128, 10, 1, 13)


def test_jodell_gripper_default_config_matches_reference_project() -> None:
    config = default_config()

    assert config["gripper"]["leftPort"] == "COM8"
    assert config["gripper"]["rightPort"] == "COM9"
    assert config["gripper"]["leftSlaveId"] == 10
    assert config["gripper"]["rightSlaveId"] == 9
    assert config["gripper"]["commandSpeed"] == 10
    assert config["gripper"]["commandTorque"] == 1
    assert config["gripper"]["sampleMode"] == "dual_worker"
    assert config["gripper"]["sampleHz"] == 30
    assert config["gripper"]["jodellDllPath"].endswith("backend/vendor/jodell/jodellTool.dll")


def test_gripper_selects_and_closes_configured_port_per_command(monkeypatch: MonkeyPatch) -> None:
    config = default_config()
    driver = Rs485GripperDriver()

    class FakeDll:
        def __init__(self) -> None:
            self.serial_calls: list[tuple[int, int, bool]] = []

        def serialOperation(self, port: int, baudrate: int, status: bool) -> int:
            self.serial_calls.append((port, baudrate, status))
            return 1

        def clawEnable(self, slave: int, status: bool) -> int:
            _ = (slave, status)
            return 1

        def getClawCurrentLocation(self, slave: int) -> int:
            _ = slave
            return 128

    fake = FakeDll()
    monkeypatch.setattr(driver, "_load_dll", lambda active_config: fake)

    assert driver.command(config, "left", "enable", None).ok is True
    assert driver.command(config, "right", "enable", None).ok is True
    assert driver.position(config, "right").ok is True
    assert driver.command(config, "right", "disable", None).ok is True

    assert fake.serial_calls == [
        (8, 115200, True),
        (8, 115200, False),
        (9, 115200, True),
        (9, 115200, False),
    ]


def test_gripper_diagnose_checks_port_enable_and_position_without_motion(monkeypatch: MonkeyPatch) -> None:
    config = default_config()
    driver = Rs485GripperDriver()

    class FakeDll:
        def __init__(self) -> None:
            self.serial_calls: list[tuple[int, int, bool]] = []
            self.motion_calls = 0

        def serialOperation(self, port: int, baudrate: int, status: bool) -> int:
            self.serial_calls.append((port, baudrate, status))
            return 1

        def clawEnable(self, slave: int, status: bool) -> int:
            _ = (slave, status)
            return 1

        def getClawCurrentLocation(self, slave: int) -> int:
            _ = slave
            return 128

        def runWithParam(self, slave: int, pos: int, speed: int, torque: int) -> int:
            _ = (slave, pos, speed, torque)
            self.motion_calls += 1
            return 1

    fake = FakeDll()
    monkeypatch.setattr(driver, "_load_dll", lambda active_config: fake)

    result = driver.diagnose(config, "right")

    assert result.ok is True
    assert result.details["port"] == "COM9"
    assert result.details["positionRaw"] == 128
    assert fake.serial_calls == [(9, 115200, True)]
    assert fake.motion_calls == 0


def test_gripper_command_rejects_motion_when_disabled(monkeypatch: MonkeyPatch) -> None:
    config = default_config()
    config["gripper"]["leftEnabled"] = False
    driver = Rs485GripperDriver()

    def fail_load(active_config: dict[str, object]) -> object:
        _ = active_config
        raise AssertionError("disabled motion command should not load vendor DLL")

    monkeypatch.setattr(driver, "_load_dll", fail_load)

    result = driver.command(config, "left", "open", None)

    assert result.ok is False
    assert "left gripper is disabled" in result.message


def test_gripper_position_does_not_auto_enable_disabled_side(monkeypatch: MonkeyPatch) -> None:
    config = default_config()
    config["gripper"]["leftEnabled"] = False
    config["gripper"]["sampleEnableOnNegative"] = True
    driver = Rs485GripperDriver()

    class FakeDll:
        def __init__(self) -> None:
            self.enable_calls: list[tuple[int, bool]] = []

        def serialOperation(self, port: int, baudrate: int, status: bool) -> int:
            _ = (port, baudrate, status)
            return 1

        def clawEnable(self, slave: int, status: bool) -> int:
            self.enable_calls.append((slave, status))
            return 1

        def getClawCurrentLocation(self, slave: int) -> int:
            _ = slave
            return -1

    fake = FakeDll()
    monkeypatch.setattr(driver, "_load_dll", lambda active_config: fake)

    result = driver.position(config, "left")

    assert result.ok is False
    assert fake.enable_calls == []


def test_gripper_position_may_auto_enable_enabled_side_on_negative_read(monkeypatch: MonkeyPatch) -> None:
    config = default_config()
    config["gripper"]["rightEnabled"] = True
    config["gripper"]["sampleEnableOnNegative"] = True
    driver = Rs485GripperDriver()

    class FakeDll:
        def __init__(self) -> None:
            self.enable_calls: list[tuple[int, bool]] = []
            self.reads = 0

        def serialOperation(self, port: int, baudrate: int, status: bool) -> int:
            _ = (port, baudrate, status)
            return 1

        def clawEnable(self, slave: int, status: bool) -> int:
            self.enable_calls.append((slave, status))
            return 1

        def getClawCurrentLocation(self, slave: int) -> int:
            _ = slave
            self.reads += 1
            return -1 if self.reads == 1 else 255

    fake = FakeDll()
    monkeypatch.setattr(driver, "_load_dll", lambda active_config: fake)

    result = driver.position(config, "right")

    assert result.ok is True
    assert result.position_mm == 0
    assert fake.enable_calls == [(9, True)]
