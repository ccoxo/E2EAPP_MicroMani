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


class FailingTeleopHal(FakeHal):
    async def command(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.commands.append((name, payload))
        if name == "motion.teleop_target_update":
            raise RuntimeError("dmc_pmove failed ret=22 card=1 axis=0 deltaPulse=-7036")
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
            "leftAxisOutputScale": [0.20, 0.20, 1.0, 1.0, 0.5, 0.10],
            "translationStepUm": 5000.0,
            "rotationStepDeg": 0.2,
            "translationStepLimitPulse": 4000,
            "rotationStepLimitPulse": 1250,
            "translationDeadzone": 0.00002,
            "rotationDeadzone": 0.05,
            "incrementalTranslationMinEffectiveDelta": 0.00005,
            "incrementalTranslationReverseDeadzone": 0.00010,
            "translationStartVelocityUmS": 300.0,
            "translationMaxVelocityUmS": 4000.0,
            "rotationStartVelocityDegS": 0.25,
            "rotationMaxVelocityDegS": 3.0,
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
            "Z": 150.0,
            "Roll": 0.2,
            "Pitch": -0.1,
            "Yaw": 0.010000000000000002,
        }
        assert payload["translationStepUm"] == 5000.0
        assert payload["rotationStepDeg"] == 0.2
        assert payload["translationStepLimitPulse"] == 4000
        assert payload["rotationStepLimitPulse"] == 1250
        assert payload["enabledAxes"] == [True, True, True, True, True, True]
        assert payload["syncZeroDeltaTarget"] is True
        assert payload["softLimitMin"] == [-1000.0, -2000.0, -3000.0, -40.0, -50.0, -60.0]
        assert payload["softLimitMax"] == [1000.0, 2000.0, 3000.0, 40.0, 50.0, 60.0]
        assert payload["translationVelocityUiPerSec"] == 4000.0
        assert payload["rotationVelocityUiPerSec"] == 3.0
        assert payload["translationStartVelocityUiPerSec"] == 300.0
        assert payload["rotationStartVelocityUiPerSec"] == 0.25
        assert mapper.status()["lastAction"]["deltaVector"] == [
            1800.0,
            -1800.0,
            150.0,
            0.2,
            -0.1,
            0.010000000000000002,
            0,
            0,
            0,
            0,
            0,
            0,
        ]

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
        assert payload["deltas"]["Roll"] == 0.1
        assert payload["translationStepLimitPulse"] == 100.0
        assert payload["rotationStepLimitPulse"] == 50.0

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
        "translationVelocityUmS": 4000.0,
        "rotationVelocityDegS": 3.0,
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
    assert config["teleop"]["rightRotationScale"] == 0.05
    assert config["teleop"]["leftAxisOutputScale"] == [0.20, 0.20, 1.0, 1.0, 0.5, 0.10]
    assert config["teleop"]["rightAxisOutputScale"] == [0.20, 0.20, 1.0, 1.0, 0.5, 0.10]
    assert config["teleop"]["translationStepLimitPulse"] == 4000
    assert config["teleop"]["rotationStepLimitPulse"] == 1250
    assert config["teleop"]["translationStartVelocityUmS"] == 300.0
    assert config["teleop"]["translationMaxVelocityUmS"] == 4000.0
    assert config["teleop"]["swapHands"] is True
    assert config["teleop"]["stabilityMode"] == "free"
    assert config["teleop"]["leftSoftLimitMin"] == [-200000000.0] * 6
    assert config["teleop"]["leftSoftLimitMax"] == [200000000.0] * 6
    assert config["teleop"]["rightSoftLimitMin"] == [-200000000.0] * 6
    assert config["teleop"]["rightSoftLimitMax"] == [200000000.0] * 6
    assert config["motion"]["leftSoftLimits"]["yaw"] == {"min": -60000, "max": 60000}
    assert config["motion"]["workOriginStrategyVersion"] == "icf_work_origin_20260513"
    assert config["motion"]["origin"]["leftPulse"] == [100000.0, 0.0, -35179.0, 64833.0, 64839.0, -2947.0]
    assert config["motion"]["origin"]["rightPulse"] == [-233.0, -19221.0, 593101.0, 4427.0, -81110.0, -180.0]


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
