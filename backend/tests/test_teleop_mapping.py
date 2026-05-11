from __future__ import annotations

import asyncio
from typing import Any

from backend.services.teleop_mapping import TeleopMappingService


class FakeHal:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, Any]]] = []

    async def command(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.commands.append((name, payload))
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
            "leftTranslationScale": 0.24,
            "leftRotationScale": 0.18,
            "translationDeadzone": 0.00002,
            "rotationDeadzone": 0.08,
            "requireClutch": require_clutch,
        }
    }


def test_teleop_mapper_sends_continuous_six_axis_delta() -> None:
    async def run() -> None:
        hal = FakeHal()
        mapper = TeleopMappingService(settings=None, hal=hal, logs=None)  # type: ignore[arg-type]
        config = base_config(require_clutch=False)
        hand = base_hand(clutch=False)

        await mapper._step_side("left", hand, config)
        hand["pose"] = [0.001, -0.001, 0.0005, 2.0, -2.0, 1.0]
        await mapper._step_side("left", hand, config)

        assert hal.commands
        name, payload = hal.commands[0]
        assert name == "motion.teleop_target_update"
        assert payload["side"] == "left"
        assert payload["deltas"] == {
            "X": 200.0,
            "Y": -200.0,
            "Z": 120.0,
            "Roll": 0.2,
            "Pitch": -0.2,
            "Yaw": 0.18,
        }
        assert payload["translationVelocityUiPerSec"] == 1000.0
        assert payload["rotationVelocityUiPerSec"] == 0.5
        assert mapper.status()["lastAction"]["deltaVector"] == [
            200.0,
            -200.0,
            120.0,
            0.2,
            -0.2,
            0.18,
            0,
            0,
            0,
            0,
            0,
            0,
        ]

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


def test_teleop_mapper_status_reports_updated_limits() -> None:
    mapper = TeleopMappingService(settings=None, hal=FakeHal(), logs=None)  # type: ignore[arg-type]

    assert mapper.status()["limits"] == {
        "translationStepUm": 200.0,
        "rotationStepDeg": 0.2,
        "translationVelocityUmS": 1000.0,
        "rotationVelocityDegS": 0.5,
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
