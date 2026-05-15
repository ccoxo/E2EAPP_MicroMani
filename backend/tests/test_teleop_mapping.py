from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from backend.core.config import SettingsService
from backend.core.defaults import ICF_TELEOP_DEFAULTS, ICF_TELEOP_STRATEGY_VERSION, default_config
from backend.core.logging import LogService
from backend.services.teleop_mapping import TeleopMappingService


class FakeHal:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, Any]]] = []

    async def command(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.commands.append((name, payload))
        return {"ok": True}

    async def health(self) -> Any:
        return type(
            "Health",
            (),
            {"connected": False, "ltdmc_ok": False, "omega7_ok": False},
        )()

    async def omega_state(self) -> dict[str, Any]:
        return {"hands": []}


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
                    "clipped": [True, False, False, True, False, False],
                },
            }
        return {"ok": True}


def base_hand(clutch: bool = False) -> dict[str, Any]:
    return {
        "connected": True,
        "lastReadOk": True,
        "clutchPressed": clutch,
        "pose": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    }


def base_config(require_clutch: bool) -> dict[str, Any]:
    return {
        "teleop": {
            "leftConnected": True,
            "leftTranslationScale": 0.30,
            "leftRotationScale": 0.10,
            "leftAxisOutputScale": [0.20, 0.20, 0.20, 0.25, 0.25, 1.50],
            "translationStepUm": 5000.0,
            "rotationStepDeg": 0.2,
            "translationStepLimitPulse": 4000,
            "rotationStepLimitPulse": 1250,
            "translationPulseDeadband": 2,
            "rotationPulseDeadband": 2,
            "translationDeadzone": 0.00002,
            "rotationDeadzone": 0.05,
            "incrementalTranslationMinEffectiveDelta": 0.00005,
            "incrementalTranslationReverseDeadzone": 0.00010,
            "translationStartVelocityUmS": 300.0,
            "translationMaxVelocityUmS": 4000.0,
            "rotationStartVelocityDegS": 0.5,
            "rotationMaxVelocityDegS": 6.0,
            "motionProfileAccSec": 0.05,
            "motionProfileDecSec": 0.05,
            "leftEnabledAxes": [True, True, True, True, True, True],
            "leftSoftLimitMin": [-1000.0, -2000.0, -3000.0, -40.0, -50.0, -60.0],
            "leftSoftLimitMax": [1000.0, 2000.0, 3000.0, 40.0, 50.0, 60.0],
            "requireClutch": require_clutch,
        },
        "motion": {
            "leftSoftLimits": {
                "x": {"min": -100.0, "max": 100.0},
                "y": {"min": -200.0, "max": 200.0},
                "z": {"min": -300.0, "max": 300.0},
                "roll": {"min": -10.0, "max": 10.0},
                "pitch": {"min": -20.0, "max": 20.0},
                "yaw": {"min": -30.0, "max": 30.0},
            },
        },
    }


def start_config(*, valid_origin: bool = True, home_before_start: bool = True) -> dict[str, Any]:
    config = default_config()
    config["hal"]["mode"] = "real"
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
    return config


def test_teleop_start_returns_to_work_origin_before_real_mapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=FakeSettings(start_config()), hal=hal, logs=LogService())

        await mapper.start("recording")
        await mapper.stop("recording")

        assert hal.commands[0] == (
            "motion.home_all",
            {
                "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
            },
        )

    asyncio.run(run())


def test_teleop_start_returns_requested_side_to_origin_before_real_mapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=FakeSettings(start_config()), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", home_side="right")
        await mapper.stop("teleop-connect")

        assert hal.commands[0] == (
            "motion.home_origin_side",
            {
                "side": "right",
                "pulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
            },
        )

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
        assert payload["deltas"] == {
            "X": 1800.0,
            "Y": -1800.0,
            "Z": 30.0,
            "Roll": 0.05,
            "Pitch": -0.05,
            "Yaw": 0.15000000000000002,
        }
        assert payload["translationStepUm"] == 5000.0
        assert payload["rotationStepDeg"] == 0.2
        assert payload["translationStepLimitPulse"] == 4000
        assert payload["rotationStepLimitPulse"] == 1250
        assert payload["translationPulseDeadband"] == 2
        assert payload["rotationPulseDeadband"] == 2
        assert payload["enabledAxes"] == [True, True, True, True, True, True]
        assert payload["syncZeroDeltaTarget"] is True
        assert payload["softLimitMin"] == [-1000.0, -2000.0, -3000.0, -40.0, -50.0, -60.0]
        assert payload["softLimitMax"] == [1000.0, 2000.0, 3000.0, 40.0, 50.0, 60.0]
        assert payload["translationVelocityUiPerSec"] == 4000.0
        assert payload["rotationVelocityUiPerSec"] == 6.0
        assert payload["translationStartVelocityUiPerSec"] == 300.0
        assert payload["rotationStartVelocityUiPerSec"] == 0.5
        assert mapper.status()["lastAction"]["deltaVector"] == [
            1800.0,
            -1800.0,
            30.0,
            0.05,
            -0.05,
            0.15000000000000002,
            0,
            0,
            0,
            0,
            0,
            0,
        ]
        assert mapper.status()["actionHistory"][-1]["deltaVector"] == mapper.status()["lastAction"]["deltaVector"]
        assert mapper.status()["actionHistory"][-1]["monotonic_s"] > 0

    asyncio.run(run())


def test_teleop_mapper_falls_back_to_icf_soft_limits_for_invalid_teleop_limits() -> None:
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
        assert payload["softLimitMin"] == ICF_TELEOP_DEFAULTS["leftSoftLimitMin"]
        assert payload["softLimitMax"] == ICF_TELEOP_DEFAULTS["leftSoftLimitMax"]

    asyncio.run(run_case([-1.0] * 5, [1.0] * 6))
    asyncio.run(run_case([10.0] * 6, [9.0] * 6))


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
        assert payload["deltas"]["Roll"] == 0.025
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
        assert payload["deltas"]["Roll"] == 0.05

        last_action = mapper.status()["lastAction"]
        assert last_action["requestedDeltas"]["X"] == 1800.0
        assert last_action["requestedDeltas"]["Roll"] == 0.05
        assert last_action["deltas"]["X"] == 800.0
        assert last_action["deltas"]["Roll"] == 0.02
        assert last_action["appliedDeltas"]["X"] == 800.0
        assert last_action["deltaVector"] == [800.0, 0.0, 0.0, 0.02, 0.0, 0.0, 0, 0, 0, 0, 0, 0]

    asyncio.run(run())


def test_teleop_mapper_accumulates_small_translation_until_effective_delta() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
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
        assert hal.commands[2][1]["deltas"]["X"] == pytest.approx(-6.3)

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

        assert hal.commands[0][1]["deltas"]["Yaw"] == pytest.approx(1.5)
        assert hal.commands[1][1]["deltas"]["Yaw"] == pytest.approx(1.5)
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
        "translationVelocityUmS": 4000.0,
        "rotationVelocityDegS": 6.0,
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


def test_settings_migration_updates_existing_runtime_to_icf_teleop_strategy(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    old_config = default_config()
    old_config["teleop"].pop("strategyVersion", None)
    old_config["teleop"]["leftConnected"] = True
    old_config["teleop"]["leftTranslationScale"] = 0.24
    old_config["teleop"]["leftRotationScale"] = 0.18
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
    assert config["teleop"]["leftConnected"] is True
    assert config["teleop"]["leftTranslationScale"] == 0.30
    assert config["teleop"]["rightTranslationScale"] == 0.30
    assert config["teleop"]["leftRotationScale"] == 0.10
    assert config["teleop"]["rightRotationScale"] == 0.10
    assert config["teleop"]["leftAxisOutputScale"] == [0.40, 0.20, 0.20, 1.00, 0.20, 0.20]
    assert config["teleop"]["rightAxisOutputScale"] == [0.40, 0.20, 0.20, 1.00, 0.20, 0.20]
    assert config["teleop"]["translationStepLimitPulse"] == 4000
    assert config["teleop"]["rotationStepLimitPulse"] == 1250
    assert config["teleop"]["translationPulseDeadband"] == 2
    assert config["teleop"]["rotationPulseDeadband"] == 2
    assert config["teleop"]["translationStartVelocityUmS"] == 300.0
    assert config["teleop"]["translationMaxVelocityUmS"] == 4000.0
    assert config["teleop"]["rotationStartVelocityDegS"] == 0.50
    assert config["teleop"]["rotationMaxVelocityDegS"] == 6.0
    assert config["teleop"]["swapHands"] is False
    assert config["teleop"]["swapTeleopChannels"] is True
    assert config["teleop"]["stabilityMode"] == "off"
    assert config["teleop"]["softLimitUnitSpec"] == ["um", "um", "um", "deg", "deg", "deg"]
    assert config["teleop"]["leftSoftLimitMin"] == [
        -25000.0,
        -37500.0,
        -37500.0,
        -90.0,
        -90.0,
        -7.0,
    ]
    assert config["teleop"]["leftSoftLimitMax"] == [
        25000.0,
        37500.0,
        37500.0,
        90.0,
        90.0,
        7.0,
    ]
    assert config["teleop"]["rightSoftLimitMin"] == [
        -25000.0,
        -37500.0,
        -37500.0,
        -90.0,
        -90.0,
        -7.0,
    ]
    assert config["teleop"]["rightSoftLimitMax"] == [
        25000.0,
        37500.0,
        37500.0,
        90.0,
        90.0,
        7.0,
    ]
    assert config["motion"]["leftSoftLimits"]["yaw"] == {"min": -7000, "max": 7000}
    assert config["motion"]["kinematics"]["rightPhysicalAxis"] == [2, 0, 5, 8, 1, 7]
    assert config["motion"]["kinematics"]["rightSignedPulsePerUnit"] == [
        -5000.0,
        -10000.0,
        -10000.0,
        1666.666667,
        2500.0,
        666.0,
    ]
    assert config["motion"]["workOriginStrategyVersion"] == "icf_work_origin_20260513"
    assert config["motion"]["origin"]["leftPulse"] == [258510.0, -200013.0, 274821.0, 49833.0, 84839.0, 381102.0]
    assert config["motion"]["origin"]["rightPulse"] == [99769.0, 382483.0, 881210.0, -35473.0, -215115.0, -5006.0]
    assert config["gripper"]["leftPort"] == "COM8"
    assert config["gripper"]["rightPort"] == "COM9"
    assert config["teleop"]["gripperTeleop"]["leftSourceHand"] == "PhysicalRight"
    assert config["teleop"]["gripperTeleop"]["rightSourceHand"] == "PhysicalLeft"
    assert config["teleop"]["gripperTeleop"]["autoGapCalibration"] is True


def test_settings_migration_updates_legacy_icf_translation_speed(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    old_config = default_config()
    old_config["teleop"]["strategyVersion"] = ICF_TELEOP_STRATEGY_VERSION
    old_config["teleop"]["translationStartVelocityUmS"] = 400.0
    old_config["teleop"]["translationMaxVelocityUmS"] = 5000.0
    (runtime_dir / "config.json").write_text(json.dumps(old_config), encoding="utf-8")

    config = SettingsService(runtime_dir, LogService()).get_config()

    assert config["teleop"]["translationStartVelocityUmS"] == 300.0
    assert config["teleop"]["translationMaxVelocityUmS"] == 4000.0


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

    assert config["cameras"]["global"] == "AR0234 / index 1"
    assert config["cameras"]["globalIdentity"] == "USB\\VID_1D6B&PID_0102&MI_00\\6&1E9A8698&0&0000"
    assert config["cameras"]["wristLeft"] == "IMX258 / index 2"
    assert config["cameras"]["wristLeftIdentity"] == "USB\\VID_0EDC&PID_3080&MI_00\\7&38B4EA25&0&0000"
    assert config["cameras"]["wristRight"] == "IMX258 / index 0"
    assert config["cameras"]["wristRightIdentity"] == "USB\\VID_0EDC&PID_3080&MI_00\\6&1BBFDB86&0&0000"


def test_settings_migration_updates_cyclic_camera_roles(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    old_config = default_config()
    old_config["cameras"]["global"] = "AR0234 / index 2"
    old_config["cameras"]["wristLeft"] = "IMX258 / index 0"
    old_config["cameras"]["wristRight"] = "IMX258 / index 1"
    (runtime_dir / "config.json").write_text(json.dumps(old_config), encoding="utf-8")

    config = SettingsService(runtime_dir, LogService()).get_config()

    assert config["cameras"]["global"] == "AR0234 / index 1"
    assert config["cameras"]["wristLeft"] == "IMX258 / index 2"
    assert config["cameras"]["wristRight"] == "IMX258 / index 0"
