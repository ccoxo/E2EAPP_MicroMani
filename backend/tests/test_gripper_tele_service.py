from __future__ import annotations

import asyncio
import threading

from backend.core.defaults import default_config
from backend.core.logging import LogService
from backend.services.gripper_tele_service import FollowGripper, GripperTeleService


class FakeSettings:
    def __init__(self) -> None:
        self.config = default_config()
        self.config["teleop"]["gripperTeleop"]["enabled"] = False

    def get_config(self) -> dict:
        return self.config


class FakeGripperHardware:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, str, str, float | None]] = []

    def command(self, config: dict, side: str, command: str, target_mm: float | None):
        self.calls.append((config, side, command, target_mm))
        return type("Result", (), {"ok": True, "message": "ok"})()


class FakeHardware:
    def __init__(self) -> None:
        self.gripper = FakeGripperHardware()


class BlockingLeftGripperHardware:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, str, str, float | None]] = []
        self.left_entered = threading.Event()
        self.left_release = threading.Event()

    def command(self, config: dict, side: str, command: str, target_mm: float | None):
        self.calls.append((config, side, command, target_mm))
        if side == "left" and command == "target":
            self.left_entered.set()
            self.left_release.wait(timeout=1.0)
        return type("Result", (), {"ok": True, "message": f"{side} ok"})()


class BlockingLeftHardware:
    def __init__(self) -> None:
        self.gripper = BlockingLeftGripperHardware()


class BlockingOmegaHal:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def omega_state(self) -> dict:
        self.entered.set()
        await self.release.wait()
        return {
            "hands": [
                {
                    "side": "left",
                    "connected": True,
                    "lastReadOk": True,
                    "gripperGapMm": 12.0,
                }
            ]
        }


class DualHandOmegaHal:
    async def omega_state(self) -> dict:
        return {
            "hands": [
                {
                    "side": "left",
                    "connected": True,
                    "lastReadOk": True,
                    "gripperGapMm": 0.0,
                },
                {
                    "side": "right",
                    "connected": True,
                    "lastReadOk": True,
                    "gripperGapMm": 25.0,
                },
            ]
        }


def test_gripper_teleop_maps_gap_to_continuous_target() -> None:
    cfg = default_config()["teleop"]["gripperTeleop"]
    follower = FollowGripper("left")

    assert cfg["leftGapMaxMm"] == 25.0
    assert follower.update(0.0, cfg, 26.0, now_ms=0) == (0.0, 255)
    assert follower.update(12.5, cfg, 26.0, now_ms=10) == (13.0, 128)
    assert follower.update(25.0, cfg, 26.0, now_ms=20) == (26.0, 0)


def test_gripper_teleop_respects_icf_min_gap_protection() -> None:
    cfg = {
        **default_config()["teleop"]["gripperTeleop"],
        "icfTargetProtectionEnabled": True,
        "icfTargetMinGapMm": 1.02,
    }
    follower = FollowGripper("left")

    assert follower.update(0.0, cfg, 26.0, now_ms=0) == (1.02, 245)
    assert follower.update_button(True, cfg, 26.0, now_ms=60) == (1.02, 245)


def test_gripper_teleop_can_invert_right_gap_direction() -> None:
    cfg = {
        **default_config()["teleop"]["gripperTeleop"],
        "rightGapInvert": True,
    }
    follower = FollowGripper("right")

    assert follower.update(0.0, cfg, 26.0, now_ms=0) == (26.0, 0)
    assert follower.update(25.0, cfg, 26.0, now_ms=60) == (0.0, 255)


def test_gripper_teleop_respects_raw_position_deadband() -> None:
    cfg = {
        **default_config()["teleop"]["gripperTeleop"],
        "rightGapInvert": False,
        "positionDeadbandCounts": 2,
        "minCommandIntervalMs": 50,
    }
    follower = FollowGripper("right")

    assert follower.update(12.5, cfg, 26.0, now_ms=0) == (13.0, 128)
    assert follower.update(12.55, cfg, 26.0, now_ms=10) is None
    assert follower.update(12.55, cfg, 26.0, now_ms=60) == (13.052, 127)


def test_gripper_teleop_button_fallback_is_edge_triggered() -> None:
    cfg = default_config()["teleop"]["gripperTeleop"]
    follower = FollowGripper("right")

    assert follower.update_button(True, cfg, 26.0, now_ms=0) == (0.0, 255)
    assert follower.update_button(True, cfg, 26.0, now_ms=10) is None
    assert follower.update_button(False, cfg, 26.0, now_ms=20) == (26.0, 0)
    assert follower.update_button(False, cfg, 26.0, now_ms=30) is None


def test_gripper_teleop_auto_calibrates_offset_gap_range() -> None:
    cfg = {
        **default_config()["teleop"]["gripperTeleop"],
        "leftGapMinMm": 0.0,
        "leftGapMaxMm": 25.0,
        "autoGapCalibration": True,
        "autoGapMinSpanMm": 2.0,
        "autoGapMarginMm": 1.0,
    }
    follower = FollowGripper("left")

    assert follower.update(-20.0, cfg, 26.0, now_ms=0) == (0.0, 255)
    assert follower.update(-10.0, cfg, 26.0, now_ms=60) == (26.0, 0)
    assert follower.update(-15.0, cfg, 26.0, now_ms=120) == (13.0, 128)


def test_gripper_teleop_selects_configured_source_hand() -> None:
    hands = {
        "left": {"side": "left", "gripperGapMm": 4.0},
        "right": {"side": "right", "gripperGapMm": 20.0},
    }
    cfg = {"leftSourceHand": "PhysicalRight"}

    assert GripperTeleService._select_source_hand(hands, cfg, "left") is hands["right"]


def test_gripper_teleop_keeps_running_until_all_sources_stop() -> None:
    async def run() -> None:
        service = GripperTeleService(FakeSettings(), None, None, LogService())  # type: ignore[arg-type]

        service.start("teleop-connect")
        service.start("recording")
        assert service.get_status()["sources"] == ["recording", "teleop-connect"]

        service.stop("teleop-connect")
        assert service.get_status()["sources"] == ["recording"]
        assert service.is_running() is True

        service.stop("recording")
        await asyncio.sleep(0.02)
        assert service.get_status()["sources"] == []

    asyncio.run(run())


def test_gripper_teleop_restarts_when_started_while_previous_loop_is_stopping() -> None:
    async def run() -> None:
        service = GripperTeleService(FakeSettings(), None, None, LogService())  # type: ignore[arg-type]

        service.start("manual")
        service.stop("manual")
        service.start("recording")
        await asyncio.sleep(0.02)

        assert service.get_status()["sources"] == ["recording"]
        assert service.is_running() is True

        service.stop("recording")

    asyncio.run(run())


def test_gripper_teleop_stop_drops_in_flight_omega_sample_before_command() -> None:
    async def run() -> None:
        settings = FakeSettings()
        settings.config["teleop"]["gripperTeleop"]["enabled"] = True
        hal = BlockingOmegaHal()
        hardware = FakeHardware()
        service = GripperTeleService(settings, hal, hardware, LogService())  # type: ignore[arg-type]

        service.start("manual")
        await asyncio.wait_for(hal.entered.wait(), timeout=1.0)
        service.stop("manual")
        hal.release.set()
        await asyncio.sleep(0.02)

        assert hardware.gripper.calls == []

    asyncio.run(run())


def test_gripper_teleop_stop_cancels_blocked_omega_read() -> None:
    async def run() -> None:
        settings = FakeSettings()
        settings.config["teleop"]["gripperTeleop"]["enabled"] = True
        hal = BlockingOmegaHal()
        service = GripperTeleService(settings, hal, FakeHardware(), LogService())  # type: ignore[arg-type]

        service.start("manual")
        await asyncio.wait_for(hal.entered.wait(), timeout=1.0)
        task = service._task
        assert task is not None

        try:
            service.stop("manual")
            await asyncio.sleep(0)

            assert task.done() is True
            assert service.is_running() is False
        finally:
            hal.release.set()
            if not task.done():
                await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(run())


def test_gripper_teleop_left_command_does_not_block_right_follow() -> None:
    async def run() -> None:
        settings = FakeSettings()
        settings.config["teleop"]["gripperTeleop"]["enabled"] = True
        settings.config["teleop"]["gripperTeleop"]["loopHz"] = 1000
        hardware = BlockingLeftHardware()
        service = GripperTeleService(settings, DualHandOmegaHal(), hardware, LogService())  # type: ignore[arg-type]

        service.start("manual")
        try:
            await asyncio.wait_for(asyncio.to_thread(hardware.gripper.left_entered.wait), timeout=1.0)
            await asyncio.sleep(0.05)

            assert any(side == "right" and command == "target" for _cfg, side, command, _target in hardware.gripper.calls)
        finally:
            hardware.gripper.left_release.set()
            service.stop("manual")
            await asyncio.sleep(0.05)

    asyncio.run(run())


def test_gripper_teleop_target_bypasses_manual_enable_flag() -> None:
    settings = FakeSettings()
    settings.config["gripper"]["leftEnabled"] = False
    hardware = FakeHardware()
    service = GripperTeleService(settings, None, hardware, LogService())  # type: ignore[arg-type]

    result = service._issue_command(settings.config, "left", "target", 128, 192, 12.0)

    assert result.ok is True
    sent_config, side, command, target = hardware.gripper.calls[-1]
    assert side == "left"
    assert command == "target"
    assert target == 12.0
    assert sent_config["gripper"]["leftEnabled"] is True
