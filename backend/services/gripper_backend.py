from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from backend.core.gripper_protection import icf_target_min_gap_mm, icf_target_protection_enabled
from backend.drivers.gripper_rs485 import GripperResult


@runtime_checkable
class GripperBackend(Protocol):
    name: str

    def is_enabled(self, config: dict[str, Any]) -> bool:
        ...

    async def status(self, config: dict[str, Any]) -> dict[str, Any]:
        ...

    async def position(self, config: dict[str, Any], side: str) -> GripperResult:
        ...

    async def diagnose(self, config: dict[str, Any], side: str) -> GripperResult:
        ...

    async def command(
        self,
        config: dict[str, Any],
        side: str,
        command: str,
        target_mm: float | None,
    ) -> GripperResult:
        ...

    async def stop(self) -> None:
        ...


def native_teleop_enabled(config: dict[str, Any]) -> bool:
    return True


def gripper_serial_ports(config: dict[str, Any]) -> list[dict[str, Any]]:
    gripper = config.get("gripper", {}) if isinstance(config.get("gripper"), dict) else {}
    baudrate = int(gripper.get("baudrate", 115200))
    return [
        {
            "side": "left",
            "port": str(gripper.get("leftPort", "COM8")),
            "slaveId": int(gripper.get("leftSlaveId", 10)),
            "baudrate": baudrate,
        },
        {
            "side": "right",
            "port": str(gripper.get("rightPort", "COM9")),
            "slaveId": int(gripper.get("rightSlaveId", 9)),
            "baudrate": baudrate,
        },
    ]


def native_gripper_payload(config: dict[str, Any], side: str, target_mm: float) -> dict[str, object]:
    gripper = config.get("gripper", {}) if isinstance(config.get("gripper"), dict) else {}
    teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
    gripper_teleop = teleop.get("gripperTeleop", {}) if isinstance(teleop.get("gripperTeleop"), dict) else {}
    return {
        "side": side,
        "targetMm": target_mm,
        "leftPort": str(gripper.get("leftPort", "COM8")),
        "rightPort": str(gripper.get("rightPort", "COM9")),
        "leftSlaveId": int(gripper.get("leftSlaveId", 10)),
        "rightSlaveId": int(gripper.get("rightSlaveId", 9)),
        "baudrate": int(gripper.get("baudrate", 115200)),
        "strokeMm": float(gripper.get("strokeMm", 26)),
        "jodellDllPath": str(gripper.get("jodellDllPath", "")),
        "gripSpeed": int(gripper_teleop.get("gripSpeed", 255)),
        "gripTorque": int(gripper_teleop.get("gripTorque", 1)),
        "icfTargetProtectionEnabled": icf_target_protection_enabled(config),
        "icfTargetMinGapMm": icf_target_min_gap_mm(config),
    }


def hal_response_message(result: dict[str, object]) -> str:
    response = result.get("response")
    if isinstance(response, dict):
        message = response.get("message")
        if message is not None:
            return str(message)
    message = result.get("message")
    return str(message) if message is not None else ""


def native_status_to_gripper_status(config: dict[str, Any], mapper_status: dict[str, Any]) -> dict[str, Any]:
    targets = config.get("gripper", {}) if isinstance(config.get("gripper"), dict) else {}
    native_status = mapper_status.get("nativeStatus", {})
    sources = mapper_status.get("sources", [])
    requested_running = bool(
        isinstance(sources, list)
        and any(source in {"teleop-connect", "manual-gripper", "recording"} for source in sources)
    )
    raw_targets = native_status.get("gripperTargets") if isinstance(native_status, dict) else None
    raw_grippers = native_status.get("grippers") if isinstance(native_status, dict) else None
    left_mm = targets.get("targetLeftMm", 0.0)
    right_mm = targets.get("targetRightMm", 0.0)
    if isinstance(raw_targets, list) and len(raw_targets) >= 2:
        left_mm, right_mm = raw_targets[0], raw_targets[1]
    positions = {"left": left_mm, "right": right_mm}
    sides: dict[str, dict[str, Any]] = {}
    overall_ok = True
    messages: list[str] = []
    ports = gripper_serial_ports(config)
    for side in ("left", "right"):
        detail = raw_grippers.get(side, {}) if isinstance(raw_grippers, dict) else {}
        if not isinstance(detail, dict):
            detail = {}
        port_detail = next((item for item in ports if item.get("side") == side), {})
        command_ts = int(float(detail.get("lastCommandTs", 0) or 0))
        message = str(detail.get("message", "") or "")
        commanded = command_ts > 0 or bool(message)
        side_ok = bool(detail.get("ok")) if commanded else None
        if side_ok is False:
            overall_ok = False
            if message:
                messages.append(f"{side}: {message}")
        position_mm = positions[side]
        raw_position = detail.get("positionMm")
        if raw_position is not None:
            try:
                position_mm = float(raw_position)
            except (TypeError, ValueError):
                position_mm = positions[side]
        positions[side] = position_mm
        sides[side] = {
            "ok": side_ok,
            "message": message,
            "serial": port_detail if isinstance(port_detail, dict) else {},
            "positionMm": position_mm,
            "targetMm": detail.get("targetMm", positions[side]),
            "lastCommandTs": command_ts,
        }
    return {
        "ok": overall_ok,
        "message": "; ".join(messages) if messages else "managed by HAL-native teleop",
        "nativeManaged": True,
        "running": (
            requested_running and bool(native_status.get("running", False))
            if isinstance(native_status, dict)
            else False
        ),
        "requestedRunning": requested_running,
        "positionMm": positions,
        "sides": sides,
        "ports": ports,
        "nativeStatus": native_status if isinstance(native_status, dict) else {},
    }


class NativeGripperAdapter:
    name = "hal_native"

    def __init__(self, hal: Any, teleop_mapper: Any) -> None:
        self._hal = hal
        self._teleop_mapper = teleop_mapper

    def is_enabled(self, config: dict[str, Any]) -> bool:
        return native_teleop_enabled(config)

    async def status(self, config: dict[str, Any]) -> dict[str, Any]:
        mapper_status = await asyncio.to_thread(self._teleop_mapper.status, config)
        return native_status_to_gripper_status(config, mapper_status)

    async def position(self, config: dict[str, Any], side: str) -> GripperResult:
        status = await self.status(config)
        positions = status.get("positionMm", {})
        position_mm = positions.get(side, 0.0) if isinstance(positions, dict) else 0.0
        return GripperResult(
            True,
            str(status.get("message", "managed by HAL-native teleop")),
            float(position_mm),
            dict(status),
        )

    async def diagnose(self, config: dict[str, Any], side: str) -> GripperResult:
        return await self.position(config, side)

    async def command(
        self,
        config: dict[str, Any],
        side: str,
        command: str,
        target_mm: float | None,
    ) -> GripperResult:
        if target_mm is None:
            return GripperResult(
                True,
                "HAL-native gripper state updated; no position command required",
                details={"nativeManaged": True},
            )
        hal_result = await self._hal.command(
            "teleop.native.gripper_command",
            native_gripper_payload(config, side, target_mm),
        )
        message = hal_response_message(hal_result) or "HAL-native gripper command accepted"
        return GripperResult(
            True,
            message,
            target_mm,
            {"nativeManaged": True, "hal": hal_result},
        )

    async def stop(self) -> None:
        return None
