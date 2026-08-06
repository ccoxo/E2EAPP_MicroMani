from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from backend.core.config import SettingsService
from backend.core.defaults import (
    ICF_HOME_REFERENCE_VERSION,
    ICF_ROTATION_MECHANICAL_LIMIT_CONFIG,
    ICF_TELEOP_STRATEGY_VERSION,
    ICF_WORK_ORIGIN_VERSION,
    default_config,
)
from backend.core.logging import LogService
from backend.core.motion_limits import (
    effective_limits_ui,
    pulse_to_axis_ui,
    rotation_work_limits_ui,
    side_home_reference_ui,
    side_origin_ui,
)
from backend.services.teleop_mapping import TeleopMappingService


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class FakeHal:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self.native_status: dict[str, Any] = {
            "running": True,
            "lastAction": {
                "side": "right",
                "sourceSide": "left",
                "launchDeltaPulse": [0.0, 0.0, 0.0, 0.0, 0.0, -333.0],
                "updateReturn": [0.0, 0.0, 0.0, 0.0, 0.0, 21.0],
                "moveStarted": [False, False, False, False, False, True],
            },
            "actionHistory": [],
            "blockers": {},
        }

    async def command(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.commands.append((name, payload))
        if name == "teleop.native.status":
            return {"mode": "real", "command": name, "response": self.native_status}
        return {"ok": True}

    async def health(self) -> Any:
        return type(
            "Health",
            (),
            {"connected": False, "ltdmc_ok": False, "omega7_ok": False},
        )()

    async def omega_state(self) -> dict[str, Any]:
        return {"hands": []}

    async def motion_state(self) -> dict[str, Any]:
        return {
            "pulses": [0.0] * 12,
            "enabled": [True] * 12,
            "estop_active": False,
        }


class FakeSettings:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def get_config(self) -> dict[str, Any]:
        return self.config


class GuardedSettings(FakeSettings):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.calls = 0

    def get_config(self) -> dict[str, Any]:
        self.calls += 1
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self.config
        raise AssertionError("teleop mapper read config on the event loop")


def _non_status_commands(commands: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    return [(name, payload) for name, payload in commands if name != "teleop.native.status"]


class FailingNativeStopHal(FakeHal):
    async def command(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.commands.append((name, payload))
        if name == "teleop.native.stop":
            raise RuntimeError("HAL connection refused")
        return await super().command(name, payload)


class StatusTimeoutHal(FakeHal):
    async def command(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.commands.append((name, payload))
        if name == "teleop.native.status":
            raise RuntimeError("HAL request failed: /teleop/native/status: timed out")
        return {"mode": "real", "command": name, "response": {}}


class NativeStartTimeoutHal(FakeHal):
    async def command(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.commands.append((name, payload))
        if name == "teleop.native.start":
            raise RuntimeError("HAL request failed: /teleop/native/start: timed out")
        return {"mode": "real", "command": name, "response": {}}


class DriftedMotionStateHal(FakeHal):
    async def motion_state(self) -> dict[str, Any]:
        return {
            "pulses": [0.0] * 12,
            "enabled": [True] * 12,
            "estop_active": False,
        }


class RecoveringPrehomeHal(FakeHal):
    def __init__(self, recovered_pulse: float) -> None:
        super().__init__()
        self.recovered_pulse = recovered_pulse
        self.returned_home = False

    async def command(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.commands.append((name, payload))
        if name == "motion.home_origin_side":
            self.returned_home = True
        if name == "teleop.native.status":
            return {"mode": "real", "command": name, "response": self.native_status}
        return {"ok": True}

    async def motion_state(self) -> dict[str, Any]:
        pulses = [0.0] * 12
        if self.returned_home:
            pulses[3] = self.recovered_pulse
        return {
            "pulses": pulses,
            "enabled": [True] * 12,
            "estop_active": False,
        }


class SlowNativeTransitionHal(FakeHal):
    def __init__(self) -> None:
        super().__init__()
        self.active_transition_commands = 0
        self.max_active_transition_commands = 0

    async def command(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name in {"teleop.native.configure", "teleop.native.start", "teleop.native.stop"}:
            self.active_transition_commands += 1
            self.max_active_transition_commands = max(
                self.max_active_transition_commands,
                self.active_transition_commands,
            )
            try:
                await asyncio.sleep(0.01)
                return await super().command(name, payload)
            finally:
                self.active_transition_commands -= 1
        return await super().command(name, payload)


def start_config(
    *,
    valid_origin: bool = True,
    home_before_start: bool = True,
) -> dict[str, Any]:
    config = default_config()
    config["hal"]["mode"] = "real"
    config["teleop"].pop("engine", None)
    config["teleop"]["homeBeforeStart"] = home_before_start
    config["motion"]["origin"] = {
        "valid": valid_origin,
        "leftValid": valid_origin,
        "rightValid": valid_origin,
        "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
        "updatedAt": 100,
        "previousValid": False,
        "previousLeftPulse": [0.0] * 6,
        "previousRightPulse": [0.0] * 6,
        "previousUpdatedAt": 0,
    }
    config["motion"]["homeReference"] = {
        "valid": valid_origin,
        "leftValid": valid_origin,
        "rightValid": valid_origin,
        "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
        "updatedAt": 100,
    }
    return config


def test_native_teleop_start_configures_and_starts_hal_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config()
        config["teleop"]["leftConnected"] = True
        config["teleop"]["rightConnected"] = False
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("recording")
        await mapper.stop("recording")

        command_names = [name for name, _ in hal.commands]
        assert command_names[:3] == [
            "motion.home_all",
            "teleop.native.configure",
            "teleop.native.start",
        ]
        assert "motion.teleop_target_update" not in command_names
        start_payload = hal.commands[2][1]
        assert "engine" not in start_payload
        assert start_payload["leftConnected"] is True
        assert start_payload["rightConnected"] is False
        assert start_payload["controlMode"] == "incremental_position"
        assert start_payload["mappingMode"] == "direct"
        assert start_payload["nativeLoopHz"] == 100
        assert start_payload["leftTranslationScale"] == 1.0
        assert start_payload["rightTranslationScale"] == 1.0
        assert start_payload["leftRotationScale"] == 1.0
        assert start_payload["rightRotationScale"] == 1.0
        assert start_payload["leftGravityCompensation"] is True
        assert start_payload["rightGravityCompensation"] is True
        assert start_payload["leftGravityScale"] == pytest.approx(0.45)
        assert start_payload["rightGravityScale"] == pytest.approx(1.0)
        assert start_payload["leftAxisOutputScale"] == [0.60, 0.50, 0.375, 0.60, 0.08, 0.10]
        assert start_payload["rightAxisOutputScale"] == [0.60, 0.50, 0.375, 0.60, 0.08, 0.001]
        assert start_payload["leftImpulseCoeff"] == [-5000000, -5000000, -10000000, 1667, 2500, -333.3333]
        assert start_payload["rightImpulseCoeff"] == [-5000000, 10000000, -5000000, 1667, -2500, 3333.333]
        assert start_payload["gripperTeleopEnabled"] is True
        assert start_payload["leftSourceHand"] == "PhysicalRight"
        assert start_payload["rightSourceHand"] == "PhysicalLeft"
        assert start_payload["leftWorkOriginValid"] is True
        assert start_payload["rightWorkOriginValid"] is True
        assert start_payload["leftWorkOriginPulse"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        assert start_payload["rightWorkOriginPulse"] == [7.0, 8.0, 9.0, 10.0, 11.0, 12.0]
        assert start_payload["continuousIncrementMode"] is True
        assert start_payload["translationStartVelocityUmS"] == pytest.approx(600.0)
        assert start_payload["translationMaxVelocityUmS"] == pytest.approx(8000.0)
        assert start_payload["rotationStartVelocityDegS"] == pytest.approx(1.0)
        assert start_payload["rotationMaxVelocityDegS"] == pytest.approx(12.0)
        assert start_payload["motionProfileAccSec"] == pytest.approx(0.05)
        assert start_payload["motionProfileDecSec"] == pytest.approx(0.05)
        assert start_payload["translationInputEpsilon"] == pytest.approx(0.00002)
        assert start_payload["rotationInputEpsilon"] == pytest.approx(0.03)
        assert start_payload["translationMinActivePulse"] == pytest.approx(3.0)
        assert start_payload["rotationMinActivePulse"] == pytest.approx(3.0)
        assert start_payload["continuousMicroConfirmTicks"] == 0
        assert hal.commands[-1][0] == "teleop.native.stop"

    asyncio.run(run())


def test_teleop_mapper_start_stop_read_config_off_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")

    async def run() -> None:
        settings = GuardedSettings(start_config())
        mapper = TeleopMappingService(settings=settings, hal=FakeHal(), logs=LogService())

        await mapper.start("recording")
        await mapper.stop("recording")

        assert settings.calls >= 2

    asyncio.run(run())


def test_legacy_python_mapper_config_still_uses_hal_native(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config()
        config["teleop"]["engine"] = "python_mapper"
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("recording")
        await mapper.stop("recording")

        command_names = [name for name, _ in hal.commands]
        assert command_names[:3] == [
            "motion.home_all",
            "teleop.native.configure",
            "teleop.native.start",
        ]
        assert "motion.teleop_target_update" not in command_names

    asyncio.run(run())


def test_native_teleop_connect_enables_hal_native_gripper_follow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config()
        config["teleop"]["leftConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)

        configure_payloads = [payload for name, payload in hal.commands if name == "teleop.native.configure"]
        assert configure_payloads[-1]["gripperTeleopEnabled"] is True

    asyncio.run(run())


def test_native_manual_gripper_source_enables_hal_native_gripper_follow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config()
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("manual-gripper", pre_home=False)

        configure_payloads = [payload for name, payload in hal.commands if name == "teleop.native.configure"]
        assert configure_payloads[-1]["gripperTeleopEnabled"] is True

    asyncio.run(run())


def test_native_gripper_teleop_start_does_not_enable_arm_motion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config()
        config["teleop"]["leftConnected"] = True
        config["teleop"]["rightConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("manual-gripper", pre_home=False)

        configure_payloads = [payload for name, payload in hal.commands if name == "teleop.native.configure"]
        assert configure_payloads[-1]["gripperTeleopEnabled"] is True
        assert configure_payloads[-1]["leftConnected"] is False
        assert configure_payloads[-1]["rightConnected"] is False

    asyncio.run(run())


def test_native_teleop_running_update_uses_start_without_extra_configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config()
        config["teleop"]["leftConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)
        first_configure_count = sum(1 for name, _ in hal.commands if name == "teleop.native.configure")
        config["teleop"]["rightConnected"] = True
        await mapper.start("teleop-connect", pre_home=False)

        assert sum(1 for name, _ in hal.commands if name == "teleop.native.configure") == first_configure_count
        assert hal.commands[-1][0] == "teleop.native.start"
        assert hal.commands[-1][1]["rightConnected"] is True

    asyncio.run(run())


def test_native_manual_gripper_start_does_not_restart_active_arm_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config()
        config["teleop"]["leftConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)
        hal.commands.clear()
        await mapper.start("manual-gripper", pre_home=False)

        assert _non_status_commands(hal.commands) == []
        assert mapper.status()["sources"] == ["manual-gripper", "teleop-connect"]

    asyncio.run(run())


def test_native_teleop_running_origin_change_forces_rehome_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        logs = LogService()
        hal = FakeHal()
        config = start_config()
        config["teleop"]["leftConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=logs)

        await mapper.start("teleop-connect", pre_home=False)
        hal.commands.clear()
        config["motion"]["origin"]["leftPulse"] = [101.0, 102.0, 103.0, 104.0, 105.0, 106.0]

        await mapper.start("teleop-connect", pre_home=False)

        commands = _non_status_commands(hal.commands)
        assert [name for name, _ in commands] == [
            "teleop.native.stop",
            "motion.home_origin_side",
            "teleop.native.start",
        ]
        assert commands[1][1] == {
            "side": "left",
            "pulse": [101.0, 102.0, 103.0, 104.0, 105.0, 106.0],
            "enabledAxes": [True, True, True, True, True, True],
        }
        assert commands[-1][1]["leftWorkOriginPulse"] == [101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
        origin_event = next(
            message for message in [entry.msg for entry in logs.list_entries()]
            if "event=teleop_origin_transition" in message
        )
        assert "action=force_rehome_before_native_restart" in origin_event
        assert "changedSides=[left]" in origin_event
        assert "leftOldWorkOriginPulse=[1,2,3,4,5,6]" in origin_event
        assert "leftNewWorkOriginPulse=[101,102,103,104,105,106]" in origin_event

    asyncio.run(run())


def test_native_teleop_running_duplicate_start_skips_same_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config()
        config["teleop"]["leftConnected"] = True
        config["teleop"]["rightConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)
        first_start_count = sum(1 for name, _ in hal.commands if name == "teleop.native.start")
        await mapper.start("teleop-connect", pre_home=False)

        assert sum(1 for name, _ in hal.commands if name == "teleop.native.start") == first_start_count

    asyncio.run(run())


def test_native_teleop_running_refresh_enters_recovery_for_rotation_outside_work_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = DriftedMotionStateHal()
        config = start_config()
        config["teleop"]["leftConnected"] = False
        config["teleop"]["rightConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)
        hal.commands.clear()
        config["motion"]["homeReference"]["leftPulse"][3] = -1_000_000.0

        await mapper.start("teleop-connect", pre_home=False)

        assert [name for name, _ in _non_status_commands(hal.commands)] == ["teleop.native.start"]
        assert mapper.status()["armed"] is True
        assert mapper.status()["running"] is True
        assert mapper.status()["sources"] == ["teleop-connect"]

    asyncio.run(run())


def test_native_teleop_running_refresh_from_manual_gripper_enters_arm_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = DriftedMotionStateHal()
        config = start_config()
        config["teleop"]["leftConnected"] = False
        config["teleop"]["rightConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("manual-gripper", pre_home=False)
        hal.commands.clear()
        config["motion"]["homeReference"]["leftPulse"][3] = -1_000_000.0

        await mapper.start("teleop-connect", pre_home=True, home_side="left")

        assert [name for name, _ in _non_status_commands(hal.commands)] == ["teleop.native.start"]
        assert mapper.status()["armed"] is True
        assert mapper.status()["running"] is True
        assert mapper.status()["sources"] == ["manual-gripper", "teleop-connect"]

    asyncio.run(run())


def test_native_teleop_stop_can_remove_aux_source_without_restarting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config()
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)
        await mapper.start("recording", pre_home=False)
        hal.commands.clear()

        await mapper.stop("teleop-connect", restart_remaining=False)

        assert _non_status_commands(hal.commands) == []
        assert mapper.status()["armed"] is True
        assert mapper.status()["sources"] == ["recording"]

        await mapper.stop("recording")

        assert [name for name, _ in _non_status_commands(hal.commands)] == ["teleop.native.stop"]

    asyncio.run(run())


def test_native_teleop_stop_restart_enters_recovery_for_remaining_arm_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = DriftedMotionStateHal()
        config = start_config()
        config["teleop"]["leftConnected"] = False
        config["teleop"]["rightConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)
        await mapper.start("recording", pre_home=False)
        hal.commands.clear()
        config["motion"]["homeReference"]["leftPulse"][3] = -1_000_000.0

        await mapper.stop("recording")

        assert [name for name, _ in _non_status_commands(hal.commands)] == [
            "teleop.native.configure",
            "teleop.native.start",
        ]
        assert mapper.status()["armed"] is True
        assert mapper.status()["running"] is True
        assert mapper.status()["sources"] == ["teleop-connect"]

    asyncio.run(run())


def test_native_teleop_start_failure_rolls_back_arm_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = NativeStartTimeoutHal()
        config = start_config()
        config["teleop"]["leftConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        with pytest.raises(RuntimeError, match="/teleop/native/start: timed out"):
            await mapper.start("teleop-connect", pre_home=False)

        status = mapper.status()
        assert status["armed"] is False
        assert status["sources"] == []

    asyncio.run(run())


def test_native_teleop_start_does_not_wait_for_initial_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = StatusTimeoutHal()
        config = start_config(home_before_start=False)
        config["teleop"]["leftConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)
        try:
            command_names = [name for name, _ in hal.commands]
            assert command_names[:2] == ["teleop.native.configure", "teleop.native.start"]
            assert "teleop.native.status" not in command_names[:2]
            assert mapper.status()["armed"] is True
        finally:
            await mapper.stop("teleop-connect")

    asyncio.run(run())


def test_native_teleop_start_and_stop_transitions_are_serialized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = SlowNativeTransitionHal()
        config = start_config(home_before_start=False)
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await asyncio.gather(
            mapper.start("teleop-connect", pre_home=False),
            mapper.start("manual-gripper", pre_home=False),
        )
        await asyncio.gather(
            mapper.stop("teleop-connect", restart_remaining=False),
            mapper.stop("manual-gripper"),
        )

        assert hal.max_active_transition_commands == 1
        assert mapper.status()["armed"] is False

    asyncio.run(run())


def test_native_teleop_stop_is_idempotent_after_source_already_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config(home_before_start=False)
        config["teleop"]["leftConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("recording", pre_home=False)
        await mapper.stop("recording")
        await mapper.stop("recording")

        stop_commands = [name for name, _ in hal.commands if name == "teleop.native.stop"]
        assert stop_commands == ["teleop.native.stop"]

    asyncio.run(run())


def test_native_teleop_status_feeds_dataset_action_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        hal.native_status = {
            "running": True,
            "lastAction": {
                "ts": 123,
                "monotonicMs": 456000,
                "monotonic_s": 456.0,
                "side": "right",
                "sourceSide": "left",
                "axis": "Yaw",
                "delta": 0.5,
                "unit": "deg",
                "deltaVector": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            },
            "actionHistory": [
                {
                    "ts": 123,
                    "monotonicMs": 456000,
                    "side": "right",
                    "sourceSide": "left",
                    "axis": "X",
                    "delta": 10.0,
                    "unit": "um",
                    "deltaVector": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                }
            ],
            "blockers": {"left": {"state": "active"}},
        }
        mapper = TeleopMappingService(
            settings=FakeSettings(start_config()),
            hal=hal,
            logs=LogService(),
        )

        await mapper._refresh_native_status()

        status = mapper.status()
        assert status["running"] is True
        assert status["lastAction"]["side"] == "right"
        assert status["lastAction"]["deltaVector"][6] == 10.0
        assert status["actionHistory"][0]["sourceSide"] == "left"
        assert status["blockers"]["left"]["state"] == "active"

    asyncio.run(run())


def test_native_teleop_status_keeps_dds_action_times_on_hal_steady_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        hal.native_status = {
            "running": True,
            "dds_stamp_monotonic_ms": 456000,
            "monotonicMs": 456000,
            "received_monotonic_ms": 100000,
            "received_timestamp_ms": 2222,
            "lastAction": {
                "ts": 123,
                "monotonicMs": 455900,
                "monotonic_s": 455.9,
                "side": "right",
                "deltaVector": [0.0] * 12,
            },
            "actionHistory": [
                {
                    "ts": 122,
                    "monotonicMs": 455875,
                    "side": "left",
                    "deltaVector": [1.0] * 12,
                }
            ],
            "blockers": {},
        }
        mapper = TeleopMappingService(
            settings=FakeSettings(start_config()),
            hal=hal,
            logs=LogService(),
        )

        await mapper._refresh_native_status()

        status = mapper.status()
        assert status["lastAction"]["monotonicMs"] == 455900
        assert status["lastAction"]["monotonic_s"] == pytest.approx(455.9)
        assert "host_monotonic_s" not in status["lastAction"]
        assert "hostMonotonicMs" not in status["lastAction"]
        assert status["actionHistory"][0]["monotonicMs"] == 455875
        assert "host_monotonic_s" not in status["nativeStatus"]["lastAction"]

    asyncio.run(run())


def test_native_status_loop_uses_gripper_sample_rate_for_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config()
        config["gripper"]["sampleHz"] = 30
        config["teleop"]["commandIntervalMs"] = 10
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())
        mapper._stop_event = asyncio.Event()
        delays: list[float] = []

        async def fake_refresh() -> None:
            assert mapper._stop_event is not None
            mapper._stop_event.set()

        async def fake_sleep(delay: float) -> None:
            delays.append(delay)

        monkeypatch.setattr(mapper, "_refresh_native_status", fake_refresh)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        await mapper._run_native_status_loop()

        assert delays == pytest.approx([1.0 / 30.0])

    asyncio.run(run())


def test_native_teleop_status_diag_log_reports_pulse_update_and_clip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        logs = LogService()
        hal = FakeHal()
        config = start_config()
        config["teleop"]["diagLog"] = True
        hal.native_status = {
            "running": True,
            "lastAction": {
                "ts": 123,
                "monotonicMs": 456000,
                "monotonic_s": 456.0,
                "side": "right",
                "sourceSide": "left",
                "axis": "Yaw",
                "delta": 0.5,
                "unit": "deg",
                "deltas": [0.0, 0.0, 0.0, 0.0, 0.0, 0.5],
                "appliedDeltas": [0.0, 0.0, 0.0, 0.0, 0.0, 0.5],
                "requestedDeltaPulse": [0.0, 0.0, 0.0, 0.0, 0.0, -333.0],
                "appliedDeltaPulse": [0.0, 0.0, 0.0, 0.0, 0.0, -333.0],
                "targetPulse": [0.0, 0.0, 0.0, 0.0, 0.0, 1200.0],
                "currentPulse": [0.0, 0.0, 0.0, 0.0, 0.0, 900.0],
                "launchDeltaPulse": [0.0, 0.0, 0.0, 0.0, 0.0, -333.0],
                "updateReturn": [0.0, 0.0, 0.0, 0.0, 0.0, 21.0],
                "stopReason": [0.0, 0.0, 0.0, 0.0, 0.0, 5.0],
                "axisIoStatus": [0.0, 0.0, 0.0, 0.0, 0.0, 4096.0],
                "movingBefore": [False, False, False, False, False, False],
                "moveStarted": [False, False, False, False, False, True],
                "clipped": [False, False, False, False, False, True],
                "deltaVector": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5],
            },
            "actionHistory": [],
            "blockers": {"left": {"state": "active"}},
            "inputs": {
                "left": {
                    "targetSide": "right",
                    "referenceValid": True,
                    "inputActive": True,
                    "semanticPose": [0.0, 0.0, 0.0, 12.5, 0.0, 0.5],
                    "referencePose": [0.0, 0.0, 0.0, 12.0, 0.0, 0.0],
                    "rawDelta": [0.0, 0.0, 0.0, 0.5, 0.0, 0.5],
                    "filteredDelta": [0.0, 0.0, 0.0, 0.5, 0.0, 0.5],
                    "requestedPulse": [0.0, 0.0, 0.0, 833.5, 0.0, -333.0],
                    "emittedPulse": [0.0, 0.0, 0.0, 833.5, 0.0, -333.0],
                    "outputDeltaUi": [0.0, 0.0, 0.0, 0.5, 0.0, 0.5],
                },
            },
        }
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=logs)

        await mapper._refresh_native_status()
        await mapper._refresh_native_status()

        messages = [entry.msg for entry in logs.list_entries()]
        diag_messages = [message for message in messages if message.startswith("teleop diag left->right")]
        assert len(diag_messages) == 1
        event_messages = [
            message
            for message in messages
            if "event=teleop_status" in message and "sideMap=left->right" in message and "axis=Yaw" in message
        ]
        assert len(event_messages) == 1
        event = event_messages[0]
        assert "axis=Yaw" in event
        assert "clip=Yaw" in event
        assert "reqPulse=[Yaw:-333]" in event
        assert "emitPulse=[Yaw:-333]" in event
        assert "targetPulse=[Yaw:1200]" in event
        assert "currentPulse=[Yaw:900]" in event
        assert "updateRet=[Yaw:21]" in event
        assert "stopReason=[Yaw:5]" in event
        assert "axisIoStatus=[Yaw:4096]" in event
        axis_trace = next(message for message in messages if "event=teleop_axis_trace" in message)
        assert "source=left" in axis_trace
        assert "target=right" in axis_trace
        assert "axis=Yaw" in axis_trace
        assert "rawPose=[Roll:12.5,Yaw:0.5]" in axis_trace
        assert "refPose=[Roll:12]" in axis_trace
        assert "rawDelta=[Roll:0.5,Yaw:0.5]" in axis_trace
        assert "filteredDelta=[Roll:0.5,Yaw:0.5]" in axis_trace
        assert "outputDelta=[Roll:0.5,Yaw:0.5]" in axis_trace
        assert "requestedPulse=[Roll:833.5,Yaw:-333]" in axis_trace
        assert "emitPulse=[Roll:833.5,Yaw:-333]" in axis_trace
        assert "currentPulse=[Yaw:900]" in axis_trace
        assert "targetPulse=[Yaw:1200]" in axis_trace
        assert "launchPulse=[Yaw:-333]" in axis_trace
        assert "moveStarted=[Yaw:1]" in axis_trace
        assert "clipped=[Yaw:1]" in axis_trace
        assert "updateRet=[Yaw:21]" in axis_trace
        assert "stopReason=[Yaw:5]" in axis_trace
        assert "axisIoStatus=[Yaw:4096]" in axis_trace
        assert "blockReason=active" in axis_trace
        assert "referenceValid=true" in axis_trace
        assert "inputActive=true" in axis_trace
        diag = diag_messages[0]
        assert "axis=Yaw" in diag
        assert "clip=Yaw" in diag
        assert "app=[Yaw:0.5]" in diag
        assert "pulseReq=[Yaw:-333]" in diag
        assert "pulseApp=[Yaw:-333]" in diag
        assert "targetPulse=[Yaw:1200]" in diag
        assert "currentPulse=[Yaw:900]" in diag
        assert "launchPulse=[Yaw:-333]" in diag
        assert "movingBefore=[]" in diag
        assert "moveStarted=[Yaw:1]" in diag
        assert "updateRet=[Yaw:21]" in diag
        assert "stopReason=[Yaw:5]" in diag
        assert "axisIoStatus=[Yaw:4096]" in diag

    asyncio.run(run())


def test_native_teleop_status_logs_compact_input_gate_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        logs = LogService()
        hal = FakeHal()
        config = start_config()
        config["teleop"]["diagLog"] = False
        hal.native_status = {
            "running": True,
            "lastAction": {
                "side": "right",
                "sourceSide": "left",
                "launchDeltaPulse": [0.0, 0.0, 0.0, 0.0, 0.0, -333.0],
                "updateReturn": [0.0, 0.0, 0.0, 0.0, 0.0, 21.0],
                "moveStarted": [False, False, False, False, False, True],
            },
            "actionHistory": [],
            "blockers": {
                "left": {"state": "active", "message": ""},
                "right": {"state": "active", "message": "incremental input below output threshold"},
            },
            "inputs": {
                "left": {
                    "targetSide": "right",
                    "referenceValid": True,
                    "inputActive": True,
                    "rawDelta": [0.001, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "requestedPulse": [9.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "emittedPulse": [8.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "outputDeltaUi": [1.6, 0.0, 0.0, 0.0, 0.0, 0.0],
                },
                "right": {
                    "targetSide": "left",
                    "referenceValid": True,
                    "inputActive": True,
                    "rawDelta": [0.0, 0.0, 0.0, 0.0, 0.0, 0.02],
                    "requestedPulse": [0.0, 0.0, 0.0, 0.0, 0.0, 1.3],
                    "emittedPulse": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "outputDeltaUi": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                },
            },
            "grippers": {
                "left": {"ok": False, "targetMm": 2.0, "message": "COM8 open failed", "lastCommandTs": 123},
                "right": {"ok": True, "targetMm": 20.0, "message": "", "lastCommandTs": 124},
            },
        }
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=logs)

        await mapper._refresh_native_status()

        messages = [entry.msg for entry in logs.list_entries()]
        summary = next(message for message in messages if message.startswith("native status "))
        event = next(
            message for message in messages if "event=teleop_status" in message and "sideMap=left->right" in message
        )
        assert "raw=[X:0.001]" in event
        assert "reqPulse=[X:9]" in event
        assert "emitPulse=[X:8]" in event
        assert "blockReason=active" in event
        assert "left->right" in summary
        assert "right->left" in summary
        assert "raw=[X:0.001]" in summary
        assert "reqPulse=[X:9]" in summary
        assert "emitPulse=[X:8]" in summary
        assert "out=[X:1.6]" in summary
        assert "block=active:incremental input below output threshold" in summary
        assert "last=left->right" in summary
        assert "updateRet=[Yaw:21]" in summary
        assert "moveStarted=[Yaw:1]" in summary
        assert "launchPulse=[Yaw:-333]" in summary
        assert "grip=left:ERR target=2" in summary
        assert "COM8 open failed" in summary

    asyncio.run(run())


def test_native_teleop_status_summary_is_time_throttled_even_when_idle_values_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    current_ms = {"value": 1000}
    monkeypatch.setattr("backend.services.teleop_mapping.now_ms", lambda: current_ms["value"])

    async def run() -> None:
        logs = LogService(monotonic_ms=lambda: current_ms["value"], emit_startup=False)
        hal = FakeHal()
        config = start_config()
        config["teleop"]["diagLog"] = False
        hal.native_status = {
            "running": True,
            "lastAction": None,
            "actionHistory": [],
            "blockers": {
                "left": {"state": "idle", "message": "logical hand is disconnected"},
                "right": {"state": "idle", "message": "logical hand is disconnected"},
            },
            "inputs": {
                "left": {"targetSide": "right", "referenceValid": False, "inputActive": False},
                "right": {"targetSide": "left", "referenceValid": False, "inputActive": False},
            },
            "grippers": {
                "left": {"ok": True, "targetMm": 0.1, "message": "", "lastCommandTs": 10},
                "right": {"ok": False, "targetMm": 5.0, "message": "COM9 open failed", "lastCommandTs": 11},
            },
        }
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=logs)

        await mapper._refresh_native_status()
        current_ms["value"] = 1400
        hal.native_status["grippers"]["left"]["targetMm"] = 0.2
        hal.native_status["grippers"]["right"]["targetMm"] = 5.1
        await mapper._refresh_native_status()

        messages = [entry.msg for entry in logs.list_entries()]
        assert len([message for message in messages if message.startswith("native status ")]) == 1
        assert len([message for message in messages if "event=teleop_status" in message]) == 2

    asyncio.run(run())


def test_native_gripper_summary_marks_never_commanded_ports_idle() -> None:
    mapper = TeleopMappingService(settings=None, hal=FakeHal(), logs=None)  # type: ignore[arg-type]

    summary = mapper._format_native_gripper_summary(
        {
            "left": {"ok": False, "targetMm": 0.0, "message": "", "lastCommandTs": 0},
            "right": {"ok": False, "targetMm": 0.0, "message": "", "lastCommandTs": 0},
        }
    )

    assert summary == "grip=left:IDLE target=0;right:IDLE target=0"


def test_native_teleop_start_logs_mode_and_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        logs = LogService()
        hal = FakeHal()
        config = start_config()
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=logs)

        await mapper.start("manual-gripper", pre_home=False)

        messages = [entry.msg for entry in logs.list_entries()]
        assert any("event=teleop_mode" in message and "source=manual-gripper" in message for message in messages)
        assert any("event=teleop_profile" in message and "axis=left.Yaw" in message for message in messages)

    asyncio.run(run())


def test_native_teleop_idle_status_clears_stale_action_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(
            settings=FakeSettings(start_config()),
            hal=hal,
            logs=LogService(),
        )
        hal.native_status = {
            "running": True,
            "lastAction": {
                "side": "right",
                "sourceSide": "left",
                "deltaVector": [1.0] * 12,
            },
            "actionHistory": [{"deltaVector": [1.0] * 12}],
            "blockers": {"left": {"state": "active"}},
            "lastError": "dmc_pmove failed ret=3006",
        }
        await mapper._refresh_native_status()
        assert mapper.status()["lastError"] == "dmc_pmove failed ret=3006"

        hal.native_status = {
            "running": False,
            "lastAction": None,
            "actionHistory": [],
            "blockers": {},
            "lastError": "",
        }
        await mapper._refresh_native_status()

        status = mapper.status()
        assert status["lastAction"] is None
        assert status["actionHistory"] == []
        assert status["blockers"] == {}
        assert status["lastError"] == ""

    asyncio.run(run())


def test_native_teleop_stop_clears_native_status_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config(home_before_start=False)
        mapper = TeleopMappingService(
            settings=FakeSettings(config),
            hal=hal,
            logs=LogService(),
        )
        hal.native_status = {
            "running": True,
            "lastAction": {
                "side": "right",
                "sourceSide": "left",
                "deltaVector": [1.0] * 12,
            },
            "actionHistory": [{"deltaVector": [1.0] * 12}],
            "blockers": {"left": {"state": "active"}},
            "lastError": "dmc_set_profile failed ret=4",
        }
        await mapper.start("recording", pre_home=False)
        assert mapper.status()["running"] is True

        hal.native_status = {
            "running": False,
            "lastAction": None,
            "actionHistory": [],
            "blockers": {},
            "lastError": "",
        }
        await mapper.stop("recording")
        status = mapper.status()

        assert status["armed"] is False
        assert status["running"] is False
        assert status["lastAction"] is None
        assert status["actionHistory"] == []
        assert status["blockers"] == {}
        assert status["lastError"] == ""
        assert status["nativeStatus"] == {}

    asyncio.run(run())


def test_native_teleop_stop_failure_still_clears_local_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FailingNativeStopHal()
        config = start_config(home_before_start=False)
        mapper = TeleopMappingService(
            settings=FakeSettings(config),
            hal=hal,
            logs=LogService(),
        )
        hal.native_status = {
            "running": True,
            "lastAction": {"side": "right", "sourceSide": "left", "deltaVector": [1.0] * 12},
            "actionHistory": [{"deltaVector": [1.0] * 12}],
            "blockers": {"left": {"state": "active"}},
            "lastError": "",
        }
        await mapper.start("teleop-connect", pre_home=False)

        with pytest.raises(RuntimeError, match="connection refused"):
            await mapper.stop("teleop-connect")

        status = mapper.status()
        assert status["armed"] is False
        assert status["running"] is False
        assert status["sources"] == []
        assert status["nativeStatus"] == {}

    asyncio.run(run())


def test_teleop_start_returns_to_work_origin_before_real_mapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=FakeSettings(start_config()), hal=hal, logs=LogService())

        await mapper.start("recording")
        await mapper.stop("recording")

        assert hal.commands[:1] == [
            (
                "motion.home_all",
                {
                    "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                    "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
                    "leftEnabledAxes": [True, True, True, True, True, True],
                    "rightEnabledAxes": [True] * 6,
                },
            ),
        ]

    asyncio.run(run())


def test_teleop_start_returns_requested_side_to_origin_before_real_mapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=FakeSettings(start_config()), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", home_side="right")
        await mapper.stop("teleop-connect")

        assert hal.commands[:1] == [
            (
                "motion.home_origin_side",
                {
                    "side": "right",
                    "pulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
                    "enabledAxes": [True] * 6,
                },
            ),
        ]

    asyncio.run(run())


def test_teleop_start_requires_work_origin_when_pre_home_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(
            settings=FakeSettings(start_config(valid_origin=False)),
            hal=hal,
            logs=LogService(),
        )

        with pytest.raises(RuntimeError, match="work origin is not captured"):
            await mapper.start("recording")

        assert hal.commands == []
        assert mapper.status()["armed"] is False

    asyncio.run(run())


def test_native_teleop_status_reports_startup_work_origin_blockers() -> None:
    config = start_config(valid_origin=False)
    config["teleop"]["swapTeleopChannels"] = True
    mapper = TeleopMappingService(settings=FakeSettings(config), hal=FakeHal(), logs=LogService())

    blockers = mapper.status(config)["blockers"]

    assert blockers["left"]["sourceSide"] == "left"
    assert blockers["left"]["targetSide"] == "right"
    assert blockers["left"]["active"] is False
    assert blockers["left"]["state"] == "blocked"
    assert blockers["left"]["reasons"] == ["right motion work origin is not captured"]
    assert blockers["right"]["sourceSide"] == "right"
    assert blockers["right"]["targetSide"] == "left"
    assert blockers["right"]["reasons"] == ["left motion work origin is not captured"]


def test_native_teleop_status_reports_previous_origin_restore_blocker_detail() -> None:
    config = start_config(valid_origin=False)
    config["teleop"]["swapTeleopChannels"] = False
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 100,
    }
    config["motion"]["origin"]["previousValid"] = True
    config["motion"]["origin"]["previousLeftPulse"] = [0.0, 0.0, 0.0, 0.0, 0.0, 100_000.0]
    mapper = TeleopMappingService(settings=FakeSettings(config), hal=FakeHal(), logs=LogService())

    reasons = mapper.status(config)["blockers"]["left"]["reasons"]

    assert reasons[0] == "left motion work origin is not captured"
    assert any("previous left work origin" in reason and "Yaw" in reason and "not in" in reason for reason in reasons)


def test_teleop_prehome_blocks_stale_hardware_zero_before_motion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = DriftedMotionStateHal()
        config = start_config()
        config["teleop"]["leftConnected"] = False
        config["teleop"]["rightConnected"] = True
        config["motion"]["homeReference"]["leftPulse"][3] = -1_000_000.0
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        with pytest.raises(RuntimeError, match="left Roll current position is outside work soft limit"):
            await mapper.start("teleop-connect", pre_home=True, home_side="left")

        assert [name for name, _ in hal.commands] == ["motion.home_origin_side"]
        assert mapper.status()["armed"] is False

    asyncio.run(run())


def test_native_teleop_prehome_disabled_enters_recovery_for_rotation_position_outside_work_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = DriftedMotionStateHal()
        config = start_config(home_before_start=False)
        config["teleop"]["leftConnected"] = False
        config["teleop"]["rightConnected"] = True
        config["motion"]["homeReference"]["leftPulse"][3] = -1_000_000.0
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=True, home_side="left")

        assert [name for name, _ in hal.commands[:2]] == ["teleop.native.configure", "teleop.native.start"]
        assert mapper.status()["armed"] is True

    asyncio.run(run())


def test_native_teleop_prehome_can_recover_rotation_position_outside_work_limit_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        recovered_roll_pulse = 25_000.0
        hal = RecoveringPrehomeHal(recovered_roll_pulse)
        config = start_config()
        config["teleop"]["leftConnected"] = False
        config["teleop"]["rightConnected"] = True
        config["motion"]["homeReference"]["leftPulse"][3] = 0.0
        config["motion"]["origin"]["leftPulse"][3] = recovered_roll_pulse
        config["motion"]["rotationWorkLimits"]["left"]["roll"] = {"min": 10.0, "max": 20.0}
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=True, home_side="left")

        assert [name for name, _ in hal.commands[:3]] == [
            "motion.home_origin_side",
            "teleop.native.configure",
            "teleop.native.start",
        ]
        assert mapper.status()["armed"] is True
        assert mapper.status()["running"] is True

        await mapper.stop("teleop-connect")

    asyncio.run(run())


def test_native_teleop_start_without_prehome_enters_recovery_for_rotation_position_outside_work_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = DriftedMotionStateHal()
        config = start_config()
        config["teleop"]["leftConnected"] = False
        config["teleop"]["rightConnected"] = True
        config["motion"]["homeReference"]["leftPulse"][3] = -1_000_000.0
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)

        assert [name for name, _ in hal.commands[:2]] == ["teleop.native.configure", "teleop.native.start"]
        assert mapper.status()["armed"] is True

    asyncio.run(run())


def test_native_gripper_teleop_start_ignores_stale_arm_connection_with_hal_native_gripper_follow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = DriftedMotionStateHal()
        config = start_config()
        config["teleop"]["leftConnected"] = False
        config["teleop"]["rightConnected"] = True
        config["motion"]["homeReference"]["leftPulse"][3] = -1_000_000.0
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("manual-gripper", pre_home=False)

        assert [name for name, _ in hal.commands[:2]] == ["teleop.native.configure", "teleop.native.start"]
        assert hal.commands[0][1]["leftConnected"] is False
        assert hal.commands[0][1]["rightConnected"] is False
        assert hal.commands[0][1]["gripperTeleopEnabled"] is True
        assert mapper.status()["armed"] is True

    asyncio.run(run())


def test_python_mapper_motion_update_path_removed() -> None:
    source = (BACKEND_ROOT / "services" / "teleop_mapping.py").read_text(encoding="utf-8")

    assert "motion.teleop_target_update" not in source
    assert "_step_side" not in source
    assert "def _start_native(" not in source
def test_hal_native_payload_disables_translation_soft_limits() -> None:
    config = start_config()
    config["motion"]["origin"]["leftPulse"] = [-500.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0]
    config["motion"]["leftSoftLimits"] = {
        "x": {"min": -52000.0, "max": -2000.0},
        "y": {"min": -1000.0, "max": 3000.0},
        "z": {"min": -500.0, "max": 4500.0},
        "roll": {"min": -4000.0, "max": 4000.0},
        "pitch": {"min": -5000.0, "max": 5000.0},
        "yaw": {"min": -6000.0, "max": 6000.0},
    }
    mapper = TeleopMappingService(settings=FakeSettings(config), hal=FakeHal(), logs=LogService())

    payload = mapper._native_payload(config)

    assert payload["leftWorkOriginValid"] is True
    assert payload["leftWorkOriginPulse"] == [-500.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0]
    assert payload["leftSoftLimitMin"] == [-1000000000.0, -1000000000.0, -1000000000.0, -4.0, -5.0, -6.0]
    assert payload["leftSoftLimitMax"] == [1000000000.0, 1000000000.0, 1000000000.0, 4.0, 5.0, 6.0]


def test_hal_native_payload_sends_kalman_filter_toggle() -> None:
    config = start_config()
    config["teleop"]["kalmanFilterEnabled"] = True
    config["teleop"]["kalmanBeta"] = 0.12
    config["teleop"]["kalmanDtMaxSec"] = 0.08
    config["teleop"]["kalmanTranslationMeasurementVariance"] = 0.000003
    config["teleop"]["kalmanRotationMeasurementVariance"] = 0.08
    config["teleop"]["kalmanTranslationIntentVelocityThreshold"] = 0.0007
    config["teleop"]["kalmanRotationIntentVelocityThreshold"] = 0.7
    mapper = TeleopMappingService(settings=FakeSettings(config), hal=FakeHal(), logs=LogService())

    payload = mapper._native_payload(config)

    assert payload["kalmanFilterEnabled"] is True
    assert payload["kalmanBeta"] == 0.12
    assert payload["kalmanDtMaxSec"] == 0.08
    assert payload["kalmanTranslationMeasurementVariance"] == 0.000003
    assert payload["kalmanRotationMeasurementVariance"] == 0.08
    assert payload["kalmanTranslationIntentVelocityThreshold"] == 0.0007
    assert payload["kalmanRotationIntentVelocityThreshold"] == 0.7


def test_hal_native_payload_enables_isolated_gripper_workers_by_default() -> None:
    config = start_config()
    config["gripper"]["jodellWorkerExePath"] = "F:/custom/JodellGripperWorker.exe"
    config["gripper"]["workerCommandTimeoutSec"] = 1.5
    mapper = TeleopMappingService(settings=FakeSettings(config), hal=FakeHal(), logs=LogService())

    payload = mapper._native_payload(config)

    assert payload["gripperProcessWorkersEnabled"] is True
    assert payload["jodellWorkerExePath"] == "F:/custom/JodellGripperWorker.exe"
    assert payload["gripperWorkerCommandTimeoutMs"] == 1500.0


def test_hal_native_payload_enables_gripper_teleop_from_config() -> None:
    config = start_config()
    config["teleop"]["gripperTeleop"]["enabled"] = True
    mapper = TeleopMappingService(settings=FakeSettings(config), hal=FakeHal(), logs=LogService())

    payload = mapper._native_payload(config)

    assert payload["gripperTeleopEnabled"] is True


def test_hal_native_payload_maps_operator_gripper_sources_to_hardware_targets() -> None:
    config = start_config()
    config["teleop"]["gripperTeleop"]["leftSourceHand"] = "PhysicalLeft"
    config["teleop"]["gripperTeleop"]["rightSourceHand"] = "PhysicalRight"
    mapper = TeleopMappingService(settings=FakeSettings(config), hal=FakeHal(), logs=LogService())

    payload = mapper._native_payload(config)

    assert payload["leftSourceHand"] == "PhysicalRight"
    assert payload["rightSourceHand"] == "PhysicalLeft"


def test_hal_native_payload_gripper_source_fallbacks_use_operator_view() -> None:
    config = start_config()
    config["teleop"]["gripperTeleop"].pop("leftSourceHand", None)
    config["teleop"]["gripperTeleop"].pop("rightSourceHand", None)
    mapper = TeleopMappingService(settings=FakeSettings(config), hal=FakeHal(), logs=LogService())

    payload = mapper._native_payload(config)

    assert payload["leftSourceHand"] == "PhysicalRight"
    assert payload["rightSourceHand"] == "PhysicalLeft"


def test_hal_native_payload_allows_yaw_on_card0_side() -> None:
    config = start_config()
    config["motion"]["leftCardNo"] = 0
    config["motion"]["rightCardNo"] = 1
    config["teleop"]["leftEnabledAxes"] = [True, True, True, True, True, True]
    config["teleop"]["rightEnabledAxes"] = [True, True, True, True, True, True]
    mapper = TeleopMappingService(settings=FakeSettings(config), hal=FakeHal(), logs=LogService())

    payload = mapper._native_payload(config)

    assert payload["leftEnabledAxes"] == [True] * 6
    assert payload["rightEnabledAxes"] == [True] * 6


def test_hal_native_payload_disables_translation_limits_and_sends_rotation_work_window() -> None:
    config = start_config()
    config["motion"]["leftSoftLimits"] = {
        "x": {"min": -101.0, "max": 102.0},
        "y": {"min": -201.0, "max": 202.0},
        "z": {"min": -301.0, "max": 302.0},
        "roll": {"min": 10000.0, "max": 50000.0},
        "pitch": {"min": 20000.0, "max": 60000.0},
        "yaw": {"min": -33000.0, "max": -22000.0},
    }
    config["motion"]["rotationWorkLimits"] = {
        "enabled": True,
        "left": {
            "roll": {"min": -4.0, "max": 6.0},
            "pitch": {"min": -5.0, "max": 7.0},
            "yaw": {"min": -1.0, "max": 1.0},
        },
        "right": {
            "roll": {"min": -8.0, "max": 9.0},
            "pitch": {"min": -10.0, "max": 11.0},
            "yaw": {"min": -2.0, "max": 2.0},
        },
    }
    mapper = TeleopMappingService(settings=FakeSettings(config), hal=FakeHal(), logs=LogService())

    payload = mapper._native_payload(config)

    assert payload["leftSoftLimitMin"] == pytest.approx(
        [-1000000000.0, -1000000000.0, -1000000000.0, 10.0, 20.0, -33.0]
    )
    assert payload["leftSoftLimitMax"] == pytest.approx([1000000000.0, 1000000000.0, 1000000000.0, 50.0, 60.0, -22.0])
    assert payload["rotationWorkLimitEnabled"] is True
    assert payload["leftRotationWorkLimitMin"] == [0.0, 0.0, 0.0, -4.0, -5.0, -1.0]
    assert payload["leftRotationWorkLimitMax"] == [0.0, 0.0, 0.0, 6.0, 7.0, 1.0]


def test_hal_native_payload_sends_home_reference_for_soft_limit_anchor() -> None:
    config = start_config()
    config["motion"]["origin"]["leftPulse"] = [1.0, 2.0, 3.0, 166_667.0, 5.0, 6.0]
    config["motion"]["homeReference"] = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [10.0, 20.0, 30.0, 0.0, 50.0, 60.0],
        "rightPulse": [70.0, 80.0, 90.0, 100.0, 110.0, 120.0],
        "updatedAt": 123,
    }
    mapper = TeleopMappingService(settings=FakeSettings(config), hal=FakeHal(), logs=LogService())

    payload = mapper._native_payload(config)

    assert payload["leftWorkOriginPulse"] == [1.0, 2.0, 3.0, 166_667.0, 5.0, 6.0]
    assert payload["leftHomeReferenceValid"] is True
    assert payload["leftHomeReferencePulse"] == [10.0, 20.0, 30.0, 0.0, 50.0, 60.0]


def test_effective_rotation_limits_follow_home_reference_not_work_origin() -> None:
    config = default_config()
    config["motion"]["origin"]["leftPulse"][3] = 166_667.0
    config["motion"]["origin"]["leftValid"] = True
    config["motion"]["origin"]["valid"] = True
    config["motion"]["homeReference"]["leftPulse"][3] = 0.0
    config["motion"]["homeReference"]["leftValid"] = True
    config["motion"]["homeReference"]["valid"] = True
    config["motion"]["leftSoftLimits"]["roll"] = {"min": -360_000.0, "max": 360_000.0}

    limits = effective_limits_ui(config, "left")

    work_origin = side_origin_ui(config, "left")
    assert work_origin is not None
    assert work_origin[3] == pytest.approx(100.0, abs=1e-3)
    home_roll = pulse_to_axis_ui(config, "left", 3, config["motion"]["homeReference"]["leftPulse"][3])
    assert limits[3].min == pytest.approx(home_roll - 5.0, abs=1e-6)
    assert limits[3].max == pytest.approx(home_roll + 95.0, abs=1e-6)


def test_settings_migration_updates_existing_runtime_to_icf_teleop_strategy(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    old_config = default_config()
    old_config["teleop"].pop("strategyVersion", None)
    old_config["teleop"]["controlMode"] = "velocity_admittance"
    old_config["teleop"]["leftConnected"] = True
    old_config["teleop"]["leftTranslationScale"] = 0.24
    old_config["teleop"]["leftRotationScale"] = 0.18
    old_config["teleop"]["gripperTeleop"]["leftSourceHand"] = "PhysicalRight"
    old_config["teleop"]["gripperTeleop"]["rightSourceHand"] = "PhysicalLeft"
    old_config["motion"]["leftSoftLimits"]["yaw"] = {"min": -7.5, "max": 7.5}
    old_config["motion"].pop("workOriginStrategyVersion", None)
    old_config["motion"]["origin"] = {
        "valid": False,
        "leftValid": False,
        "rightValid": False,
        "leftPulse": [0.0] * 6,
        "rightPulse": [0.0] * 6,
        "updatedAt": 0,
    }
    (runtime_dir / "config.json").write_text(json.dumps(old_config), encoding="utf-8")

    config = SettingsService(runtime_dir, LogService()).get_config()

    assert config["teleop"]["strategyVersion"] == ICF_TELEOP_STRATEGY_VERSION
    assert config["teleop"]["controlMode"] == "incremental_position"
    assert config["teleop"]["leftConnected"] is True
    assert config["teleop"]["leftGravityCompensation"] is True
    assert config["teleop"]["rightGravityCompensation"] is True
    assert config["teleop"]["leftForceFeedback"] is True
    assert config["teleop"]["rightForceFeedback"] is True
    assert config["teleop"]["leftGravityScale"] == 0.45
    assert config["teleop"]["rightGravityScale"] == 1.0
    assert config["teleop"]["leftTranslationScale"] == 1.0
    assert config["teleop"]["rightTranslationScale"] == 1.0
    assert config["teleop"]["leftRotationScale"] == 1.0
    assert config["teleop"]["rightRotationScale"] == 1.0
    assert config["teleop"]["mappingMode"] == "direct"
    assert config["teleop"]["leftAxisOutputScale"] == [0.60, 0.50, 0.375, 0.60, 0.08, 0.10]
    assert config["teleop"]["rightAxisOutputScale"] == [0.60, 0.50, 0.375, 0.60, 0.08, 0.001]
    assert config["teleop"]["translationStepLimitPulse"] == 4000
    assert config["teleop"]["rotationStepLimitPulse"] == 1250
    assert config["teleop"]["translationPulseDeadband"] == 2
    assert config["teleop"]["rotationPulseDeadband"] == 2
    assert config["teleop"]["translationStartVelocityUmS"] == 600.0
    assert config["teleop"]["translationMaxVelocityUmS"] == 8000.0
    assert config["teleop"]["rotationStartVelocityDegS"] == 1.0
    assert config["teleop"]["rotationMaxVelocityDegS"] == 12.0
    assert config["teleop"]["continuousIncrementMode"] is True
    assert config["teleop"]["translationInputEpsilon"] == 0.00002
    assert config["teleop"]["rotationInputEpsilon"] == 0.03
    assert config["teleop"]["translationMinActivePulse"] == 3
    assert config["teleop"]["rotationMinActivePulse"] == 3
    assert config["teleop"]["continuousMicroConfirmTicks"] == 0
    assert config["teleop"]["diagLog"] is False
    assert config["teleop"]["swapHands"] is False
    assert config["teleop"]["swapTeleopChannels"] is True
    assert config["teleop"]["stabilityMode"] == "off"
    assert config["teleop"]["softLimitUnitSpec"] == ["um", "um", "um", "deg", "deg", "deg"]
    assert config["teleop"]["leftSoftLimitMin"] == [
        -25000.0,
        -37500.0,
        -37500.0,
        -5.0,
        -30.0,
        -7.0,
    ]
    assert config["teleop"]["leftSoftLimitMax"] == [
        25000.0,
        37500.0,
        37500.0,
        95.0,
        30.0,
        7.0,
    ]
    assert config["teleop"]["rightEnabledAxes"] == [True] * 6
    assert config["teleop"]["rightSoftLimitMin"] == [
        -25000.0,
        -37500.0,
        -37500.0,
        -95.0,
        -30.0,
        -7.0,
    ]
    assert config["teleop"]["rightSoftLimitMax"] == [
        25000.0,
        37500.0,
        37500.0,
        5.0,
        30.0,
        7.0,
    ]
    assert config["teleop"]["leftImpulseCoeff"] == [-5000000, -5000000, -10000000, 1667, 2500, -333.3333]
    assert config["teleop"]["rightImpulseCoeff"] == [-5000000, 10000000, -5000000, 1667, -2500, 3333.333]
    assert config["teleop"]["leftDirectionSign"] == [1, -1, -1, 1, -1, -1]
    assert config["teleop"]["rightDirectionSign"] == [1, 1, -1, 1, 1, 1]
    assert config["teleop"]["syncImpulseCoeffFromKinematics"] is False
    assert config["motion"]["rotationWorkLimits"]["enabled"] is True
    assert config["motion"]["rotationWorkLimits"]["left"]["roll"] == {"min": -5.0, "max": 95.0}
    assert config["motion"]["rotationWorkLimits"]["left"]["pitch"] == {"min": -30.0, "max": 30.0}
    assert config["motion"]["rotationWorkLimits"]["left"]["yaw"] == {"min": -7.0, "max": 7.0}
    assert config["motion"]["rotationWorkLimits"]["right"]["roll"] == {"min": -95.0, "max": 5.0}
    assert config["motion"]["rotationWorkLimits"]["right"]["pitch"] == {"min": -30.0, "max": 30.0}
    assert config["motion"]["leftSoftLimits"]["x"] == {"min": -25000.0, "max": 25000.0}
    assert config["motion"]["leftSoftLimits"]["y"] == {"min": -37500.0, "max": 37500.0}
    assert config["motion"]["leftSoftLimits"]["z"] == {"min": -37500.0, "max": 37500.0}
    assert config["motion"]["rightSoftLimits"]["x"] == {"min": -25000.0, "max": 25000.0}
    assert config["motion"]["rightSoftLimits"]["y"] == {"min": -37500.0, "max": 37500.0}
    assert config["motion"]["rightSoftLimits"]["z"] == {"min": -37500.0, "max": 37500.0}
    for axis_key in ("roll", "pitch", "yaw"):
        assert config["motion"]["leftSoftLimits"][axis_key] == {
            "min": -ICF_ROTATION_MECHANICAL_LIMIT_CONFIG,
            "max": ICF_ROTATION_MECHANICAL_LIMIT_CONFIG,
        }
        assert config["motion"]["rightSoftLimits"][axis_key] == {
            "min": -ICF_ROTATION_MECHANICAL_LIMIT_CONFIG,
            "max": ICF_ROTATION_MECHANICAL_LIMIT_CONFIG,
        }
    assert config["motion"]["kinematics"]["rightPhysicalAxis"] == [2, 0, 5, 8, 1, 7]
    assert config["motion"]["kinematics"]["rightSignedPulsePerUnit"] == [
        -5000.0,
        -10000.0,
        -5000.0,
        1666.666667,
        2500.0,
        333.3333,
    ]
    assert config["motion"]["kinematics"]["leftSignedPulsePerUnit"] == [
        -5000.0,
        5000.0,
        -10000.0,
        1666.666667,
        -2500.0,
        -3333.333,
    ]
    assert config["motion"]["workOriginStrategyVersion"] == ICF_WORK_ORIGIN_VERSION
    assert config["motion"]["origin"]["leftPulse"] == [258494.0, -200013.0, 274821.0, 49833.0, 84839.0, 381102.0]
    assert config["motion"]["origin"]["rightPulse"] == [99772.0, 382486.0, 881207.0, 19527.0, -175127.0, -9668.0]
    assert config["gripper"]["leftPort"] == "COM8"
    assert config["gripper"]["rightPort"] == "COM9"
    assert config["teleop"]["gripperTeleop"]["leftSourceHand"] == "PhysicalLeft"
    assert config["teleop"]["gripperTeleop"]["rightSourceHand"] == "PhysicalRight"
    assert config["teleop"]["gripperTeleop"]["rightGapInvert"] is False
    assert config["teleop"]["gripperTeleop"]["autoGapCalibration"] is True


def test_settings_migration_updates_aggressive_teleop_defaults_to_stable_window(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["strategyVersion"] = "e2e_omega7_native_v28_gripper_python_right_y_20260611"
    config["teleop"]["leftTranslationScale"] = 1.25
    config["teleop"]["rightTranslationScale"] = 1.25
    config["teleop"]["leftRotationScale"] = 1.20
    config["teleop"]["rightRotationScale"] = 1.20
    config["teleop"]["leftAxisOutputScale"] = [0.65, 0.45, 0.45, 0.60, 0.16, 0.20]
    config["teleop"]["rightAxisOutputScale"] = [0.65, 0.45, 0.45, 0.55, 0.16, 0.25]
    config["teleop"]["translationStartVelocityUmS"] = 1500.0
    config["teleop"]["translationMaxVelocityUmS"] = 20000.0
    config["teleop"]["rotationStartVelocityDegS"] = 2.5
    config["teleop"]["rotationMaxVelocityDegS"] = 30.0
    config["teleop"]["motionProfileAccSec"] = 0.03
    config["teleop"]["motionProfileDecSec"] = 0.03
    config["teleop"]["translationPulseDeadband"] = 0
    config["teleop"]["rotationPulseDeadband"] = 0
    config["teleop"]["translationInputEpsilon"] = 0.0000001
    config["teleop"]["rotationInputEpsilon"] = 0.001
    config["teleop"]["translationMinActivePulse"] = 1
    config["teleop"]["rotationMinActivePulse"] = 1
    config["teleop"]["continuousMicroConfirmTicks"] = 0
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["teleop"]["leftTranslationScale"] == 1.0
    assert migrated["teleop"]["rightTranslationScale"] == 1.0
    assert migrated["teleop"]["leftRotationScale"] == 1.0
    assert migrated["teleop"]["rightRotationScale"] == 1.0
    assert migrated["teleop"]["leftAxisOutputScale"] == [0.60, 0.50, 0.375, 0.60, 0.08, 0.10]
    assert migrated["teleop"]["rightAxisOutputScale"] == [0.60, 0.50, 0.375, 0.60, 0.08, 0.001]
    assert migrated["teleop"]["translationStartVelocityUmS"] == 600.0
    assert migrated["teleop"]["translationMaxVelocityUmS"] == 8000.0
    assert migrated["teleop"]["rotationStartVelocityDegS"] == 1.0
    assert migrated["teleop"]["rotationMaxVelocityDegS"] == 12.0
    assert migrated["teleop"]["motionProfileAccSec"] == 0.05
    assert migrated["teleop"]["motionProfileDecSec"] == 0.05
    assert migrated["teleop"]["translationPulseDeadband"] == 2
    assert migrated["teleop"]["rotationPulseDeadband"] == 2
    assert migrated["teleop"]["translationInputEpsilon"] == 0.00002
    assert migrated["teleop"]["rotationInputEpsilon"] == 0.03
    assert migrated["teleop"]["translationMinActivePulse"] == 3
    assert migrated["teleop"]["rotationMinActivePulse"] == 3
    assert migrated["teleop"]["continuousMicroConfirmTicks"] == 0


def test_settings_migration_updates_sluggish_gate_defaults_to_stable_window(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["strategyVersion"] = "e2e_omega7_native_v28_gripper_python_right_y_20260611"
    config["teleop"]["translationInputEpsilon"] = 0.000005
    config["teleop"]["rotationInputEpsilon"] = 0.08
    config["teleop"]["translationMinActivePulse"] = 3
    config["teleop"]["rotationMinActivePulse"] = 3
    config["teleop"]["continuousMicroConfirmTicks"] = 2
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["teleop"]["translationInputEpsilon"] == 0.00002
    assert migrated["teleop"]["rotationInputEpsilon"] == 0.03
    assert migrated["teleop"]["translationMinActivePulse"] == 3
    assert migrated["teleop"]["rotationMinActivePulse"] == 3
    assert migrated["teleop"]["continuousMicroConfirmTicks"] == 0


def test_settings_migration_preserves_user_tuned_responsiveness_values(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["translationStartVelocityUmS"] = 750.0
    config["teleop"]["translationMaxVelocityUmS"] = 9000.0
    config["teleop"]["rotationStartVelocityDegS"] = 1.25
    config["teleop"]["rotationMaxVelocityDegS"] = 15.0
    config["teleop"]["motionProfileAccSec"] = 0.06
    config["teleop"]["motionProfileDecSec"] = 0.07
    config["teleop"]["translationPulseDeadband"] = 1
    config["teleop"]["rotationPulseDeadband"] = 1
    config["teleop"]["translationInputEpsilon"] = 0.00001
    config["teleop"]["rotationInputEpsilon"] = 0.10
    config["teleop"]["translationMinActivePulse"] = 2
    config["teleop"]["rotationMinActivePulse"] = 2
    config["teleop"]["continuousMicroConfirmTicks"] = 1
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["teleop"]["translationStartVelocityUmS"] == 750.0
    assert migrated["teleop"]["translationMaxVelocityUmS"] == 9000.0
    assert migrated["teleop"]["rotationStartVelocityDegS"] == 1.25
    assert migrated["teleop"]["rotationMaxVelocityDegS"] == 15.0
    assert migrated["teleop"]["motionProfileAccSec"] == 0.06
    assert migrated["teleop"]["motionProfileDecSec"] == 0.07
    assert migrated["teleop"]["translationPulseDeadband"] == 1
    assert migrated["teleop"]["rotationPulseDeadband"] == 1
    assert migrated["teleop"]["translationInputEpsilon"] == 0.00001
    assert migrated["teleop"]["rotationInputEpsilon"] == 0.10
    assert migrated["teleop"]["translationMinActivePulse"] == 2
    assert migrated["teleop"]["rotationMinActivePulse"] == 2
    assert migrated["teleop"]["continuousMicroConfirmTicks"] == 1


def test_settings_migration_preserves_user_tuned_gripper_sources(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["strategyVersion"] = ICF_TELEOP_STRATEGY_VERSION
    config["teleop"]["gripperTeleop"]["leftSourceHand"] = "LeftHanded"
    config["teleop"]["gripperTeleop"]["rightSourceHand"] = "RightHanded"
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["teleop"]["gripperTeleop"]["leftSourceHand"] == "LeftHanded"
    assert migrated["teleop"]["gripperTeleop"]["rightSourceHand"] == "RightHanded"


def test_settings_save_atomically_backs_up_current_work_origin(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    settings = SettingsService(runtime_dir, LogService(emit_startup=False))
    config = settings.get_config()
    old_origin = {
        "valid": True,
        "leftValid": True,
        "rightValid": True,
        "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
        "updatedAt": 123,
        "previousValid": False,
        "previousLeftPulse": [0.0] * 6,
        "previousRightPulse": [0.0] * 6,
        "previousUpdatedAt": 0,
    }
    config["motion"]["origin"] = old_origin
    settings.save_config(config, emit_log=False)

    next_config = settings.get_config()
    next_config["motion"]["homeOnStartup"]["enabled"] = not bool(next_config["motion"]["homeOnStartup"]["enabled"])
    settings.save_config(next_config, emit_log=False)

    backups = sorted((runtime_dir / "_work_origin_backups").glob("*.json"))
    assert backups
    backup_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in backups]
    assert any(payload["origin"]["leftPulse"] == old_origin["leftPulse"] for payload in backup_payloads)
    assert not list(runtime_dir.glob("config.json.*.tmp"))


def test_settings_migration_updates_prior_right_roll_negative_only_window(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["strategyVersion"] = ICF_TELEOP_STRATEGY_VERSION
    config["motion"]["workOriginStrategyVersion"] = ICF_WORK_ORIGIN_VERSION
    config["teleop"]["rightSoftLimitMin"][3] = -100.0
    config["teleop"]["rightSoftLimitMax"][3] = 0.0
    config["motion"]["rotationWorkLimits"]["right"]["roll"] = {"min": -100.0, "max": 0.0}
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["teleop"]["rightSoftLimitMin"][3] == -95.0
    assert migrated["teleop"]["rightSoftLimitMax"][3] == 5.0
    assert migrated["motion"]["rotationWorkLimits"]["right"]["roll"] == {"min": -95.0, "max": 5.0}
    home_reference = side_home_reference_ui(migrated, "right")
    assert home_reference is not None
    limits = effective_limits_ui(migrated, "right")
    assert limits[3].min - home_reference[3] == pytest.approx(-95.0, abs=1e-6)
    assert limits[3].max - home_reference[3] == pytest.approx(5.0, abs=1e-6)


def test_settings_migration_preserves_right_roll_mechanical_limit_and_intersects_work_window(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["strategyVersion"] = ICF_TELEOP_STRATEGY_VERSION
    config["motion"]["workOriginStrategyVersion"] = ICF_WORK_ORIGIN_VERSION
    config["motion"]["origin"]["rightPulse"][3] = -674_985.0
    config["motion"]["origin"]["rightValid"] = True
    config["motion"]["origin"]["valid"] = True
    config["motion"]["rightSoftLimits"]["roll"] = {
        "min": -78_283.80000234324,
        "max": 111_716.19999765676,
    }
    soft_limits_before = json.loads(json.dumps(config["motion"]["rightSoftLimits"]))
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["motion"]["rightSoftLimits"] == soft_limits_before
    home_reference = side_home_reference_ui(migrated, "right")
    assert home_reference is not None
    limits = effective_limits_ui(migrated, "right")
    assert limits[3].min < home_reference[3] < limits[3].max
    assert limits[3].min == pytest.approx(max(-78.28380000234324, home_reference[3] - 95.0), abs=1e-6)
    assert limits[3].max == pytest.approx(min(111.71619999765676, home_reference[3] + 5.0), abs=1e-6)


def test_settings_keeps_current_strategy_mechanical_soft_limits_stable(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["motion"]["homeReference"]["leftPulse"][3] = -392_532.0
    config["motion"]["origin"]["leftPulse"][3] = -1_095_580.0
    config["motion"]["leftSoftLimits"]["roll"] = {
        "min": -662_348.0,
        "max": -562_348.0,
    }
    soft_limits_before = json.loads(json.dumps(config["motion"]["leftSoftLimits"]))
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["motion"]["leftSoftLimits"] == soft_limits_before
    home_reference = side_home_reference_ui(migrated, "left")
    assert home_reference is not None
    limits = effective_limits_ui(migrated, "left")
    mechanical_min = soft_limits_before["roll"]["min"] / 1000.0
    mechanical_max = soft_limits_before["roll"]["max"] / 1000.0
    assert limits[3].min == pytest.approx(max(mechanical_min, home_reference[3] - 5.0), abs=1e-6)
    assert limits[3].max == pytest.approx(min(mechanical_max, home_reference[3] + 95.0), abs=1e-6)


def test_current_strategy_default_mechanical_limits_keep_refreshed_home_reference_valid(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["motion"]["homeReference"]["leftPulse"][3] = 34_404.0
    config["motion"]["homeReference"]["rightPulse"][3] = 439_830.0
    config["motion"]["origin"]["leftPulse"][3] = 34_404.0
    config["motion"]["origin"]["rightPulse"][3] = 439_830.0
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["motion"]["origin"]["leftValid"] is True
    assert migrated["motion"]["origin"]["rightValid"] is True
    for side in ("left", "right"):
        home_reference = side_home_reference_ui(migrated, side)
        assert home_reference is not None
        limits = effective_limits_ui(migrated, side)
        assert limits[3].min <= home_reference[3] <= limits[3].max


def test_default_rotation_work_limits_are_reenabled_for_stable_mechanical_defaults(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["motion"]["rotationWorkLimits"]["enabled"] = False
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["motion"]["rotationWorkLimits"]["enabled"] is True
    home_reference = side_home_reference_ui(migrated, "left")
    assert home_reference is not None
    limits = effective_limits_ui(migrated, "left")
    assert limits[3].min == pytest.approx(home_reference[3] - 5.0, abs=1e-6)
    assert limits[3].max == pytest.approx(home_reference[3] + 95.0, abs=1e-6)


def test_settings_migration_keeps_left_yaw_limit_anchored_to_home_reference(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["strategyVersion"] = ICF_TELEOP_STRATEGY_VERSION
    config["motion"]["workOriginStrategyVersion"] = ICF_WORK_ORIGIN_VERSION
    config["motion"]["origin"]["leftPulse"][5] = 1393.0
    config["motion"]["origin"]["leftValid"] = True
    config["motion"]["origin"]["valid"] = True
    config["motion"]["leftSoftLimits"]["yaw"] = {"min": -121_330.61143306113, "max": -107_330.61143306113}
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    home_reference = side_home_reference_ui(migrated, "left")
    assert home_reference is not None
    limits = effective_limits_ui(migrated, "left")
    assert limits[5].min < home_reference[5] < limits[5].max
    assert limits[5].min == pytest.approx(home_reference[5] - 7.0)
    assert limits[5].max == pytest.approx(home_reference[5] + 7.0)
    assert migrated["teleop"]["leftEnabledAxes"][5] is True
    assert migrated["teleop"]["rightEnabledAxes"][5] is True


def test_settings_migration_adds_home_reference_model_and_preserves_left_roll_mechanical_limit(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["strategyVersion"] = ICF_TELEOP_STRATEGY_VERSION
    config["motion"]["workOriginStrategyVersion"] = ICF_WORK_ORIGIN_VERSION
    config["motion"].pop("homeReferenceVersion", None)
    config["motion"].pop("homeReference", None)
    config["motion"].pop("workOriginOffset", None)
    config["motion"].pop("relativeSoftLimits", None)
    config["motion"]["origin"]["leftPulse"] = [100686.0, 66003.0, -68716.0, -152667.0, 5073.0, -297.0]
    config["motion"]["origin"]["leftValid"] = True
    config["motion"]["origin"]["valid"] = True
    config["motion"]["leftSoftLimits"]["roll"] = {
        "min": -70100.20000597996,
        "max": 129899.79999402004,
    }
    soft_limits_before = json.loads(json.dumps(config["motion"]["leftSoftLimits"]))
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["motion"]["homeReferenceVersion"] == ICF_HOME_REFERENCE_VERSION
    assert migrated["motion"]["homeReference"]["leftPulse"] == config["motion"]["origin"]["leftPulse"]
    assert migrated["motion"]["homeReference"]["leftValid"] is True
    assert migrated["motion"]["workOriginOffset"]["leftPulseDelta"] == [0.0] * 6
    assert migrated["motion"]["workOriginOffset"]["leftValid"] is True
    assert migrated["motion"]["relativeSoftLimits"]["left"]["roll"] == {"min": -5000.0, "max": 95000.0}
    assert migrated["motion"]["leftSoftLimits"] == soft_limits_before
    home_reference = side_home_reference_ui(migrated, "left")
    assert home_reference is not None
    limits = effective_limits_ui(migrated, "left")
    assert limits[3].min == pytest.approx(max(-70.10020000597996, home_reference[3] - 5.0), abs=1e-6)
    assert limits[3].max == pytest.approx(min(129.89979999402005, home_reference[3] + 95.0), abs=1e-6)


def test_settings_migration_updates_prior_right_pitch_window_and_yaw_axis(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["strategyVersion"] = ICF_TELEOP_STRATEGY_VERSION
    config["motion"]["workOriginStrategyVersion"] = ICF_WORK_ORIGIN_VERSION
    config["teleop"]["rightEnabledAxes"] = [True, True, True, True, True, True]
    config["teleop"]["rightSoftLimitMin"][4] = -100.0
    config["teleop"]["rightSoftLimitMax"][4] = 100.0
    config["motion"]["rotationWorkLimits"]["right"]["pitch"] = {"min": -100.0, "max": 100.0}
    origin = side_origin_ui(config, "right")
    assert origin is not None
    config["motion"]["rightSoftLimits"]["pitch"] = {
        "min": (origin[4] - 100.0) * 1000.0,
        "max": (origin[4] + 100.0) * 1000.0,
    }
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["teleop"]["rightEnabledAxes"] == [True] * 6
    assert migrated["teleop"]["rightSoftLimitMin"][4] == -30.0
    assert migrated["teleop"]["rightSoftLimitMax"][4] == 30.0
    assert migrated["motion"]["rotationWorkLimits"]["right"]["pitch"] == {"min": -30.0, "max": 30.0}
    limits = effective_limits_ui(migrated, "right")
    migrated_reference = side_home_reference_ui(migrated, "right")
    assert migrated_reference is not None
    assert limits[4].min - migrated_reference[4] == pytest.approx(-30.0, abs=1e-6)
    assert limits[4].max - migrated_reference[4] == pytest.approx(30.0, abs=1e-6)


def test_rotation_work_limit_missing_axis_defaults_to_icf_window() -> None:
    config = default_config()
    config["motion"]["rotationWorkLimits"]["left"].pop("roll")
    config["motion"]["rotationWorkLimits"]["right"].pop("roll")
    config["motion"]["rotationWorkLimits"]["right"].pop("pitch")

    left_limits = rotation_work_limits_ui(config, "left")
    right_limits = rotation_work_limits_ui(config, "right")

    assert left_limits[3].min == -5.0
    assert left_limits[3].max == 95.0
    assert right_limits[3].min == -95.0
    assert right_limits[3].max == 5.0
    assert right_limits[4].min == -30.0
    assert right_limits[4].max == 30.0


def test_settings_migration_updates_current_axis_output_defaults_to_stable_window(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["strategyVersion"] = ICF_TELEOP_STRATEGY_VERSION
    config["teleop"]["leftAxisOutputScale"] = [0.40, 0.25, 0.25, 0.40, 0.20, 0.20]
    config["teleop"]["rightAxisOutputScale"] = [0.40, 0.25, 0.25, 0.40, 0.20, 0.20]
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["teleop"]["leftAxisOutputScale"] == [0.60, 0.50, 0.375, 0.60, 0.08, 0.10]
    assert migrated["teleop"]["rightAxisOutputScale"] == [0.60, 0.50, 0.375, 0.60, 0.08, 0.001]


def test_settings_migration_updates_prior_pitch_yaw_axis_output_defaults_to_stable_window(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["strategyVersion"] = ICF_TELEOP_STRATEGY_VERSION
    config["teleop"]["leftAxisOutputScale"] = [0.40, 0.25, 0.25, 0.40, 0.10, 0.15]
    config["teleop"]["rightAxisOutputScale"] = [0.40, 0.25, 0.25, 0.40, 0.10, 0.15]
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["teleop"]["leftAxisOutputScale"] == [0.60, 0.50, 0.375, 0.60, 0.08, 0.10]
    assert migrated["teleop"]["rightAxisOutputScale"] == [0.60, 0.50, 0.375, 0.60, 0.08, 0.001]


def test_settings_does_not_reanchor_current_strategy_mechanical_soft_limits_to_home_reference(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    old_config = default_config()
    old_config["motion"]["origin"]["leftPulse"][5] = 27504.0
    old_config["motion"]["leftSoftLimits"]["yaw"] = {"min": -8000.0, "max": 8000.0}
    soft_limits_before = json.loads(json.dumps(old_config["motion"]["leftSoftLimits"]))
    (runtime_dir / "config.json").write_text(json.dumps(old_config), encoding="utf-8")

    config = SettingsService(runtime_dir, LogService()).get_config()

    assert config["motion"]["leftSoftLimits"] == soft_limits_before
    home_reference = side_home_reference_ui(config, "left")
    assert home_reference is not None
    limits = effective_limits_ui(config, "left")
    assert limits[5].min == pytest.approx(max(-8.0, home_reference[5] - 7.0), abs=1e-6)
    assert limits[5].max == pytest.approx(min(8.0, home_reference[5] + 7.0), abs=1e-6)


def test_settings_migration_updates_legacy_icf_translation_speed(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    old_config = default_config()
    old_config["teleop"]["strategyVersion"] = ICF_TELEOP_STRATEGY_VERSION
    old_config["teleop"]["translationStartVelocityUmS"] = 400.0
    old_config["teleop"]["translationMaxVelocityUmS"] = 5000.0
    (runtime_dir / "config.json").write_text(json.dumps(old_config), encoding="utf-8")

    config = SettingsService(runtime_dir, LogService()).get_config()

    assert config["teleop"]["translationStartVelocityUmS"] == 600.0
    assert config["teleop"]["translationMaxVelocityUmS"] == 8000.0


def test_settings_migration_updates_legacy_reversed_wrist_cameras(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    old_config = default_config()
    old_config["cameras"]["global"] = "AR0234 / index 2"
    old_config["cameras"]["wristLeft"] = "IMX258 / index 1"
    old_config["cameras"]["wristLeftIdentity"] = "USB\\VID_0EDC&PID_3080&MI_00\\7&38B4EA25&0&0000"
    old_config["cameras"]["wristRight"] = "IMX258 / index 0"
    old_config["cameras"]["wristRightIdentity"] = "USB\\VID_0EDC&PID_3080&MI_00\\6&1BBFDB86&0&0000"
    (runtime_dir / "config.json").write_text(json.dumps(old_config), encoding="utf-8")

    config = SettingsService(runtime_dir, LogService()).get_config()

    assert config["cameras"]["global"] == "IMX335 / index 1"
    assert config["cameras"]["globalIdentity"] == "USB\\VID_0ABD&PID_8050&MI_00\\7&1396F44D&0&0000"
    assert config["cameras"]["wristLeft"] == "IMX335 / index 0"
    assert config["cameras"]["wristLeftIdentity"] == "USB\\VID_0ABD&PID_8050&MI_00\\7&398F0A3&0&0000"
    assert config["cameras"]["wristRight"] == "IMX335 / index 2"
    assert config["cameras"]["wristRightIdentity"] == "USB\\VID_0ABD&PID_8050&MI_00\\8&3724732E&0&0000"


def test_settings_migration_updates_previous_imx258_camera_defaults(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    old_config = default_config()
    old_config["cameras"]["global"] = "AR0234 / index 1"
    old_config["cameras"]["wristLeft"] = "IMX258 / index 2"
    old_config["cameras"]["wristRight"] = "IMX258 / index 0"
    (runtime_dir / "config.json").write_text(json.dumps(old_config), encoding="utf-8")

    config = SettingsService(runtime_dir, LogService()).get_config()

    assert config["cameras"]["global"] == "IMX335 / index 1"
    assert config["cameras"]["wristLeft"] == "IMX335 / index 0"
    assert config["cameras"]["wristRight"] == "IMX335 / index 2"


def test_settings_migration_updates_previous_imx335_camera_defaults(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    old_config = default_config()
    old_config["cameras"]["global"] = "IMX335 / index 1"
    old_config["cameras"]["globalIdentity"] = "USB\\VID_0ABD&PID_8050&MI_00\\7&124CCBA8&0&0000"
    old_config["cameras"]["wristLeft"] = "IMX335 / index 2"
    old_config["cameras"]["wristLeftIdentity"] = "USB\\VID_0ABD&PID_8050&MI_00\\7&7861A93&0&0000"
    old_config["cameras"]["wristRight"] = "IMX335 / index 0"
    old_config["cameras"]["wristRightIdentity"] = "USB\\VID_0ABD&PID_8050&MI_00\\7&398F0A3&0&0000"
    (runtime_dir / "config.json").write_text(json.dumps(old_config), encoding="utf-8")

    config = SettingsService(runtime_dir, LogService()).get_config()

    assert config["cameras"]["globalIdentity"] == "USB\\VID_0ABD&PID_8050&MI_00\\7&1396F44D&0&0000"
    assert config["cameras"]["wristLeft"] == "IMX335 / index 0"
    assert config["cameras"]["wristLeftIdentity"] == "USB\\VID_0ABD&PID_8050&MI_00\\7&398F0A3&0&0000"
    assert config["cameras"]["wristRight"] == "IMX335 / index 2"
    assert config["cameras"]["wristRightIdentity"] == "USB\\VID_0ABD&PID_8050&MI_00\\8&3724732E&0&0000"


def test_settings_migration_updates_cyclic_camera_roles(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    old_config = default_config()
    old_config["cameras"]["global"] = "AR0234 / index 2"
    old_config["cameras"]["wristLeft"] = "IMX258 / index 0"
    old_config["cameras"]["wristRight"] = "IMX258 / index 1"
    (runtime_dir / "config.json").write_text(json.dumps(old_config), encoding="utf-8")

    config = SettingsService(runtime_dir, LogService()).get_config()

    assert config["cameras"]["global"] == "IMX335 / index 1"
    assert config["cameras"]["wristLeft"] == "IMX335 / index 0"
    assert config["cameras"]["wristRight"] == "IMX335 / index 2"
