from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from backend.core.config import SettingsService
from backend.core.defaults import (
    ICF_HOME_REFERENCE_VERSION,
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


class FakeHal:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self.native_status: dict[str, Any] = {
            "running": False,
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


class FailingTeleopHal(FakeHal):
    async def command(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.commands.append((name, payload))
        if name == "motion.teleop_target_update":
            raise RuntimeError("dmc_pmove failed ret=22 card=1 axis=0 deltaPulse=-7036")
        return {"ok": True}


class FailingNativeStopHal(FakeHal):
    async def command(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.commands.append((name, payload))
        if name == "teleop.native.stop":
            raise RuntimeError("HAL connection refused")
        return await super().command(name, payload)


class AppliedTeleopHal(FakeHal):
    async def command(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.commands.append((name, payload))
        if name == "motion.teleop_target_update":
            return {
                "mode": "real",
                "command": name,
                "response": {
                    "ok": True,
                    "appliedDeltas": [800.0, 0.0, 0.0, 0.02, 0.0, 0.0],
                    "updateReturn": [0.0, 0.0, 0.0, 21.0, 0.0, 0.0],
                    "clipped": [True, False, False, True, False, False],
                },
            }
        return {"ok": True}


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


def base_hand(clutch: bool = False) -> dict[str, Any]:
    return {
        "connected": True,
        "lastReadOk": True,
        "clutchPressed": clutch,
        "pose": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    }


def base_config(require_clutch: bool) -> dict[str, Any]:
    motion_soft_limits = {
        "x": {"min": -1000.0, "max": 1000.0},
        "y": {"min": -2000.0, "max": 2000.0},
        "z": {"min": -3000.0, "max": 3000.0},
        "roll": {"min": -40000.0, "max": 40000.0},
        "pitch": {"min": -50000.0, "max": 50000.0},
        "yaw": {"min": -60000.0, "max": 60000.0},
    }
    return {
        "teleop": {
            "leftConnected": True,
            "rightConnected": True,
            "leftTranslationScale": 0.30,
            "rightTranslationScale": 0.30,
            "leftRotationScale": 0.10,
            "rightRotationScale": 0.10,
            "leftAxisOutputScale": [0.20, 0.20, 0.20, 0.25, 0.25, 1.50],
            "rightAxisOutputScale": [0.20, 0.20, 0.20, 0.25, 0.25, 1.50],
            "leftImpulseCoeff": [-5000000, -5000000, -10000000, 1667, 2500, -333.3333],
            "rightImpulseCoeff": [-5000000, 10000000, -5000000, 1667, -2500, 3333.333],
            "translationStepUm": 5000.0,
            "rotationStepDeg": 0.2,
            "translationStepLimitPulse": 4000,
            "rotationStepLimitPulse": 1250,
            "translationPulseDeadband": 2,
            "rotationPulseDeadband": 2,
            "translationDeadzone": 0.00002,
            "rotationDeadzone": 0.03,
            "incrementalTranslationMinEffectiveDelta": 0.00005,
            "incrementalTranslationReverseDeadzone": 0.00010,
            "translationStartVelocityUmS": 300.0,
            "translationMaxVelocityUmS": 4000.0,
            "rotationStartVelocityDegS": 0.5,
            "rotationMaxVelocityDegS": 6.0,
            "motionProfileAccSec": 0.05,
            "motionProfileDecSec": 0.05,
            "continuousIncrementMode": True,
            "translationInputEpsilon": 0.00002,
            "rotationInputEpsilon": 0.03,
            "translationMinActivePulse": 3,
            "rotationMinActivePulse": 3,
            "continuousMicroConfirmTicks": 0,
            "leftEnabledAxes": [True, True, True, True, True, True],
            "rightEnabledAxes": [True, True, True, True, True, True],
            "leftSoftLimitMin": [-1000.0, -2000.0, -3000.0, -40.0, -50.0, -60.0],
            "leftSoftLimitMax": [1000.0, 2000.0, 3000.0, 40.0, 50.0, 60.0],
            "rightSoftLimitMin": [-1000.0, -2000.0, -3000.0, -40.0, -50.0, -60.0],
            "rightSoftLimitMax": [1000.0, 2000.0, 3000.0, 40.0, 50.0, 60.0],
            "requireClutch": require_clutch,
        },
        "motion": {
            "leftSoftLimits": json.loads(json.dumps(motion_soft_limits)),
            "rightSoftLimits": json.loads(json.dumps(motion_soft_limits)),
        },
    }


def start_config(
    *,
    valid_origin: bool = True,
    home_before_start: bool = True,
    engine: str = "python_mapper",
) -> dict[str, Any]:
    config = default_config()
    config["hal"]["mode"] = "real"
    config["teleop"]["engine"] = engine
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
        config = start_config(engine="hal_native")
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
        assert start_payload["leftConnected"] is True
        assert start_payload["rightConnected"] is False
        assert start_payload["controlMode"] == "incremental_position"
        assert start_payload["mappingMode"] == "direct"
        assert start_payload["nativeLoopHz"] == 100
        assert start_payload["leftTranslationScale"] == 1.25
        assert start_payload["rightTranslationScale"] == 1.25
        assert start_payload["leftRotationScale"] == 1.20
        assert start_payload["rightRotationScale"] == 1.20
        assert start_payload["leftAxisOutputScale"] == [0.65, 0.45, 0.45, 0.60, 0.16, 0.20]
        assert start_payload["rightAxisOutputScale"] == [0.65, 0.45, 0.45, 0.55, 0.16, 0.25]
        assert start_payload["leftImpulseCoeff"] == [-5000000, -5000000, -10000000, 1667, 2500, -333.3333]
        assert start_payload["rightImpulseCoeff"] == [-5000000, 10000000, -5000000, 1667, -2500, 3333.333]
        assert start_payload["gripperTeleopEnabled"] is False
        assert start_payload["leftSourceHand"] == "PhysicalRight"
        assert start_payload["rightSourceHand"] == "PhysicalLeft"
        assert start_payload["leftWorkOriginValid"] is True
        assert start_payload["rightWorkOriginValid"] is True
        assert start_payload["leftWorkOriginPulse"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        assert start_payload["rightWorkOriginPulse"] == [7.0, 8.0, 9.0, 10.0, 11.0, 12.0]
        assert start_payload["continuousIncrementMode"] is True
        assert start_payload["translationStartVelocityUmS"] == pytest.approx(1500.0)
        assert start_payload["translationMaxVelocityUmS"] == pytest.approx(20000.0)
        assert start_payload["rotationStartVelocityDegS"] == pytest.approx(2.5)
        assert start_payload["rotationMaxVelocityDegS"] == pytest.approx(30.0)
        assert start_payload["motionProfileAccSec"] == pytest.approx(0.03)
        assert start_payload["motionProfileDecSec"] == pytest.approx(0.03)
        assert start_payload["translationInputEpsilon"] == pytest.approx(0.000005)
        assert start_payload["rotationInputEpsilon"] == pytest.approx(0.08)
        assert start_payload["translationMinActivePulse"] == pytest.approx(3.0)
        assert start_payload["rotationMinActivePulse"] == pytest.approx(3.0)
        assert start_payload["continuousMicroConfirmTicks"] == 2
        assert hal.commands[-1][0] == "teleop.native.stop"

    asyncio.run(run())


def test_native_teleop_connect_does_not_enable_hal_gripper_follow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config(engine="hal_native")
        config["teleop"]["leftConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)

        configure_payloads = [payload for name, payload in hal.commands if name == "teleop.native.configure"]
        assert configure_payloads[-1]["gripperTeleopEnabled"] is False

    asyncio.run(run())


def test_native_manual_gripper_source_does_not_enable_hal_gripper_follow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config(engine="hal_native")
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("manual-gripper", pre_home=False)

        configure_payloads = [payload for name, payload in hal.commands if name == "teleop.native.configure"]
        assert configure_payloads[-1]["gripperTeleopEnabled"] is False

    asyncio.run(run())


def test_native_gripper_teleop_start_does_not_enable_arm_motion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config(engine="hal_native")
        config["teleop"]["leftConnected"] = True
        config["teleop"]["rightConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("manual-gripper", pre_home=False)

        configure_payloads = [payload for name, payload in hal.commands if name == "teleop.native.configure"]
        assert configure_payloads[-1]["gripperTeleopEnabled"] is False
        assert configure_payloads[-1]["leftConnected"] is False
        assert configure_payloads[-1]["rightConnected"] is False

    asyncio.run(run())


def test_native_teleop_running_update_uses_start_without_extra_configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config(engine="hal_native")
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


def test_native_teleop_running_origin_change_forces_rehome_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        logs = LogService()
        hal = FakeHal()
        config = start_config(engine="hal_native")
        config["teleop"]["leftConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=logs)

        await mapper.start("teleop-connect", pre_home=False)
        hal.commands.clear()
        config["motion"]["origin"]["leftPulse"] = [101.0, 102.0, 103.0, 104.0, 105.0, 106.0]

        await mapper.start("teleop-connect", pre_home=False)

        assert [name for name, _ in hal.commands] == [
            "teleop.native.stop",
            "motion.home_origin_side",
            "teleop.native.start",
        ]
        assert hal.commands[1][1] == {
            "side": "left",
            "pulse": [101.0, 102.0, 103.0, 104.0, 105.0, 106.0],
            "enabledAxes": [True, True, True, True, True, True],
        }
        assert hal.commands[-1][1]["leftWorkOriginPulse"] == [101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
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
        config = start_config(engine="hal_native")
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
        config = start_config(engine="hal_native")
        config["teleop"]["leftConnected"] = False
        config["teleop"]["rightConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)
        hal.commands.clear()
        config["motion"]["homeReference"]["leftPulse"][3] = -1_000_000.0

        await mapper.start("teleop-connect", pre_home=False)

        assert [name for name, _ in hal.commands] == ["teleop.native.start"]
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
        config = start_config(engine="hal_native")
        config["teleop"]["leftConnected"] = False
        config["teleop"]["rightConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("manual-gripper", pre_home=False)
        hal.commands.clear()
        config["motion"]["homeReference"]["leftPulse"][3] = -1_000_000.0

        await mapper.start("teleop-connect", pre_home=True, home_side="left")

        assert [name for name, _ in hal.commands] == ["teleop.native.start"]
        assert mapper.status()["armed"] is True
        assert mapper.status()["running"] is True
        assert mapper.status()["sources"] == ["manual-gripper", "teleop-connect"]

    asyncio.run(run())


def test_native_teleop_stop_can_remove_aux_source_without_restarting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config(engine="hal_native")
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)
        await mapper.start("recording", pre_home=False)
        hal.commands.clear()

        await mapper.stop("teleop-connect", restart_remaining=False)

        assert hal.commands == []
        assert mapper.status()["armed"] is True
        assert mapper.status()["sources"] == ["recording"]

        await mapper.stop("recording")

        assert [name for name, _ in hal.commands] == ["teleop.native.stop"]

    asyncio.run(run())


def test_native_teleop_stop_restart_enters_recovery_for_remaining_arm_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = DriftedMotionStateHal()
        config = start_config(engine="hal_native")
        config["teleop"]["leftConnected"] = False
        config["teleop"]["rightConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)
        await mapper.start("recording", pre_home=False)
        hal.commands.clear()
        config["motion"]["homeReference"]["leftPulse"][3] = -1_000_000.0

        await mapper.stop("recording")

        assert [name for name, _ in hal.commands] == ["teleop.native.configure", "teleop.native.start"]
        assert mapper.status()["armed"] is True
        assert mapper.status()["running"] is True
        assert mapper.status()["sources"] == ["teleop-connect"]

    asyncio.run(run())


def test_native_teleop_start_failure_rolls_back_arm_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = NativeStartTimeoutHal()
        config = start_config(engine="hal_native")
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
        config = start_config(engine="hal_native", home_before_start=False)
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
        config = start_config(engine="hal_native", home_before_start=False)
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
        config = start_config(engine="hal_native", home_before_start=False)
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
            settings=FakeSettings(start_config(engine="hal_native")),
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


def test_native_status_loop_uses_gripper_sample_rate_for_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config(engine="hal_native")
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
        config = start_config(engine="hal_native")
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
        config = start_config(engine="hal_native")
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
        config = start_config(engine="hal_native")
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
        config = start_config(engine="hal_native")
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
            settings=FakeSettings(start_config(engine="hal_native")),
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
        config = start_config(engine="hal_native", home_before_start=False)
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
        config = start_config(engine="hal_native", home_before_start=False)
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
                    "rightEnabledAxes": [True, True, True, True, True, False],
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
                    "enabledAxes": [True, True, True, True, True, False],
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


def test_teleop_prehome_blocks_stale_hardware_zero_before_motion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = DriftedMotionStateHal()
        config = start_config(engine="hal_native")
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
        config = start_config(engine="hal_native", home_before_start=False)
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
        config = start_config(engine="hal_native")
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
        config = start_config(engine="hal_native")
        config["teleop"]["leftConnected"] = False
        config["teleop"]["rightConnected"] = True
        config["motion"]["homeReference"]["leftPulse"][3] = -1_000_000.0
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)

        assert [name for name, _ in hal.commands[:2]] == ["teleop.native.configure", "teleop.native.start"]
        assert mapper.status()["armed"] is True

    asyncio.run(run())


def test_native_gripper_teleop_start_ignores_stale_arm_connection_without_native_gripper_follow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = DriftedMotionStateHal()
        config = start_config(engine="hal_native")
        config["teleop"]["leftConnected"] = False
        config["teleop"]["rightConnected"] = True
        config["motion"]["homeReference"]["leftPulse"][3] = -1_000_000.0
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("manual-gripper", pre_home=False)

        assert [name for name, _ in hal.commands[:2]] == ["teleop.native.configure", "teleop.native.start"]
        assert hal.commands[0][1]["leftConnected"] is False
        assert hal.commands[0][1]["rightConnected"] is False
        assert hal.commands[0][1]["gripperTeleopEnabled"] is False
        assert mapper.status()["armed"] is True

    asyncio.run(run())


def test_teleop_mapper_sends_continuous_six_axis_delta() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.03, -0.03, 0.0005, 2.0, -2.0, 1.0]
        await mapper._step_side("left", hand, config)

        assert hal.commands
        name, payload = hal.commands[0]
        assert name == "motion.teleop_target_update"
        assert payload["side"] == "left"
        assert payload["deltas"]["X"] == 1800.0
        assert payload["deltas"]["Y"] == 1800.0
        assert payload["deltas"]["Z"] == 30.0
        assert payload["deltas"]["Roll"] == pytest.approx(0.0498)
        assert payload["deltas"]["Pitch"] == 0.05
        assert payload["deltas"]["Yaw"] == pytest.approx(0.015)
        assert payload["translationStepUm"] == 5000.0
        assert payload["rotationStepDeg"] == 0.2
        assert payload["translationStepLimitPulse"] == 4000
        assert payload["rotationStepLimitPulse"] == 1250
        assert payload["translationPulseDeadband"] == 2
        assert payload["rotationPulseDeadband"] == 2
        assert payload["enabledAxes"] == [True, True, True, True, True, True]
        assert payload["syncZeroDeltaTarget"] is True
        assert payload["softLimitMin"] == [-1000000000.0, -1000000000.0, -1000000000.0, -40.0, -50.0, -60.0]
        assert payload["softLimitMax"] == [1000000000.0, 1000000000.0, 1000000000.0, 40.0, 50.0, 60.0]
        assert payload["translationVelocityUiPerSec"] == 4000.0
        assert payload["rotationVelocityUiPerSec"] == 6.0
        assert payload["translationStartVelocityUiPerSec"] == 300.0
        assert payload["rotationStartVelocityUiPerSec"] == 0.5
        assert mapper.status()["lastAction"]["deltaVector"] == pytest.approx([
            1800.0,
            1800.0,
            30.0,
            0.0498,
            0.05,
            0.015,
            0,
            0,
            0,
            0,
            0,
            0,
        ])
        assert mapper.status()["lastAction"]["requestedPulseDeltas"] == {
            "X": -9000.0,
            "Y": 9000.0,
            "Z": -300.0,
            "Roll": 83.0,
            "Pitch": -125.0,
            "Yaw": -50.0,
        }
        assert mapper.status()["actionHistory"][-1]["deltaVector"] == mapper.status()["lastAction"]["deltaVector"]
        assert mapper.status()["actionHistory"][-1]["monotonic_s"] > 0

    asyncio.run(run())


def test_teleop_mapper_ignores_legacy_teleop_soft_limit_arrays() -> None:
    async def run_case(raw_min: list[float], raw_max: list[float]) -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        config["teleop"]["leftSoftLimitMin"] = raw_min
        config["teleop"]["leftSoftLimitMax"] = raw_max
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        await mapper._step_side("left", hand, config)

        _, payload = hal.commands[0]
        assert payload["softLimitMin"] == [-1000000000.0, -1000000000.0, -1000000000.0, -40.0, -50.0, -60.0]
        assert payload["softLimitMax"] == [1000000000.0, 1000000000.0, 1000000000.0, 40.0, 50.0, 60.0]

    asyncio.run(run_case([-1.0] * 5, [1.0] * 6))
    asyncio.run(run_case([10.0] * 6, [9.0] * 6))


def test_teleop_mapper_disables_translation_soft_limits_without_origin_offsets() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        config["teleop"]["swapTeleopChannels"] = True
        config["motion"]["origin"] = {
            "valid": True,
            "leftValid": True,
            "rightValid": True,
            "leftPulse": [0.0] * 6,
            "rightPulse": [99769.0, 382483.0, 881210.0, -35473.0, -215115.0, -5006.0],
            "updatedAt": 100,
        }
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        await mapper._step_side("left", hand, config)

        _, payload = hal.commands[0]
        assert payload["side"] == "right"
        assert payload["softLimitMin"] == [-1000000000.0, -1000000000.0, -1000000000.0, -40.0, -50.0, -60.0]
        assert payload["softLimitMax"] == [1000000000.0, 1000000000.0, 1000000000.0, 40.0, 50.0, 60.0]

    asyncio.run(run())


def test_teleop_mapper_uses_configured_step_limits() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        config["teleop"]["translationStepLimitPulse"] = 100.0
        config["teleop"]["rotationStepLimitPulse"] = 50.0
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.001, 0.0, 0.0, 1.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        _, payload = hal.commands[0]
        assert payload["deltas"]["X"] == 60.0
        assert payload["deltas"]["Roll"] == pytest.approx(0.0252)
        assert payload["translationStepLimitPulse"] == 100.0
        assert payload["rotationStepLimitPulse"] == 50.0

    asyncio.run(run())


def test_teleop_mapper_honors_icf_swapped_motion_channels() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        config["teleop"]["swapTeleopChannels"] = True
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.001, 0.0, 0.0, 0.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        _, payload = hal.commands[0]
        assert payload["side"] == "right"
        assert mapper.status()["lastAction"]["sourceSide"] == "left"
        assert mapper.status()["lastAction"]["side"] == "right"
        assert mapper.status()["lastAction"]["deltaVector"][:6] == [0.0] * 6
        assert mapper.status()["lastAction"]["deltaVector"][6] == 60.0

    asyncio.run(run())


def test_teleop_mapper_uses_target_stage_impulse_when_channels_are_swapped() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        config["teleop"]["swapTeleopChannels"] = True
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.0, 0.001, 0.0, 0.0, 1.0, 0.0]
        await mapper._step_side("left", hand, config)

        _, payload = hal.commands[0]
        assert payload["side"] == "right"
        assert payload["deltas"]["Y"] == -60.0
        assert payload["deltas"]["Pitch"] == pytest.approx(-0.0252)
        assert mapper.status()["lastAction"]["requestedPulseDeltas"]["Y"] == 600.0
        assert mapper.status()["lastAction"]["requestedPulseDeltas"]["Pitch"] == -63.0

    asyncio.run(run())


def test_teleop_mapper_pitch_uses_corrected_target_direction_on_both_swapped_arms() -> None:
    async def run() -> None:
        config = base_config(require_clutch=False)
        config["teleop"]["swapTeleopChannels"] = True

        left_hal = FakeHal()
        left_mapper = TeleopMappingService(settings=None, hal=left_hal, logs=None)  # type: ignore[arg-type]
        left_hand = base_hand(clutch=False)
        await left_mapper._step_side("left", left_hand, config)
        left_hand["pose"] = [0.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        await left_mapper._step_side("left", left_hand, config)

        right_hal = FakeHal()
        right_mapper = TeleopMappingService(settings=None, hal=right_hal, logs=None)  # type: ignore[arg-type]
        right_hand = base_hand(clutch=False)
        await right_mapper._step_side("right", right_hand, config)
        right_hand["pose"] = [0.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        await right_mapper._step_side("right", right_hand, config)

        _, left_payload = left_hal.commands[0]
        _, right_payload = right_hal.commands[0]
        assert left_payload["side"] == "right"
        assert right_payload["side"] == "left"
        assert left_payload["deltas"]["Pitch"] < 0.0
        assert right_payload["deltas"]["Pitch"] < 0.0

    asyncio.run(run())


def test_teleop_mapper_left_stage_y_matches_manual_direction_when_channels_are_swapped() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        config["teleop"]["swapTeleopChannels"] = True
        hand = base_hand(clutch=False)

        await mapper._step_side("right", hand, config)
        hand["pose"] = [0.0, 0.001, 0.0, 0.0, 0.0, 0.0]
        await mapper._step_side("right", hand, config)

        _, payload = hal.commands[0]
        assert payload["side"] == "left"
        assert payload["deltas"]["Y"] == -60.0
        assert mapper.status()["lastAction"]["requestedPulseDeltas"]["Y"] == -300.0

    asyncio.run(run())


def test_teleop_mapper_balances_swapped_z_output_with_target_stage_coefficients() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        config["teleop"]["swapTeleopChannels"] = True
        left_hand = base_hand(clutch=False)
        right_hand = base_hand(clutch=False)

        await mapper._step_side("left", left_hand, config)
        left_hand["pose"] = [0.0, 0.0, 0.001, 0.0, 0.0, 0.0]
        await mapper._step_side("left", left_hand, config)
        right_z_delta = hal.commands[-1][1]["deltas"]["Z"]

        await mapper._step_side("right", right_hand, config)
        right_hand["pose"] = [0.0, 0.0, 0.001, 0.0, 0.0, 0.0]
        await mapper._step_side("right", right_hand, config)
        left_z_delta = hal.commands[-1][1]["deltas"]["Z"]

        assert left_z_delta == pytest.approx(right_z_delta)

    asyncio.run(run())


def test_teleop_mapper_records_hal_applied_delta_after_clipping() -> None:
    async def run() -> None:
        hal = AppliedTeleopHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.03, 0.0, 0.0, 2.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        _, payload = hal.commands[0]
        assert payload["deltas"]["X"] == 1800.0
        assert payload["deltas"]["Roll"] == pytest.approx(0.0498)

        last_action = mapper.status()["lastAction"]
        assert last_action["requestedDeltas"]["X"] == 1800.0
        assert last_action["requestedDeltas"]["Roll"] == pytest.approx(0.0498)
        assert last_action["deltas"]["X"] == 800.0
        assert last_action["deltas"]["Roll"] == 0.02
        assert last_action["appliedDeltas"]["X"] == 800.0
        assert last_action["updateReturn"]["Roll"] == 21.0
        assert last_action["deltaVector"] == [800.0, 0.0, 0.0, 0.02, 0.0, 0.0, 0, 0, 0, 0, 0, 0]

    asyncio.run(run())


def test_teleop_mapper_accumulates_small_translation_until_effective_delta() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        config["teleop"]["continuousIncrementMode"] = False
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.00003, 0.0, 0.0, 0.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.00006, 0.0, 0.0, 0.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        assert hal.commands[0][1]["syncZeroDeltaTarget"] is True
        assert hal.commands[0][1]["deltas"]["X"] == 0.0
        assert hal.commands[1][1]["syncZeroDeltaTarget"] is True
        assert hal.commands[1][1]["deltas"]["X"] == pytest.approx(3.6)

    asyncio.run(run())


def test_teleop_mapper_uses_reverse_deadzone_for_translation_direction_change() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        config["teleop"]["continuousIncrementMode"] = False
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.00005, 0.0, 0.0, 0.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)
        hand["pose"] = [-0.00003, 0.0, 0.0, 0.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)
        hand["pose"] = [-0.000055, 0.0, 0.0, 0.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        assert hal.commands[0][1]["deltas"]["X"] == pytest.approx(3.0)
        assert hal.commands[1][1]["syncZeroDeltaTarget"] is True
        assert hal.commands[2][1]["deltas"]["X"] == pytest.approx(-6.4)

    asyncio.run(run())


def test_teleop_mapper_keeps_yaw_incremental() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        config["teleop"]["rotationStepDeg"] = 0.2
        hand["pose"] = [0.0, 0.0, 0.0, 0.0, 0.0, 10.0]
        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.0, 0.0, 0.0, 0.0, 0.0, 20.0]
        await mapper._step_side("left", hand, config)
        await mapper._step_side("left", hand, config)

        assert hal.commands[0][1]["deltas"]["Yaw"] == pytest.approx(0.15)
        assert hal.commands[1][1]["deltas"]["Yaw"] == pytest.approx(0.15)
        assert hal.commands[2][1]["deltas"]["Yaw"] == 0.0

    asyncio.run(run())


def test_teleop_mapper_sends_zero_delta_target_sync() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        await mapper._step_side("left", hand, config)

        name, payload = hal.commands[0]
        assert name == "motion.teleop_target_update"
        assert payload["syncZeroDeltaTarget"] is True
        assert payload["deltas"] == {axis: 0.0 for axis in ("X", "Y", "Z", "Roll", "Pitch", "Yaw")}
        assert mapper.status()["lastAction"]["deltaVector"] == [0.0] * 12

    asyncio.run(run())


def test_teleop_mapper_ignores_tiny_continuous_noise_until_real_icf_increment() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.0000002, 0.0, 0.0, 0.002, 0.0, 0.0]
        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.0000004, 0.0, 0.0, 0.004, 0.0, 0.0]
        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.00003, 0.0, 0.0, 0.6, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        first_noise_payload = hal.commands[0][1]
        second_noise_payload = hal.commands[1][1]
        first_motion_payload = hal.commands[2][1]
        assert first_noise_payload["deltas"] == {axis: 0.0 for axis in ("X", "Y", "Z", "Roll", "Pitch", "Yaw")}
        assert second_noise_payload["deltas"] == {axis: 0.0 for axis in ("X", "Y", "Z", "Roll", "Pitch", "Yaw")}
        assert first_motion_payload["deltas"]["X"] == pytest.approx(1.8)
        assert first_motion_payload["deltas"]["Roll"] == pytest.approx(0.015, rel=1e-3)
        assert mapper.status()["actionHistory"][2]["requestedPulseDeltas"]["X"] == -9.0
        assert mapper.status()["actionHistory"][2]["requestedPulseDeltas"]["Roll"] == 25.0

    asyncio.run(run())


def test_teleop_mapper_requires_two_same_direction_rotation_micro_ticks() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        hand = base_hand(clutch=False)
        micro_tick_deg = 0.0878906

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.0, 0.0, 0.0, micro_tick_deg, 0.0, 0.0]
        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.0, 0.0, 0.0, micro_tick_deg * 2.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        first_payload = hal.commands[0][1]
        second_payload = hal.commands[1][1]
        assert first_payload["deltas"]["Roll"] == 0.0
        assert first_payload["syncZeroDeltaTarget"] is True
        assert second_payload["deltas"]["Roll"] != 0.0

    asyncio.run(run())


def test_teleop_mapper_resets_rotation_micro_confirmation_on_direction_change() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        hand = base_hand(clutch=False)
        micro_tick_deg = 0.0878906

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.0, 0.0, 0.0, micro_tick_deg, 0.0, 0.0]
        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.0, 0.0, 0.0, -micro_tick_deg, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        assert hal.commands[0][1]["deltas"]["Roll"] == 0.0
        assert hal.commands[1][1]["deltas"]["Roll"] == 0.0
        assert hal.commands[2][1]["deltas"]["Roll"] != 0.0

    asyncio.run(run())


def test_teleop_mapper_applies_large_rotation_input_without_micro_confirmation_delay() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.0, 0.0, 0.0, 0.18, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        assert hal.commands[0][1]["deltas"]["Roll"] != 0.0

    asyncio.run(run())


def test_teleop_mapper_requires_two_same_direction_translation_micro_ticks() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        config["teleop"]["translationInputEpsilon"] = 0.000005
        hand = base_hand(clutch=False)
        micro_tick_m = 0.000006

        await mapper._step_side("left", hand, config)
        hand["pose"] = [micro_tick_m, 0.0, 0.0, 0.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)
        hand["pose"] = [micro_tick_m * 2.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        assert hal.commands[0][1]["deltas"]["X"] == 0.0
        assert hal.commands[1][1]["deltas"]["X"] != 0.0

    asyncio.run(run())


def test_teleop_mapper_applies_larger_continuous_increment_without_confirmation_delay() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.001, 0.0, 0.0, 1.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        first_payload = hal.commands[0][1]
        assert first_payload["deltas"]["X"] == pytest.approx(60.0)
        assert first_payload["deltas"]["Roll"] == pytest.approx(0.0252)

    asyncio.run(run())


def test_teleop_mapper_does_not_skip_busy_axis_for_continuous_updates() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.001, 0.0, 0.0, 0.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.002, 0.0, 0.0, 0.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        assert [name for name, _ in hal.commands] == [
            "motion.teleop_target_update",
            "motion.teleop_target_update",
        ]

    asyncio.run(run())


def test_teleop_mapper_stops_side_when_hand_becomes_inactive() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.001, 0.0, 0.0, 0.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)
        hand["connected"] = False
        await mapper._step_side("left", hand, config)

        assert hal.commands[-1] == ("motion.teleop_stop_side", {"side": "left"})

    asyncio.run(run())


def test_teleop_mapper_resets_reference_and_stops_side_after_hal_failure() -> None:
    async def run() -> None:
        hal = FailingTeleopHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.002, 0.0, 0.0, 0.0, 0.0, 0.0]
        with pytest.raises(RuntimeError, match="dmc_pmove failed"):
            await mapper._step_side("left", hand, config)

        assert [name for name, _ in hal.commands] == [
            "motion.teleop_target_update",
            "motion.teleop_stop_side",
        ]

        hal.commands.clear()
        await mapper._step_side("left", hand, config)

        assert hal.commands == []

    asyncio.run(run())


def test_teleop_mapper_status_reports_updated_limits() -> None:
    mapper = TeleopMappingService(settings=None, hal=FakeHal(), logs=None)  # type: ignore[arg-type]

    assert mapper.status()["limits"] == {
        "translationStepUm": 5000.0,
        "rotationStepDeg": 0.2,
        "translationStepLimitPulse": 4000.0,
        "rotationStepLimitPulse": 1250.0,
        "translationPulseDeadband": 2.0,
        "rotationPulseDeadband": 2.0,
        "translationVelocityUmS": 20000.0,
        "rotationVelocityDegS": 30.0,
        "continuousIncrementMode": True,
        "translationInputEpsilon": 5e-06,
        "rotationInputEpsilon": 0.08,
        "translationMinActivePulse": 3.0,
        "rotationMinActivePulse": 3.0,
        "continuousMicroConfirmTicks": 2,
    }


def test_teleop_mapper_honors_clutch_when_required() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=True)
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.001, 0.0, 0.0, 0.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        assert hal.commands == []

    asyncio.run(run())


def test_teleop_mapper_status_reports_inactive_blockers() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=True)
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)

        blocker = mapper.status()["blockers"]["left"]
        assert blocker["active"] is False
        assert blocker["targetSide"] == "left"
        assert blocker["reasons"] == ["clutch is required but not pressed"]

    asyncio.run(run())


def test_teleop_mapper_diag_log_reports_requested_and_applied_motion() -> None:
    async def run() -> None:
        logs = LogService()
        hal = AppliedTeleopHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=logs)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        config["teleop"]["diagLog"] = True
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.03, 0.0, 0.0, 2.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        messages = [entry.msg for entry in logs.list_entries()]
        diag = next(message for message in messages if message.startswith("teleop diag left->left"))
        assert "clip=X,Roll" in diag
        assert "req=[X:1800,Roll:0.0498]" in diag
        assert "app=[X:800,Roll:0.02]" in diag
        assert "pulseReq=[X:-9000,Roll:83]" in diag
        assert "updateRet=[Roll:21]" in diag
        assert "latency=" in diag

    asyncio.run(run())


def test_hal_native_payload_disables_translation_soft_limits() -> None:
    config = start_config(engine="hal_native")
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
    config = start_config(engine="hal_native")
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
    config = start_config(engine="hal_native")
    config["gripper"]["jodellWorkerExePath"] = "F:/custom/JodellGripperWorker.exe"
    config["gripper"]["workerCommandTimeoutSec"] = 1.5
    mapper = TeleopMappingService(settings=FakeSettings(config), hal=FakeHal(), logs=LogService())

    payload = mapper._native_payload(config)

    assert payload["gripperProcessWorkersEnabled"] is True
    assert payload["jodellWorkerExePath"] == "F:/custom/JodellGripperWorker.exe"
    assert payload["gripperWorkerCommandTimeoutMs"] == 1500.0


def test_hal_native_payload_disables_translation_limits_and_sends_rotation_work_window() -> None:
    config = start_config(engine="hal_native")
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

    assert payload["leftSoftLimitMin"] == pytest.approx([-1000000000.0, -1000000000.0, -1000000000.0, 10.0, 20.0, -33.0])
    assert payload["leftSoftLimitMax"] == pytest.approx([1000000000.0, 1000000000.0, 1000000000.0, 50.0, 60.0, -22.0])
    assert payload["rotationWorkLimitEnabled"] is True
    assert payload["leftRotationWorkLimitMin"] == [0.0, 0.0, 0.0, -4.0, -5.0, -1.0]
    assert payload["leftRotationWorkLimitMax"] == [0.0, 0.0, 0.0, 6.0, 7.0, 1.0]


def test_hal_native_payload_sends_home_reference_for_soft_limit_anchor() -> None:
    config = start_config(engine="hal_native")
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


def test_python_teleop_payload_uses_effective_rotation_limits() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=LogService())  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        config["motion"]["origin"] = {
            "valid": False,
            "leftValid": True,
            "rightValid": False,
            "leftPulse": [0.0, 0.0, 0.0, 4000.0, 0.0, 0.0],
            "rightPulse": [0.0] * 6,
            "updatedAt": 1,
        }
        config["motion"]["homeReference"] = {
            "valid": False,
            "leftValid": True,
            "rightValid": False,
            "leftPulse": [0.0, 0.0, 0.0, 4000.0, 0.0, 0.0],
            "rightPulse": [0.0] * 6,
            "updatedAt": 1,
        }
        config["motion"]["leftSoftLimits"]["roll"] = {"min": 0.0, "max": 5000.0}
        config["motion"]["rotationWorkLimits"] = {
            "enabled": True,
            "left": {
                "roll": {"min": -1.0, "max": 1.0},
                "pitch": {"min": -100.0, "max": 100.0},
                "yaw": {"min": -7.0, "max": 7.0},
            },
            "right": {
                "roll": {"min": -100.0, "max": 100.0},
                "pitch": {"min": -100.0, "max": 100.0},
                "yaw": {"min": -7.0, "max": 7.0},
            },
        }
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.0, 0.0, 0.0, 2.0, 0.0, 0.0]
        await mapper._step_side("left", hand, config)

        update_payload = next(payload for name, payload in hal.commands if name == "motion.teleop_target_update")
        assert update_payload["softLimitMin"][3] == pytest.approx(1.4, abs=1e-3)
        assert update_payload["softLimitMax"][3] == pytest.approx(3.4, abs=1e-3)

    asyncio.run(run())


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
    assert config["teleop"]["leftTranslationScale"] == 1.25
    assert config["teleop"]["rightTranslationScale"] == 1.25
    assert config["teleop"]["leftRotationScale"] == 1.20
    assert config["teleop"]["rightRotationScale"] == 1.20
    assert config["teleop"]["mappingMode"] == "direct"
    assert config["teleop"]["leftAxisOutputScale"] == [0.65, 0.45, 0.45, 0.60, 0.16, 0.20]
    assert config["teleop"]["rightAxisOutputScale"] == [0.65, 0.45, 0.45, 0.55, 0.16, 0.25]
    assert config["teleop"]["translationStepLimitPulse"] == 4000
    assert config["teleop"]["rotationStepLimitPulse"] == 1250
    assert config["teleop"]["translationPulseDeadband"] == 2
    assert config["teleop"]["rotationPulseDeadband"] == 2
    assert config["teleop"]["translationStartVelocityUmS"] == 1500.0
    assert config["teleop"]["translationMaxVelocityUmS"] == 20000.0
    assert config["teleop"]["rotationStartVelocityDegS"] == 2.5
    assert config["teleop"]["rotationMaxVelocityDegS"] == 30.0
    assert config["teleop"]["continuousIncrementMode"] is True
    assert config["teleop"]["translationInputEpsilon"] == 0.000005
    assert config["teleop"]["rotationInputEpsilon"] == 0.08
    assert config["teleop"]["translationMinActivePulse"] == 3
    assert config["teleop"]["rotationMinActivePulse"] == 3
    assert config["teleop"]["continuousMicroConfirmTicks"] == 2
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
    assert config["teleop"]["rightEnabledAxes"] == [True, True, True, True, True, False]
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
    assert config["motion"]["leftSoftLimits"]["roll"] == pytest.approx(
        {"min": 24899.8, "max": 124899.8}
    )
    assert config["motion"]["leftSoftLimits"]["pitch"] == pytest.approx(
        {"min": -63935.6, "max": -3935.6}
    )
    assert config["motion"]["leftSoftLimits"]["yaw"] == pytest.approx(
        {"min": -121330.611, "max": -107330.611}
    )
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
    assert config["teleop"]["gripperTeleop"]["leftSourceHand"] == "PhysicalRight"
    assert config["teleop"]["gripperTeleop"]["rightSourceHand"] == "PhysicalLeft"
    assert config["teleop"]["gripperTeleop"]["rightGapInvert"] is False
    assert config["teleop"]["gripperTeleop"]["autoGapCalibration"] is True


def test_settings_migration_updates_current_strategy_to_safe_rotation_gate(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["translationStartVelocityUmS"] = 600.0
    config["teleop"]["translationMaxVelocityUmS"] = 8000.0
    config["teleop"]["rotationStartVelocityDegS"] = 1.0
    config["teleop"]["rotationMaxVelocityDegS"] = 12.0
    config["teleop"]["motionProfileAccSec"] = 0.05
    config["teleop"]["motionProfileDecSec"] = 0.05
    config["teleop"]["translationInputEpsilon"] = 0.00002
    config["teleop"]["rotationInputEpsilon"] = 0.12
    config["teleop"]["continuousMicroConfirmTicks"] = 0
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["teleop"]["translationStartVelocityUmS"] == 1500.0
    assert migrated["teleop"]["translationMaxVelocityUmS"] == 20000.0
    assert migrated["teleop"]["rotationStartVelocityDegS"] == 2.5
    assert migrated["teleop"]["rotationMaxVelocityDegS"] == 30.0
    assert migrated["teleop"]["motionProfileAccSec"] == 0.03
    assert migrated["teleop"]["motionProfileDecSec"] == 0.03
    assert migrated["teleop"]["translationInputEpsilon"] == 0.000005
    assert migrated["teleop"]["rotationInputEpsilon"] == 0.08
    assert migrated["teleop"]["continuousMicroConfirmTicks"] == 2


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
    config["teleop"]["translationInputEpsilon"] = 0.00001
    config["teleop"]["rotationInputEpsilon"] = 0.10
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["teleop"]["translationStartVelocityUmS"] == 750.0
    assert migrated["teleop"]["translationMaxVelocityUmS"] == 9000.0
    assert migrated["teleop"]["rotationStartVelocityDegS"] == 1.25
    assert migrated["teleop"]["rotationMaxVelocityDegS"] == 15.0
    assert migrated["teleop"]["motionProfileAccSec"] == 0.06
    assert migrated["teleop"]["motionProfileDecSec"] == 0.07
    assert migrated["teleop"]["translationInputEpsilon"] == 0.00001
    assert migrated["teleop"]["rotationInputEpsilon"] == 0.10


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


def test_settings_migration_keeps_right_roll_window_anchored_to_home_reference(tmp_path: Any) -> None:
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
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    home_reference = side_home_reference_ui(migrated, "right")
    assert home_reference is not None
    limits = effective_limits_ui(migrated, "right")
    assert limits[3].min < home_reference[3] < limits[3].max
    assert limits[3].min == pytest.approx(home_reference[3] - 95.0, abs=1e-6)
    assert limits[3].max == pytest.approx(home_reference[3] + 5.0, abs=1e-6)


def test_settings_reanchors_current_strategy_rotation_limits_to_home_reference(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["motion"]["homeReference"]["leftPulse"][3] = -392_532.0
    config["motion"]["origin"]["leftPulse"][3] = -1_095_580.0
    config["motion"]["leftSoftLimits"]["roll"] = {
        "min": -662_348.0,
        "max": -562_348.0,
    }
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    home_reference = side_home_reference_ui(migrated, "left")
    assert home_reference is not None
    limits = effective_limits_ui(migrated, "left")
    assert limits[3].min == pytest.approx(home_reference[3] - 5.0, abs=1e-6)
    assert limits[3].max == pytest.approx(home_reference[3] + 95.0, abs=1e-6)
    assert limits[3].min < limits[3].max


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
    assert migrated["teleop"]["rightEnabledAxes"][5] is False


def test_settings_migration_adds_home_reference_model_and_reanchors_stale_left_roll(tmp_path: Any) -> None:
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
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["motion"]["homeReferenceVersion"] == ICF_HOME_REFERENCE_VERSION
    assert migrated["motion"]["homeReference"]["leftPulse"] == config["motion"]["origin"]["leftPulse"]
    assert migrated["motion"]["homeReference"]["leftValid"] is True
    assert migrated["motion"]["workOriginOffset"]["leftPulseDelta"] == [0.0] * 6
    assert migrated["motion"]["workOriginOffset"]["leftValid"] is True
    assert migrated["motion"]["relativeSoftLimits"]["left"]["roll"] == {"min": -5000.0, "max": 95000.0}
    home_reference = side_home_reference_ui(migrated, "left")
    assert home_reference is not None
    limits = effective_limits_ui(migrated, "left")
    assert limits[3].min < home_reference[3] < limits[3].max
    assert limits[3].min == pytest.approx(home_reference[3] - 5.0, abs=1e-6)
    assert limits[3].max == pytest.approx(home_reference[3] + 95.0, abs=1e-6)


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

    assert migrated["teleop"]["rightEnabledAxes"] == [True, True, True, True, True, False]
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


def test_settings_migration_updates_current_axis_output_defaults_to_slower_wrist_rates(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["strategyVersion"] = ICF_TELEOP_STRATEGY_VERSION
    config["teleop"]["leftAxisOutputScale"] = [0.40, 0.25, 0.25, 0.40, 0.20, 0.20]
    config["teleop"]["rightAxisOutputScale"] = [0.40, 0.25, 0.25, 0.40, 0.20, 0.20]
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["teleop"]["leftAxisOutputScale"] == [0.65, 0.45, 0.45, 0.60, 0.16, 0.20]
    assert migrated["teleop"]["rightAxisOutputScale"] == [0.65, 0.45, 0.45, 0.55, 0.16, 0.25]


def test_settings_migration_updates_prior_pitch_yaw_axis_output_defaults_to_current_tuning(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["strategyVersion"] = ICF_TELEOP_STRATEGY_VERSION
    config["teleop"]["leftAxisOutputScale"] = [0.40, 0.25, 0.25, 0.40, 0.10, 0.15]
    config["teleop"]["rightAxisOutputScale"] = [0.40, 0.25, 0.25, 0.40, 0.10, 0.15]
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["teleop"]["leftAxisOutputScale"] == [0.65, 0.45, 0.45, 0.60, 0.16, 0.20]
    assert migrated["teleop"]["rightAxisOutputScale"] == [0.65, 0.45, 0.45, 0.55, 0.16, 0.25]


def test_settings_reanchors_current_strategy_mechanical_soft_limits_to_home_reference(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    old_config = default_config()
    old_config["motion"]["origin"]["leftPulse"][5] = 27504.0
    old_config["motion"]["leftSoftLimits"]["yaw"] = {"min": -8000.0, "max": 8000.0}
    (runtime_dir / "config.json").write_text(json.dumps(old_config), encoding="utf-8")

    config = SettingsService(runtime_dir, LogService()).get_config()

    home_reference = side_home_reference_ui(config, "left")
    assert home_reference is not None
    assert config["motion"]["leftSoftLimits"]["yaw"] == {
        "min": pytest.approx((home_reference[5] - 7.0) * 1000.0),
        "max": pytest.approx((home_reference[5] + 7.0) * 1000.0),
    }


def test_settings_migration_updates_legacy_icf_translation_speed(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    old_config = default_config()
    old_config["teleop"]["strategyVersion"] = ICF_TELEOP_STRATEGY_VERSION
    old_config["teleop"]["translationStartVelocityUmS"] = 400.0
    old_config["teleop"]["translationMaxVelocityUmS"] = 5000.0
    (runtime_dir / "config.json").write_text(json.dumps(old_config), encoding="utf-8")

    config = SettingsService(runtime_dir, LogService()).get_config()

    assert config["teleop"]["translationStartVelocityUmS"] == 1500.0
    assert config["teleop"]["translationMaxVelocityUmS"] == 20000.0


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
    assert config["cameras"]["globalIdentity"] == "USB\\VID_0ABD&PID_8050&MI_00\\7&124CCBA8&0&0000"
    assert config["cameras"]["wristLeft"] == "IMX335 / index 2"
    assert config["cameras"]["wristLeftIdentity"] == "USB\\VID_0ABD&PID_8050&MI_00\\7&7861A93&0&0000"
    assert config["cameras"]["wristRight"] == "IMX335 / index 0"
    assert config["cameras"]["wristRightIdentity"] == "USB\\VID_0ABD&PID_8050&MI_00\\7&398F0A3&0&0000"


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
    assert config["cameras"]["wristLeft"] == "IMX335 / index 2"
    assert config["cameras"]["wristRight"] == "IMX335 / index 0"


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
    assert config["cameras"]["wristLeft"] == "IMX335 / index 2"
    assert config["cameras"]["wristRight"] == "IMX335 / index 0"
