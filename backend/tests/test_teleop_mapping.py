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
                    "updateReturn": [0.0, 0.0, 0.0, 21.0, 0.0, 0.0],
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
            "rightConnected": True,
            "leftTranslationScale": 0.30,
            "rightTranslationScale": 0.30,
            "leftRotationScale": 0.10,
            "rightRotationScale": 0.10,
            "leftAxisOutputScale": [0.20, 0.20, 0.20, 0.25, 0.25, 1.50],
            "rightAxisOutputScale": [0.20, 0.20, 0.20, 0.25, 0.25, 1.50],
            "leftImpulseCoeff": [-5000000, 10000000, -10000000, 1667, -2500, -333.3333],
            "rightImpulseCoeff": [-5000000, -10000000, -10000000, 1667, 2500, 3333.333],
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
        assert command_names[:5] == [
            "motion.enable_side",
            "motion.enable_side",
            "motion.home_all",
            "teleop.native.configure",
            "teleop.native.start",
        ]
        assert "motion.teleop_target_update" not in command_names
        start_payload = hal.commands[4][1]
        assert start_payload["leftConnected"] is True
        assert start_payload["rightConnected"] is False
        assert start_payload["controlMode"] == "incremental_position"
        assert start_payload["mappingMode"] == "direct"
        assert start_payload["nativeLoopHz"] == 100
        assert start_payload["leftTranslationScale"] == 1.0
        assert start_payload["rightTranslationScale"] == 1.0
        assert start_payload["leftRotationScale"] == 1.0
        assert start_payload["rightRotationScale"] == 1.0
        assert start_payload["leftAxisOutputScale"] == [0.40, 0.25, 0.25, 0.40, 0.20, 0.20]
        assert start_payload["rightAxisOutputScale"] == [0.40, 0.25, 0.25, 0.40, 0.20, 0.20]
        assert start_payload["leftImpulseCoeff"] == [-5000000, 10000000, -10000000, 1667, -2500, -333.3333]
        assert start_payload["rightImpulseCoeff"] == [-5000000, -10000000, -10000000, 1667, 2500, 3333.333]
        assert start_payload["gripperTeleopEnabled"] is True
        assert start_payload["leftWorkOriginValid"] is True
        assert start_payload["rightWorkOriginValid"] is True
        assert start_payload["leftWorkOriginPulse"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        assert start_payload["rightWorkOriginPulse"] == [7.0, 8.0, 9.0, 10.0, 11.0, 12.0]
        assert start_payload["continuousIncrementMode"] is True
        assert start_payload["translationInputEpsilon"] == pytest.approx(0.00002)
        assert start_payload["rotationInputEpsilon"] == pytest.approx(0.03)
        assert start_payload["translationMinActivePulse"] == pytest.approx(3.0)
        assert start_payload["rotationMinActivePulse"] == pytest.approx(3.0)
        assert start_payload["continuousMicroConfirmTicks"] == 0
        assert hal.commands[-1][0] == "teleop.native.stop"

    asyncio.run(run())


def test_native_teleop_connect_enables_gripper_follow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        config = start_config(engine="hal_native")
        config["teleop"]["leftConnected"] = True
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=LogService())

        await mapper.start("teleop-connect", pre_home=False)

        configure_payloads = [payload for name, payload in hal.commands if name == "teleop.native.configure"]
        assert configure_payloads[-1]["gripperTeleopEnabled"] is True

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
                "movingBefore": [False, False, False, False, False, False],
                "moveStarted": [False, False, False, False, False, True],
                "clipped": [False, False, False, False, False, True],
                "deltaVector": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5],
            },
            "actionHistory": [],
            "blockers": {"left": {"state": "active"}},
        }
        mapper = TeleopMappingService(settings=FakeSettings(config), hal=hal, logs=logs)

        await mapper._refresh_native_status()
        await mapper._refresh_native_status()

        messages = [entry.msg for entry in logs.list_entries()]
        diag_messages = [message for message in messages if message.startswith("teleop diag left->right")]
        assert len(diag_messages) == 1
        event_messages = [
            message for message in messages if "event=teleop_status" in message and "sideMap=left->right" in message
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


def test_teleop_start_returns_to_work_origin_before_real_mapper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=FakeSettings(start_config()), hal=hal, logs=LogService())

        await mapper.start("recording")
        await mapper.stop("recording")

        assert hal.commands[:3] == [
            (
                "motion.enable_side",
                {"side": "left"},
            ),
            (
                "motion.enable_side",
                {"side": "right"},
            ),
            (
                "motion.home_all",
                {
                    "leftPulse": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                    "rightPulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
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

        assert hal.commands[:2] == [
            (
                "motion.enable_side",
                {"side": "right"},
            ),
            (
                "motion.home_origin_side",
                {
                    "side": "right",
                    "pulse": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
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
        assert payload["deltas"]["Y"] == -1800.0
        assert payload["deltas"]["Z"] == 30.0
        assert payload["deltas"]["Roll"] == pytest.approx(0.0498)
        assert payload["deltas"]["Pitch"] == -0.05
        assert payload["deltas"]["Yaw"] == pytest.approx(0.015)
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
        assert mapper.status()["lastAction"]["deltaVector"] == pytest.approx([
            1800.0,
            -1800.0,
            30.0,
            0.0498,
            -0.05,
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
            "Y": -18000.0,
            "Z": -300.0,
            "Roll": 83.0,
            "Pitch": 125.0,
            "Yaw": -50.0,
        }
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


def test_teleop_mapper_sends_soft_limit_offsets_without_work_origin() -> None:
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
        assert payload["softLimitMin"] == [-1000.0, -2000.0, -3000.0, -40.0, -50.0, -60.0]
        assert payload["softLimitMax"] == [1000.0, 2000.0, 3000.0, 40.0, 50.0, 60.0]

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


def test_teleop_mapper_uses_source_hand_impulse_signs_when_channels_are_swapped() -> None:
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
        "translationVelocityUmS": 8000.0,
        "rotationVelocityDegS": 12.0,
        "continuousIncrementMode": True,
        "translationInputEpsilon": 2e-05,
        "rotationInputEpsilon": 0.03,
        "translationMinActivePulse": 3.0,
        "rotationMinActivePulse": 3.0,
        "continuousMicroConfirmTicks": 0,
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


def test_hal_native_payload_sends_soft_limit_offsets_without_origin_added() -> None:
    config = start_config(engine="hal_native")
    config["motion"]["origin"]["leftPulse"] = [1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0]
    config["teleop"]["leftSoftLimitMin"] = [-1.0, -2.0, -3.0, -4.0, -5.0, -6.0]
    config["teleop"]["leftSoftLimitMax"] = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    mapper = TeleopMappingService(settings=FakeSettings(config), hal=FakeHal(), logs=LogService())

    payload = mapper._native_payload(config)

    assert payload["leftWorkOriginValid"] is True
    assert payload["leftWorkOriginPulse"] == [1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0]
    assert payload["leftSoftLimitMin"] == [-1.0, -2.0, -3.0, -4.0, -5.0, -6.0]
    assert payload["leftSoftLimitMax"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]


def test_settings_migration_updates_existing_runtime_to_icf_teleop_strategy(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    old_config = default_config()
    old_config["teleop"].pop("strategyVersion", None)
    old_config["teleop"]["controlMode"] = "velocity_admittance"
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
    assert config["teleop"]["controlMode"] == "incremental_position"
    assert config["teleop"]["leftConnected"] is True
    assert config["teleop"]["leftTranslationScale"] == 1.0
    assert config["teleop"]["rightTranslationScale"] == 1.0
    assert config["teleop"]["leftRotationScale"] == 1.0
    assert config["teleop"]["rightRotationScale"] == 1.0
    assert config["teleop"]["mappingMode"] == "direct"
    assert config["teleop"]["leftAxisOutputScale"] == [0.40, 0.25, 0.25, 0.40, 0.20, 0.20]
    assert config["teleop"]["rightAxisOutputScale"] == [0.40, 0.25, 0.25, 0.40, 0.20, 0.20]
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
    assert config["teleop"]["leftImpulseCoeff"] == [-5000000, 10000000, -10000000, 1667, -2500, -333.3333]
    assert config["teleop"]["rightImpulseCoeff"] == [-5000000, -10000000, -10000000, 1667, 2500, 3333.333]
    assert config["teleop"]["leftDirectionSign"] == [1, -1, -1, 1, -1, -1]
    assert config["teleop"]["rightDirectionSign"] == [1, 1, -1, 1, 1, 1]
    assert config["teleop"]["syncImpulseCoeffFromKinematics"] is False
    assert config["motion"]["leftSoftLimits"]["yaw"] == {"min": -7000, "max": 7000}
    assert config["motion"]["kinematics"]["rightPhysicalAxis"] == [2, 0, 5, 8, 1, 7]
    assert config["motion"]["kinematics"]["rightSignedPulsePerUnit"] == [
        -5000.0,
        -10000.0,
        -10000.0,
        1666.666667,
        2500.0,
        333.3333,
    ]
    assert config["motion"]["kinematics"]["leftSignedPulsePerUnit"] == [
        -5000.0,
        10000.0,
        -10000.0,
        1666.666667,
        -2500.0,
        -3333.333,
    ]
    assert config["motion"]["workOriginStrategyVersion"] == "icf_work_origin_20260519"
    assert config["motion"]["origin"]["leftPulse"] == [258494.0, -200013.0, 274821.0, 49833.0, 84839.0, 381102.0]
    assert config["motion"]["origin"]["rightPulse"] == [99772.0, 382486.0, 881207.0, 19527.0, -175127.0, -9668.0]
    assert config["gripper"]["leftPort"] == "COM8"
    assert config["gripper"]["rightPort"] == "COM9"
    assert config["teleop"]["gripperTeleop"]["leftSourceHand"] == "PhysicalRight"
    assert config["teleop"]["gripperTeleop"]["rightSourceHand"] == "PhysicalLeft"
    assert config["teleop"]["gripperTeleop"]["rightGapInvert"] is False
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
