from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import Body, FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from starlette.websockets import WebSocketState

from backend.core.config import SettingsService
from backend.core.logging import LOG_SCHEMA_VERSION, LogService, default_session_id, now_ms, stable_config_hash
from backend.core.schemas import (
    ApiEnvelope,
    AppConfig,
    GripperCommandRequest,
    ManualAxisMoveRequest,
    SettingsCommandRequest,
    SnapshotCreateRequest,
    SnapshotScope,
)
from backend.core.units import motion_pulse_per_unit, pulses_to_ui_state
from backend.hal_client.client import HalClient, RealHalClient, TestHalClient
from backend.services.command_service import (
    CommandService,
    MotionOriginDriftConfirmationRequired,
    normalize_motion_axis_enabled,
)
from backend.services.dataset_recorder import DatasetRecorderService, DatasetSaveError
from backend.services.gripper_tele_service import GripperTeleService
from backend.services.gripper_worker_service import GripperWorkerService
from backend.services.hardware_service import HardwareService
from backend.services.policy_bridge import build_policy_action_plan, lerobot_state_from_ui
from backend.services.policy_service import PolicyService
from backend.services.stability_monitor import StabilityMonitorService
from backend.services.telemetry_hub import TelemetryHub
from backend.services.teleop_mapping import SideName, TeleopMappingService


def envelope(data: dict[str, Any] | None = None) -> ApiEnvelope:
    # 统一 API 响应外壳，便于前端按 ok/data/ts 的固定结构处理结果。
    return ApiEnvelope(ok=True, data=data or {}, ts=now_ms())


AXIS_NAMES = ("X", "Y", "Z", "Roll", "Pitch", "Yaw")


def emit_session_start_log(logs: LogService, settings: SettingsService, config: dict[str, Any]) -> None:
    hal_config = config.get("hal", {}) if isinstance(config.get("hal"), dict) else {}
    logs.event(
        "[BACKEND]",
        "INFO",
        "session_start",
        component="BACKEND",
        exe="python",
        cwd=str(Path.cwd()),
        appDir=str(Path(__file__).resolve().parents[1]),
        configPath=str(settings.config_path),
        configHash=stable_config_hash(config),
        logSchemaVersion=LOG_SCHEMA_VERSION,
        opencvVersion="python-package",
        e2eBackendDetected=bool(hal_config.get("baseUrl")),
        backendUrl=str(hal_config.get("baseUrl", "")),
    )


def emit_axis_config_snapshot_logs(logs: LogService, settings: SettingsService, config: dict[str, Any]) -> None:
    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
    kinematics = motion.get("kinematics", {}) if isinstance(motion.get("kinematics"), dict) else {}
    origin = motion.get("origin", {}) if isinstance(motion.get("origin"), dict) else {}
    pulses = motion_pulse_per_unit(config)
    config_hash = stable_config_hash(config)
    swap_channels = bool(teleop.get("swapTeleopChannels", False))
    for side, offset in (("left", 0), ("right", 6)):
        source_side = ("right" if side == "left" else "left") if swap_channels else side
        profile = motion.get(f"{side}Profile", {})
        limits = motion.get(f"{side}SoftLimits", {})
        card = motion.get(f"{side}CardNo", 1 if side == "left" else 0)
        axis_map = kinematics.get(f"{side}PhysicalAxis", kinematics.get(f"{side}AxisMap", list(range(6))))
        source_impulse = teleop.get(f"{source_side}ImpulseCoeff", [0] * 6)
        target_impulse = teleop.get(f"{side}ImpulseCoeff", [0] * 6)
        direction = teleop.get(f"{side}DirectionSign", [1] * 6)
        origin_pulse = origin.get(f"{side}Pulse", [0] * 6)
        for index, axis_name in enumerate(AXIS_NAMES):
            group = "translation" if index < 3 else "rotation"
            group_profile = profile.get(group, {}) if isinstance(profile, dict) else {}
            axis_limits = limits.get(axis_name.lower(), {}) if isinstance(limits, dict) else {}
            logs.event(
                "[HAL]",
                "INFO",
                "axis_config_snapshot",
                component="MOTION",
                side=side,
                axisName=axis_name,
                logicalAxis=offset + index,
                card=card,
                physicalAxis=axis_map[index] if isinstance(axis_map, list) and len(axis_map) > index else index,
                pulsePerUnit=pulses[offset + index],
                sourceSide=source_side,
                impulseCoeff=(
                    source_impulse[index] if isinstance(source_impulse, list) and len(source_impulse) > index else 0
                ),
                targetImpulseCoeff=(
                    target_impulse[index] if isinstance(target_impulse, list) and len(target_impulse) > index else 0
                ),
                directionSign=direction[index] if isinstance(direction, list) and len(direction) > index else 1,
                softLimitMin=axis_limits.get("min", ""),
                softLimitMax=axis_limits.get("max", ""),
                originPulse=origin_pulse[index] if isinstance(origin_pulse, list) and len(origin_pulse) > index else 0,
                enabled=True,
                profileStartSpeed=group_profile.get("startSpeed", ""),
                profileMaxSpeed=group_profile.get("maxSpeed", ""),
                acc=group_profile.get("accTimeSec", ""),
                dec=group_profile.get("decTimeSec", ""),
                configPath=str(settings.config_path),
                configHash=config_hash,
            )


def emit_omega_device_logs(logs: LogService, config: dict[str, Any], hands: list[dict[str, Any]]) -> None:
    teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
    for hand in hands:
        side = str(hand.get("side", ""))
        if side not in {"left", "right"}:
            continue
        logs.event(
            "[HAL]",
            "INFO",
            "omega_device",
            component="TELEOP",
            rate_key=f"omega_device:{side}",
            rate_ms=1000,
            side=side,
            requestedId=teleop.get(f"{side}OpenId", ""),
            openRet=bool(hand.get("connected", False)),
            deviceId=hand.get("deviceId", ""),
            serial=hand.get("serial", ""),
            physicalConnected=bool(hand.get("connected", False)),
            logicalConnected=bool(teleop.get(f"{side}Connected", False)),
            leftHanded=hand.get("leftHanded"),
            gravityRet="unknown",
            expertModeRet="unknown",
            disconnectReason="" if bool(hand.get("connected", False)) else str(hand.get("message", "")),
        )


def mask_omega_state_for_logical_connection(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    # 物理设备可能仍然在线；这里按前端的逻辑连接开关隐藏未启用的手柄状态。
    masked = dict(state)
    hands = state.get("hands")
    if not isinstance(hands, list):
        return masked

    teleop_config = config.get("teleop", {})
    next_hands: list[dict[str, Any]] = []
    for item in hands:
        if not isinstance(item, dict):
            continue
        hand = dict(item)
        side = hand.get("side")
        logical_connected = bool(teleop_config.get(f"{side}Connected", False)) if side in {"left", "right"} else True
        if not logical_connected:
            hand.update(
                {
                    "connected": False,
                    "calibrated": False,
                    "pose": [0.0] * 6,
                    "clutchPressed": False,
                    "gripperPressed": False,
                    "gripperGapMm": None,
                    "lastReadOk": False,
                    "message": "logical teleop hand disconnected",
                }
            )
        next_hands.append(hand)
    masked["hands"] = next_hands
    return masked


def origin_side_pulses(origin: dict[str, Any], side: str) -> list[float] | None:
    key = "leftPulse" if side == "left" else "rightPulse"
    raw = origin.get(key)
    if not isinstance(raw, list) or len(raw) < 6:
        return None
    try:
        return [float(value) for value in raw[:6]]
    except (TypeError, ValueError):
        return None


def relative_motion_positions(
    config: dict[str, Any],
    positions: list[float],
    pulses: list[float] | None,
) -> list[float]:
    origin = config.get("motion", {}).get("origin", {})
    if not isinstance(origin, dict) or pulses is None or len(pulses) != 12:
        return positions
    next_positions = (list(positions) + [0.0] * 12)[:12]
    relative_pulses = list(pulses)
    left_origin = origin_side_pulses(origin, "left")
    right_origin = origin_side_pulses(origin, "right")
    left_valid = bool(origin.get("leftValid", origin.get("valid", False)))
    right_valid = bool(origin.get("rightValid", origin.get("valid", False)))
    if left_valid and left_origin is not None:
        relative_pulses[:6] = [float(pulses[index]) - left_origin[index] for index in range(6)]
    if right_valid and right_origin is not None:
        relative_pulses[6:12] = [float(pulses[index + 6]) - right_origin[index] for index in range(6)]
    if not left_valid and not right_valid:
        return next_positions
    relative_positions = pulses_to_ui_state(relative_pulses, config)
    if left_valid and left_origin is not None:
        next_positions[:6] = relative_positions[:6]
    if right_valid and right_origin is not None:
        next_positions[6:12] = relative_positions[6:12]
    return next_positions


def runtime_dir_from_env() -> Path:
    # 测试和本地运行可以通过环境变量隔离 runtime 数据目录。
    raw = os.environ.get("APPSTATION_RUNTIME_DIR")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent / "runtime"


def create_app(runtime_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="AppStation Backend", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_origin_regex=r"http://(127\.0\.0\.1|localhost):\d+",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 服务实例集中挂到 app.state，FastAPI 路由和 WebSocket 循环共享同一套运行时状态。
    active_runtime_dir = runtime_dir or runtime_dir_from_env()
    session_id = default_session_id()
    logs = LogService(
        session_id=session_id,
        log_file_path=active_runtime_dir / "logs" / f"appstation-m0-{session_id}.log",
    )
    settings = SettingsService(active_runtime_dir, logs)
    startup_config = settings.get_config()
    emit_session_start_log(logs, settings, startup_config)
    emit_axis_config_snapshot_logs(logs, settings, startup_config)
    hardware = HardwareService(settings, logs)
    gripper_workers = GripperWorkerService(settings, logs)
    telemetry = TelemetryHub(settings, hardware, gripper_workers)
    hal = make_hal_client(startup_config, logs)
    teleop_mapper = TeleopMappingService(settings, hal, logs)
    commands = CommandService(settings, telemetry, hal, logs, hardware, gripper_workers, teleop=teleop_mapper)
    gripper_tele = GripperTeleService(settings, hal, hardware, logs, gripper_workers)
    recorder = DatasetRecorderService(settings, hardware, hal, telemetry, logs, teleop_mapper)
    commands.set_origin_mutation_lock_checker(recorder.origin_mutation_locked)
    stability = StabilityMonitorService(settings, hardware, hal, logs)
    policy = PolicyService(settings, hal, logs)

    app.state.logs = logs
    app.state.settings = settings
    app.state.telemetry = telemetry
    app.state.commands = commands
    app.state.hal = hal
    app.state.hardware = hardware
    app.state.gripper_workers = gripper_workers
    app.state.teleop_mapper = teleop_mapper
    app.state.gripper_tele = gripper_tele
    app.state.recorder = recorder
    app.state.stability = stability
    app.state.policy = policy
    app.state.ws_clients = set()
    app.state.shutdown_task = None
    app.state.teleop_background_tasks = set()
    stability.set_ws_client_count_provider(lambda: len(app.state.ws_clients))

    def set_teleop_logical_connection(side: str, connected: bool) -> dict[str, Any]:
        # 逻辑连接状态写入配置，让页面刷新后仍能保留操作员显式选择。
        config = settings.get_config()
        config["teleop"][f"{side}Connected"] = connected
        return settings.save_config(config, emit_log=False)

    def native_teleop_config(config: dict[str, Any]) -> bool:
        teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
        return str(teleop.get("engine", "")).lower() == "hal_native"

    def _gripper_teleop_enabled(config: dict[str, Any]) -> bool:
        teleop = config.get("teleop", {})
        gripper_teleop = teleop.get("gripperTeleop", {}) if isinstance(teleop, dict) else {}
        return isinstance(gripper_teleop, dict) and bool(gripper_teleop.get("enabled", False))

    async def get_config_async() -> dict[str, Any]:
        return await asyncio.to_thread(settings.get_config)

    async def gripper_teleop_enabled_async() -> bool:
        return _gripper_teleop_enabled(await get_config_async())

    async def native_teleop_enabled_async() -> bool:
        return native_teleop_config(await get_config_async())

    def teleop_target_side_for_source(side: SideName, config: dict[str, Any]) -> SideName:
        teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
        if bool(teleop.get("swapTeleopChannels", False)):
            return "right" if side == "left" else "left"
        return side

    def schedule_teleop_background(coro: Any, label: str) -> None:
        task = asyncio.create_task(coro, name=label)
        app.state.teleop_background_tasks.add(task)

        def _cleanup(done: asyncio.Task[Any]) -> None:
            app.state.teleop_background_tasks.discard(done)
            if done.cancelled():
                return
            try:
                done.result()
            except Exception as exc:  # noqa: BLE001
                logs.error("[HAL]", f"{label} failed: {exc}")

        task.add_done_callback(_cleanup)

    async def sync_teleop_logical_connection(
        side: SideName,
        connected: bool,
        config: dict[str, Any] | None = None,
    ) -> None:
        config = config if config is not None else await get_config_async()
        teleop_config = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
        mapped_side = teleop_target_side_for_source(side, config)
        if connected:
            if isinstance(teleop_config, dict):
                try:
                    await hal.command(
                        "omega7.gravity_compensation",
                        {
                            "leftEnabled": bool(teleop_config.get("leftGravityCompensation", True)),
                            "rightEnabled": bool(teleop_config.get("rightGravityCompensation", True)),
                        },
                    )
                except RuntimeError as exc:
                    logs.warning("[HAL]", f"Omega.7 force output apply failed: {exc}")
            try:
                await commands.enable_motion_side(mapped_side)
            except RuntimeError as exc:
                logs.error("[HAL]", f"teleop connect enable mapped {mapped_side} failed: {exc}")
            await teleop_mapper.start("teleop-connect", pre_home=False, home_side=mapped_side)
            await start_gripper_teleop_source("teleop-connect", config)
            logs.info("[HAL]", f"{side} Omega.7 logical connect background sync completed")
            return

        try:
            await commands.stop_motion_side(mapped_side)
        except RuntimeError as exc:
            logs.error("[HAL]", f"teleop disconnect stop mapped {mapped_side} failed: {exc}")
        if not bool(teleop_config.get("leftConnected", False)) and not bool(teleop_config.get("rightConnected", False)):
            await teleop_mapper.stop("teleop-connect")
            gripper_tele.stop("teleop-connect")
            logs.info("[HAL]", f"{side} Omega.7 logical disconnect background stop completed")
            return
        if native_teleop_config(config):
            await teleop_mapper.start("teleop-connect", pre_home=False)
        logs.info("[HAL]", f"{side} Omega.7 logical disconnect background refresh completed")

    def native_gripper_status(
        config: dict[str, Any] | None = None,
        serial_probe: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        active = config if config is not None else settings.get_config()
        targets = active.get("gripper", {}) if isinstance(active.get("gripper"), dict) else {}
        mapper_status = teleop_mapper.status(active)
        native_status = mapper_status.get("nativeStatus", {})
        sources = mapper_status.get("sources", [])
        requested_running = bool(
            isinstance(sources, list)
            and any(source in {"teleop-connect", "manual-gripper", "recording"} for source in sources)
        )
        raw_targets = native_status.get("gripperTargets") if isinstance(native_status, dict) else None
        raw_grippers = native_status.get("grippers") if isinstance(native_status, dict) else None
        serial_probe = serial_probe if isinstance(serial_probe, dict) else None
        serial_ok = serial_probe.get("ok") if serial_probe is not None else None
        serial_message = str(serial_probe.get("message", "") or "") if serial_probe is not None else ""
        serial_ports = []
        if serial_probe is not None:
            raw_ports = serial_probe.get("ports")
            if not isinstance(raw_ports, list):
                details = serial_probe.get("details")
                raw_ports = details.get("ports") if isinstance(details, dict) else []
            serial_ports = [item for item in raw_ports if isinstance(item, dict)] if isinstance(raw_ports, list) else []
        left_mm = targets.get("targetLeftMm", 0.0)
        right_mm = targets.get("targetRightMm", 0.0)
        if isinstance(raw_targets, list) and len(raw_targets) >= 2:
            left_mm, right_mm = raw_targets[0], raw_targets[1]
        positions = {"left": left_mm, "right": right_mm}
        sides: dict[str, dict[str, Any]] = {}
        overall_ok = serial_ok is not False
        messages: list[str] = []
        if serial_ok is False and serial_message:
            messages.append(serial_message)
        for side in ("left", "right"):
            detail = raw_grippers.get(side, {}) if isinstance(raw_grippers, dict) else {}
            if not isinstance(detail, dict):
                detail = {}
            port_detail = next((item for item in serial_ports if item.get("side") == side), {})
            command_ts = int(float(detail.get("lastCommandTs", 0) or 0))
            message = str(detail.get("message", "") or "")
            commanded = command_ts > 0 or bool(message)
            side_ok = bool(detail.get("ok")) if commanded else None
            if side_ok is None and isinstance(port_detail, dict) and "ok" in port_detail:
                side_ok = bool(port_detail.get("ok"))
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
            "message": "; ".join(messages) if messages else serial_message or "managed by HAL-native teleop",
            "nativeManaged": True,
            "running": (
                requested_running and bool(native_status.get("running", False))
                if isinstance(native_status, dict)
                else False
            ),
            "requestedRunning": requested_running,
            "positionMm": positions,
            "sides": sides,
            "ports": serial_ports or gripper_serial_ports(active),
            "nativeStatus": native_status if isinstance(native_status, dict) else {},
        }

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

    def attach_gripper_serial_ports(status: dict[str, Any], config: dict[str, Any]) -> None:
        gripper_status = status.get("gripper")
        if not isinstance(gripper_status, dict):
            return
        gripper_status.setdefault("ports", gripper_serial_ports(config))

    async def omega7_serial_status(config: dict[str, Any], hal_health: Any | None = None) -> dict[str, Any]:
        health_state = hal_health or await hal.health()
        teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
        requested_ids = {
            "left": int(teleop.get("leftOpenId", 0)),
            "right": int(teleop.get("rightOpenId", 1)),
        }
        if not health_state.connected:
            return {
                "ok": False,
                "message": "HAL unavailable; Omega.7 cannot be identified",
                "hands": [],
                "requestedIds": requested_ids,
            }
        if not health_state.omega7_ok:
            return {
                "ok": False,
                "message": health_state.message or "HAL reports omega7_ok=false",
                "hands": [],
                "requestedIds": requested_ids,
            }
        try:
            omega_state = await hal.omega_state()
        except RuntimeError as exc:
            return {
                "ok": False,
                "message": f"Omega.7 state read failed: {exc}",
                "hands": [],
                "requestedIds": requested_ids,
            }
        raw_hands = omega_state.get("hands")
        hands: list[dict[str, Any]] = []
        if isinstance(raw_hands, list):
            for side in ("left", "right"):
                hand = next(
                    (
                        item
                        for item in raw_hands
                        if isinstance(item, dict) and item.get("side") == side
                    ),
                    None,
                )
                hands.append(
                    {
                        "side": side,
                        "requestedId": requested_ids[side],
                        "connected": bool(hand.get("connected", False)) if isinstance(hand, dict) else False,
                        "lastReadOk": bool(hand.get("lastReadOk", False)) if isinstance(hand, dict) else False,
                        "deviceId": hand.get("deviceId", "") if isinstance(hand, dict) else "",
                        "serial": hand.get("serial", "") if isinstance(hand, dict) else "",
                        "leftHanded": hand.get("leftHanded") if isinstance(hand, dict) else None,
                        "message": str(hand.get("message", "")) if isinstance(hand, dict) else "not reported by HAL",
                    }
                )
        ok = len(hands) == 2 and all(bool(hand["connected"]) and bool(hand["lastReadOk"]) for hand in hands)
        return {
            "ok": ok,
            "message": "Omega.7 devices recognized by HAL" if ok else "Omega.7 device identity incomplete",
            "hands": hands,
            "requestedIds": requested_ids,
        }

    async def require_hardware_recognized(
        source: str,
        *,
        require_camera: bool,
        require_gripper: bool = True,
    ) -> None:
        config = await get_config_async()
        use_gripper_workers = gripper_workers.is_enabled(config)
        native_gripper = native_teleop_config(config) and not use_gripper_workers
        hal_health = await hal.health()
        if hal_health.mode != "real":
            return
        failures: list[str] = []
        if not hal_health.connected:
            failures.append(hal_health.message or "HAL unavailable")
        elif not hal_health.ltdmc_ok:
            failures.append("HAL motion controller ltdmc_ok=false")

        omega_status = await omega7_serial_status(config, hal_health)
        if not bool(omega_status.get("ok", False)):
            failures.append(str(omega_status.get("message") or "Omega.7 devices not recognized"))

        include_gripper_probe = require_gripper and not native_gripper and not use_gripper_workers
        hardware_status = await asyncio.to_thread(hardware.status, include_gripper=include_gripper_probe)
        camera_status = hardware_status.get("camera", {})
        if require_camera and (
            not isinstance(camera_status, dict) or not bool(camera_status.get("ok", False))
        ):
            failures.append(
                str(camera_status.get("message") if isinstance(camera_status, dict) else "cameras not ready")
            )
        if require_gripper and not use_gripper_workers:
            gripper_probe = hardware_status.get("gripper", {})
            gripper_status = native_gripper_status(config) if native_gripper else gripper_probe
            if not isinstance(gripper_status, dict) or not bool(gripper_status.get("ok", False)):
                failures.append(
                    str(
                        gripper_status.get("message")
                        if isinstance(gripper_status, dict)
                        else "gripper serial not ready"
                    )
                )
        if failures:
            message = "; ".join(item for item in failures if item)
            logs.error("[BACKEND]", f"{source} precheck failed: {message}")
            raise HTTPException(
                status_code=503,
                detail={"code": "HARDWARE_PRECHECK_FAILED", "message": message},
            )

    async def start_gripper_teleop_source(source: str, config: dict[str, Any] | None = None) -> None:
        enabled = (
            _gripper_teleop_enabled(config)
            if config is not None
            else await gripper_teleop_enabled_async()
        )
        if enabled:
            gripper_tele.start(source)

    async def stop_aux_native_teleop_sources(reason: str) -> None:
        if not await native_teleop_enabled_async():
            return
        for source in ("teleop-connect", "manual-gripper"):
            try:
                await teleop_mapper.stop(source, restart_remaining=False)
            except RuntimeError as exc:
                logs.error("[HAL]", f"{reason} native teleop source release failed source={source}: {exc}")
                raise

    async def release_runtime_handles(reason: str) -> dict[str, Any]:
        gripper_tele.stop(force=True)
        released_grippers: list[str] = []
        gripper_errors: dict[str, str] = {}
        teleop_errors: dict[str, str] = {}
        for side in ("left", "right"):
            try:
                await commands.gripper_command(GripperCommandRequest(side=side, command="disable"))
                released_grippers.append(side)
            except RuntimeError as exc:
                gripper_errors[side] = str(exc)
                logs.error("[GRIPPER]", f"{side} gripper close-release failed: {exc}")
            await asyncio.to_thread(set_teleop_logical_connection, side, False)
        config = await get_config_async()
        config["gripper"]["leftEnabled"] = False
        config["gripper"]["rightEnabled"] = False
        await asyncio.to_thread(settings.save_config, config, emit_log=False)
        await asyncio.to_thread(gripper_workers.stop_all)
        for source in ("teleop-connect", "recording"):
            try:
                await teleop_mapper.stop(source)
            except RuntimeError as exc:
                teleop_errors[source] = str(exc)
                logs.error("[HAL]", f"teleop close-release failed source={source}: {exc}")
        logs.warning("[BACKEND]", f"runtime handles released: {reason}")
        return {
            "releasedGrippers": released_grippers,
            "gripperErrors": gripper_errors,
            "teleopErrors": teleop_errors,
            "teleopConnected": {"left": False, "right": False},
        }

    async def delayed_runtime_shutdown(reason: str) -> None:
        # 浏览器关闭后延迟停栈，给快速刷新或 WebSocket 重连留出缓冲时间。
        delay_sec = float(os.environ.get("APPSTATION_CLOSE_SHUTDOWN_DELAY_SEC", "5"))
        await asyncio.sleep(delay_sec)
        active_clients = len(app.state.ws_clients)
        if active_clients > 0:
            logs.info("[BACKEND]", f"runtime shutdown skipped; active WebSocket clients={active_clients}")
            return
        repo = Path(__file__).resolve().parent.parent
        stop_script = repo / "scripts" / "stop-stack.ps1"
        if not stop_script.exists():
            logs.error("[BACKEND]", f"runtime shutdown failed; missing {stop_script}")
            return
        logs.warning("[BACKEND]", f"runtime shutdown requested: {reason}")
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(stop_script),
            ],
            cwd=repo,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    @app.on_event("startup")
    async def reconcile_startup_hal_state() -> None:
        skip_startup_home = os.environ.get("APPSTATION_SKIP_STARTUP_HOME", "").strip().lower()
        config = await get_config_async()
        teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
        if (
            native_teleop_config(config)
            and not bool(teleop.get("leftConnected", False))
            and not bool(teleop.get("rightConnected", False))
        ):
            try:
                await hal.command("teleop.native.stop", {})
                await teleop_mapper.stop("teleop-connect")
                logs.info("[HAL]", "startup cleared stale HAL-native teleop controller")
            except RuntimeError as exc:
                logs.warning("[HAL]", f"startup stale HAL-native teleop cleanup skipped: {exc}")
        if skip_startup_home in {"1", "true", "yes", "on"}:
            logs.warning("[HAL]", "startup return-to-work-origin skipped by APPSTATION_SKIP_STARTUP_HOME")
            return
        startup_config = config.get("motion", {}).get("homeOnStartup", {})
        if not isinstance(startup_config, dict) or not bool(startup_config.get("enabled", False)):
            return
        mode = str(startup_config.get("mode", "work_origin"))
        if mode != "work_origin":
            logs.warning("[HAL]", f"startup motion home skipped; unsupported mode={mode}")
            return
        hal_health = await hal.health()
        if not hal_health.connected:
            logs.warning("[HAL]", "startup return-to-work-origin skipped; HAL unavailable")
            return
        try:
            await commands.home_all()
            logs.info("[HAL]", "startup return-to-work-origin completed")
        except RuntimeError as exc:
            logs.error("[HAL]", f"startup return-to-work-origin failed: {exc}")

    @app.on_event("shutdown")
    async def stop_gripper_workers() -> None:
        try:
            record_status = await asyncio.to_thread(recorder.status)
            if record_status.get("active") or record_status.get("recording"):
                await recorder.finish_session()
        except Exception as exc:  # noqa: BLE001
            logs.error("[LEROBOT]", f"shutdown record session finish failed: {exc}")

        tasks = set(app.state.teleop_background_tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        app.state.teleop_background_tasks.clear()
        gripper_tele.stop(force=True)
        await asyncio.to_thread(gripper_workers.stop_all)
        telemetry.shutdown()

    # 查询后端、HAL 与硬件健康状态。
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        hal_health = await hal.health()
        health_config = await get_config_async()
        use_gripper_workers = gripper_workers.is_enabled(health_config)
        native_gripper = native_teleop_config(health_config) and not use_gripper_workers
        hardware_status = await asyncio.to_thread(
            hardware.status,
            include_gripper=not use_gripper_workers and not native_gripper,
        )
        if use_gripper_workers:
            hardware_status["gripper"] = await asyncio.to_thread(gripper_workers.status, health_config)
        elif native_gripper:
            hardware_status["gripper"] = native_gripper_status(health_config)
        attach_gripper_serial_ports(hardware_status, health_config)
        hardware_status["omega7"] = await omega7_serial_status(health_config, hal_health)
        return {
            "ok": True,
            "backend": "running",
            "mode": hal_health.mode,
            "hal": hal_health.__dict__,
            "hardware": hardware_status,
            "ts": now_ms(),
        }

    # 查询当前硬件连接与设备状态。
    @app.get("/api/hardware/status")
    async def hardware_status() -> dict[str, Any]:
        config = await get_config_async()
        use_gripper_workers = gripper_workers.is_enabled(config)
        native_gripper = native_teleop_config(config) and not use_gripper_workers
        status = await asyncio.to_thread(
            hardware.status,
            include_gripper=not use_gripper_workers and not native_gripper,
        )
        if use_gripper_workers:
            status["gripper"] = await asyncio.to_thread(gripper_workers.status, config)
        elif native_gripper:
            status["gripper"] = native_gripper_status(config)
        attach_gripper_serial_ports(status, config)
        status["omega7"] = await omega7_serial_status(config)
        return status

    # 获取当前应用配置。
    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        return await get_config_async()

    # 保存新的应用配置。
    @app.put("/api/settings")
    async def put_settings(config: AppConfig) -> dict[str, Any]:
        saved = await asyncio.to_thread(settings.save_config, config.model_dump(mode="json"))
        await asyncio.to_thread(gripper_workers.sync_config, saved)
        return saved

    # 应用传入配置或重新应用当前配置。
    @app.post("/api/settings/apply")
    async def apply_settings(config: AppConfig | None = None) -> ApiEnvelope:
        try:
            active = await asyncio.to_thread(
                settings.apply_config,
                config.model_dump(mode="json") if config is not None else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"code": "VALIDATION_ERROR", "message": str(exc)}) from exc
        await asyncio.to_thread(gripper_workers.sync_config, active)
        return envelope({"config": active})

    # 列出配置快照。
    @app.get("/api/settings/snapshots")
    async def list_snapshots(scope: Annotated[SnapshotScope | None, Query()] = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(settings.list_snapshots, scope)

    # 创建配置快照。
    @app.post("/api/settings/snapshots")
    async def create_snapshot(request: SnapshotCreateRequest) -> ApiEnvelope:
        snapshot = await asyncio.to_thread(settings.create_snapshot, request)
        snapshots = await asyncio.to_thread(settings.list_snapshots, request.scope)
        return envelope({"snapshot": snapshot, "snapshots": snapshots})

    # 应用指定配置快照。
    @app.post("/api/settings/snapshots/{snapshot_id}/apply")
    async def apply_snapshot(snapshot_id: str) -> ApiEnvelope:
        try:
            config = await asyncio.to_thread(settings.apply_snapshot, snapshot_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "snapshot not found"}) from exc
        snapshots = await asyncio.to_thread(settings.list_snapshots)
        return envelope({"config": config, "snapshots": snapshots})

    # 删除指定配置快照。
    @app.delete("/api/settings/snapshots/{snapshot_id}")
    async def delete_snapshot(snapshot_id: str) -> ApiEnvelope:
        try:
            await asyncio.to_thread(settings.delete_snapshot, snapshot_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "snapshot not found"}) from exc
        snapshots = await asyncio.to_thread(settings.list_snapshots)
        return envelope({"snapshots": snapshots})

    # 记录设置页通用命令日志。
    @app.post("/api/settings/log_command")
    async def settings_log_command(request: SettingsCommandRequest) -> ApiEnvelope:
        data = await commands.generic_command(request)
        return envelope(data)

    # 安排运行时延迟关闭并释放资源。
    @app.post("/api/runtime/shutdown")
    async def runtime_shutdown(payload: dict[str, Any] | None = None) -> ApiEnvelope:
        reason = str((payload or {}).get("reason", "browser-close"))
        release = await release_runtime_handles(reason)
        existing_task = app.state.shutdown_task
        if existing_task is not None and not existing_task.done():
            existing_task.cancel()
        app.state.shutdown_task = asyncio.create_task(delayed_runtime_shutdown(reason))
        return envelope({"scheduled": True, "activeClients": len(app.state.ws_clients), "release": release})

    # 释放运行时持有的硬件句柄。
    @app.post("/api/runtime/release_handles")
    async def runtime_release_handles(payload: dict[str, Any] | None = None) -> ApiEnvelope:
        reason = str((payload or {}).get("reason", "browser-close"))
        return envelope(await release_runtime_handles(reason))

    # 重新连接 HAL 服务。
    @app.post("/api/hal/reconnect")
    async def reconnect_hal() -> ApiEnvelope:
        return envelope(await commands.reconnect_hal())

    # 启动稳定性监测任务。
    @app.post("/api/stability/start")
    async def stability_start(payload: dict[str, Any] | None = None) -> ApiEnvelope:
        try:
            return envelope(await stability.start(payload))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail={"code": "STABILITY_RUNNING", "message": str(exc)}) from exc

    # 查询稳定性监测状态。
    @app.get("/api/stability/status")
    async def stability_status() -> ApiEnvelope:
        return envelope(stability.status())

    # 停止稳定性监测任务。
    @app.post("/api/stability/stop")
    async def stability_stop() -> ApiEnvelope:
        return envelope(await stability.stop())

    # 列出可用策略模型。
    @app.get("/api/models")
    async def list_models() -> ApiEnvelope:
        return envelope(policy.list_models())

    # 导入策略模型。
    @app.post("/api/models/import")
    async def import_model(payload: dict[str, Any]) -> ApiEnvelope:
        return envelope(await policy.import_model(payload))

    # 启动指定策略模型。
    @app.post("/api/models/{model_id}/start")
    async def start_model(model_id: str) -> ApiEnvelope:
        try:
            return envelope(await policy.start_model(model_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "MODEL_NOT_FOUND", "message": model_id}) from exc

    # 停止指定策略模型。
    @app.post("/api/models/{model_id}/stop")
    async def stop_model(model_id: str) -> ApiEnvelope:
        return envelope(await policy.stop_model(model_id))

    # 查询自动策略运行状态。
    @app.get("/api/auto/status")
    async def auto_status() -> ApiEnvelope:
        return envelope(await asyncio.to_thread(policy.auto_status))

    # 启动自动策略执行。
    @app.post("/api/auto/start")
    async def auto_start(payload: dict[str, Any] | None = None) -> ApiEnvelope:
        try:
            return envelope(await policy.auto_start(payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "MODEL_NOT_FOUND", "message": str(exc)}) from exc

    # 停止自动策略执行。
    @app.post("/api/auto/stop")
    async def auto_stop() -> ApiEnvelope:
        return envelope(await policy.auto_stop())

    # 向自动策略队列提交动作。
    @app.post("/api/auto/action")
    async def auto_action(payload: dict[str, Any]) -> ApiEnvelope:
        try:
            return envelope(await policy.queue_action(payload))
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail={"code": "ACTION_REJECTED", "message": str(exc)}) from exc

    # 派发自动策略队列中的下一步动作。
    @app.post("/api/auto/dispatch_next")
    async def auto_dispatch_next() -> ApiEnvelope:
        try:
            return envelope(await policy.dispatch_next())
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "HAL_DISPATCH_FAILED", "message": str(exc)}) from exc

    # 列出微调任务。
    @app.get("/api/fine_tune/jobs")
    async def list_fine_tune_jobs() -> ApiEnvelope:
        return envelope(policy.list_fine_tune_jobs())

    # 启动微调任务。
    @app.post("/api/fine_tune/jobs")
    async def start_fine_tune_job(payload: dict[str, Any]) -> ApiEnvelope:
        return envelope(await policy.start_fine_tune(payload))

    # 取消指定微调任务。
    @app.post("/api/fine_tune/jobs/{job_id}/cancel")
    async def cancel_fine_tune_job(job_id: str) -> ApiEnvelope:
        try:
            return envelope(await policy.cancel_fine_tune(job_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": job_id}) from exc

    # 触发运动急停。
    @app.post("/api/motion/emergency_stop")
    async def emergency_stop() -> ApiEnvelope:
        auto_stop_task = asyncio.create_task(policy.auto_stop())
        try:
            return envelope(await commands.emergency_stop())
        finally:
            await auto_stop_task

    # 所有运动轴执行回零。
    @app.post("/api/motion/home_all")
    async def home_all() -> ApiEnvelope:
        try:
            result = await commands.home_all()
            recorder.mark_reset_origin_all_returned()
            return envelope(result)
        except RuntimeError as exc:
            logs.error("[HAL]", f"home_all failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    # 查询运动工作原点状态。
    @app.get("/api/motion/origin")
    async def motion_origin_status() -> ApiEnvelope:
        return envelope(await asyncio.to_thread(commands.motion_origin_status))

    # 双侧记录当前工作原点。
    @app.post("/api/motion/origin/capture")
    async def capture_motion_origin_all(
        payload: Annotated[dict[str, Any] | None, Body()] = None,
    ) -> ApiEnvelope:
        try:
            confirm_large_drift = bool(payload.get("confirmLargeDrift")) if isinstance(payload, dict) else False
            return envelope(await commands.capture_motion_origin(confirm_large_drift=confirm_large_drift))
        except MotionOriginDriftConfirmationRequired as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ORIGIN_DRIFT_CONFIRM_REQUIRED",
                    "message": str(exc),
                    "drift": exc.drift,
                },
            ) from exc
        except RuntimeError as exc:
            logs.error("[HAL]", f"capture_motion_origin failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    # 清除双侧运动工作原点记录。
    @app.post("/api/motion/origin/clear")
    async def clear_motion_origin_all() -> ApiEnvelope:
        try:
            return envelope(await asyncio.to_thread(commands.clear_motion_origin))
        except RuntimeError as exc:
            logs.error("[HAL]", f"clear_motion_origin failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    # 恢复上一次运动工作原点记录。
    @app.post("/api/motion/origin/restore_previous")
    async def restore_previous_motion_origin() -> ApiEnvelope:
        try:
            return envelope(await commands.restore_previous_motion_origin())
        except RuntimeError as exc:
            logs.error("[HAL]", f"restore_previous_motion_origin failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    # 指定侧记录当前工作原点。
    @app.post("/api/motion/{side}/origin/capture")
    async def capture_motion_origin_side(
        side: str,
        payload: Annotated[dict[str, Any] | None, Body()] = None,
    ) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        try:
            confirm_large_drift = bool(payload.get("confirmLargeDrift")) if isinstance(payload, dict) else False
            return envelope(await commands.capture_motion_origin(side, confirm_large_drift=confirm_large_drift))
        except MotionOriginDriftConfirmationRequired as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ORIGIN_DRIFT_CONFIRM_REQUIRED",
                    "message": str(exc),
                    "drift": exc.drift,
                },
            ) from exc
        except RuntimeError as exc:
            logs.error("[HAL]", f"capture_motion_origin failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    # 清除指定侧运动工作原点记录。
    @app.post("/api/motion/{side}/origin/clear")
    async def clear_motion_origin_side(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        try:
            return envelope(await asyncio.to_thread(commands.clear_motion_origin, side))
        except RuntimeError as exc:
            logs.error("[HAL]", f"clear_motion_origin failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    # 使能指定侧全部运动轴。
    @app.post("/api/motion/{side}/enable_all")
    async def enable_motion_side(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        try:
            return envelope(await commands.enable_motion_side(side))
        except RuntimeError as exc:
            logs.error("[HAL]", f"enable_motion_side failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    # 禁用指定侧全部运动轴。
    @app.post("/api/motion/{side}/disable_all")
    async def disable_motion_side(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        try:
            return envelope(await commands.disable_motion_side(side))
        except RuntimeError as exc:
            logs.error("[HAL]", f"disable_motion_side failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    # 停止指定侧运动。
    @app.post("/api/motion/{side}/stop")
    async def stop_motion_side(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        try:
            return envelope(await commands.stop_motion_side(side))
        except RuntimeError as exc:
            logs.error("[HAL]", f"stop_motion_side failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    # 指定侧运动轴执行回零。
    @app.post("/api/motion/{side}/home")
    async def home_motion_side(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        try:
            return envelope(await commands.home_motion_side(side))
        except RuntimeError as exc:
            logs.error("[HAL]", f"home_motion_side failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    # 指定侧返回已记录工作原点。
    @app.post("/api/motion/{side}/return_origin")
    async def return_motion_origin_side(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        try:
            result = await commands.return_motion_origin_side(side)
            recorder.mark_reset_origin_returned(side)
            return envelope(result)
        except RuntimeError as exc:
            logs.error("[HAL]", f"return_motion_origin_side failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    # 执行手动单轴运动。
    @app.post("/api/motion/manual_axis_move")
    async def manual_axis_move(request: ManualAxisMoveRequest) -> ApiEnvelope:
        try:
            return envelope(await commands.manual_axis_move(request))
        except RuntimeError as exc:
            logs.error("[HAL]", f"manual_axis_move failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    @app.get("/api/policy/observation")
    async def policy_observation() -> ApiEnvelope:
        config = await get_config_async()
        joint_positions = list(telemetry.motion_positions)
        if _real_hardware_mode(config):
            try:
                motion_state = await hal.motion_state()
                raw_positions = motion_state.get("positions")
                raw_pulses = motion_state.get("pulses")
                if isinstance(raw_positions, list) and len(raw_positions) == 12:
                    joint_positions = [float(value) for value in raw_positions]
                if isinstance(raw_pulses, list) and len(raw_pulses) == 12:
                    pulses = [float(value) for value in raw_pulses]
                    joint_positions = relative_motion_positions(config, joint_positions, pulses)
                telemetry.motion_positions = list(joint_positions)
            except RuntimeError as exc:
                logs.error("[POLICY]", f"policy observation failed: {exc}")
                raise HTTPException(
                    status_code=503,
                    detail={"code": "POLICY_OBSERVATION_UNAVAILABLE", "message": str(exc)},
                ) from exc
        grippers = _policy_gripper_positions(config)
        return envelope({"state": lerobot_state_from_ui(joint_positions, grippers)})

    @app.post("/api/policy/action")
    async def policy_action(payload: Annotated[dict[str, Any], Body()]) -> ApiEnvelope:
        action = payload.get("action")
        if not isinstance(action, list) or len(action) != 14:
            raise HTTPException(
                status_code=400,
                detail={"code": "BAD_POLICY_ACTION", "message": "action must be a 14-element list"},
            )
        dry_run = bool(payload.get("dryRun", True))
        config = await get_config_async()
        current_state = lerobot_state_from_ui(list(telemetry.motion_positions), _policy_gripper_positions(config))
        try:
            plan = build_policy_action_plan(
                current_state,
                [float(value) for value in action],
                config,
                max_translation_um=float(payload.get("maxTranslationUm", 500.0)),
                max_rotation_deg=float(payload.get("maxRotationDeg", 0.2)),
                max_gripper_mm=float(payload.get("maxGripperMm", 1.0)),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail={"code": "POLICY_ACTION_BLOCKED", "message": str(exc)}) from exc
        if dry_run:
            return envelope({"dryRun": True, "sent": False, "plan": plan})
        results: dict[str, Any] = {"motion": {}, "grippers": {}}
        try:
            for side in ("left", "right"):
                motion_plan = plan["motion"][side]
                await commands.enable_motion_side(side)
                results["motion"][side] = await hal.command(
                    "motion.teleop_target_update",
                    _policy_motion_payload(side, motion_plan, config),
                )
            for side, target_key in (("left", "leftMm"), ("right", "rightMm")):
                gripper_side = cast(SideName, side)
                request = GripperCommandRequest(
                    side=gripper_side,
                    command="target",
                    targetMm=plan["grippers"][target_key],
                )
                results["grippers"][gripper_side] = await commands.gripper_command(request)
        except RuntimeError as exc:
            logs.error("[POLICY]", f"policy action send failed: {exc}")
            raise HTTPException(
                status_code=503,
                detail={"code": "POLICY_ACTION_UNAVAILABLE", "message": str(exc)},
            ) from exc
        return envelope({"dryRun": False, "sent": True, "plan": plan, "results": results})

    def _real_hardware_mode(config: dict[str, Any]) -> bool:
        mode = os.environ.get("APPSTATION_HAL_MODE") or config.get("hal", {}).get("mode", "real")
        return str(mode).lower() == "real"

    def _policy_gripper_positions(config: dict[str, Any]) -> list[float]:
        gripper = config.get("gripper", {}) if isinstance(config.get("gripper"), dict) else {}
        cached = list(telemetry.gripper_positions)
        left = cached[0] if len(cached) > 0 and cached[0] >= 0 else float(gripper.get("targetLeftMm", 0.0))
        right = cached[1] if len(cached) > 1 and cached[1] >= 0 else float(gripper.get("targetRightMm", 0.0))
        return [float(left), float(right)]

    def _policy_motion_payload(side: str, motion_plan: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
        deltas = motion_plan["deltas"]
        return {
            "side": side,
            "deltas": deltas,
            "translationStepLimitPulse": float(teleop.get("translationStepLimitPulse", 4000.0)),
            "rotationStepLimitPulse": float(teleop.get("rotationStepLimitPulse", 1250.0)),
            "translationPulseDeadband": float(teleop.get("translationPulseDeadband", 0.0)),
            "rotationPulseDeadband": float(teleop.get("rotationPulseDeadband", 0.0)),
            "enabledAxes": list(motion_plan["enabledAxes"]),
            "syncZeroDeltaTarget": True,
            "softLimitMin": list(motion_plan["softLimitMin"]),
            "softLimitMax": list(motion_plan["softLimitMax"]),
            "translationVelocityUiPerSec": float(teleop.get("translationMaxVelocityUmS", 4000.0)),
            "rotationVelocityUiPerSec": float(teleop.get("rotationMaxVelocityDegS", 6.0)),
            "translationStartVelocityUiPerSec": float(teleop.get("translationStartVelocityUmS", 300.0)),
            "rotationStartVelocityUiPerSec": float(teleop.get("rotationStartVelocityDegS", 0.5)),
            "accTimeSec": float(teleop.get("motionProfileAccSec", 0.05)),
            "decTimeSec": float(teleop.get("motionProfileDecSec", 0.05)),
        }

    # 确认并解除运动安全告警。
    @app.post("/api/motion/safety/acknowledge")
    async def acknowledge_safety() -> ApiEnvelope:
        return envelope(await commands.acknowledge_safety())

    # 对所有力传感器执行去皮。
    @app.post("/api/sensors/tare")
    async def tare_sensors() -> ApiEnvelope:
        try:
            return envelope(await commands.tare_force())
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "FORCE_UNAVAILABLE", "message": str(exc)}) from exc

    # 对指定侧力传感器执行去皮。
    @app.post("/api/force/{side}/tare")
    async def tare_force(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        try:
            return envelope(await commands.tare_force(side))
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "FORCE_UNAVAILABLE", "message": str(exc)}) from exc

    # 向指定侧夹爪发送控制命令。
    @app.post("/api/gripper/{side}/command")
    async def gripper_command_path(side: str, request: GripperCommandRequest) -> ApiEnvelope:
        if side != request.side:
            raise HTTPException(
                status_code=400,
                detail={"code": "BAD_SIDE", "message": "path side and body side differ"},
            )
        try:
            result = envelope(await commands.gripper_command(request))
            gripper_tele.reset_side(side)
            return result
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "GRIPPER_UNAVAILABLE", "message": str(exc)}) from exc

    # 查询指定侧夹爪当前位置。
    @app.get("/api/gripper/{side}/position")
    async def gripper_position(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        config = await get_config_async()
        if gripper_workers.is_enabled(config):
            result = await asyncio.to_thread(gripper_workers.position, config, side)
        elif native_teleop_config(config):
            status = native_gripper_status(config)
            positions = status["positionMm"]
            return envelope(
                {
                    "ok": True,
                    "message": status["message"],
                    "position_mm": positions.get(side, 0.0) if isinstance(positions, dict) else 0.0,
                    "nativeManaged": True,
                    "details": status,
                }
            )
        else:
            result = await asyncio.to_thread(hardware.gripper.position, config, side)
        if not result.ok:
            raise HTTPException(status_code=503, detail={"code": "GRIPPER_UNAVAILABLE", "message": result.message})
        return envelope(result.__dict__)

    # 诊断指定侧夹爪通信与状态。
    @app.post("/api/gripper/{side}/diagnose")
    async def gripper_diagnose(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        config = await get_config_async()
        if gripper_workers.is_enabled(config):
            worker_status = await asyncio.to_thread(gripper_workers.status, config)
            side_status = worker_status.get("sides", {}).get(side, {})
            logs.info("[GRIPPER]", str(side_status.get("message", "worker status")))
            return envelope(
                {
                    "ok": bool(side_status.get("ok")),
                    "message": side_status.get("message", "worker status"),
                    "position_mm": side_status.get("positionMm"),
                    "details": side_status,
                }
            )
        if native_teleop_config(config):
            status = native_gripper_status(config)
            positions = status["positionMm"]
            return envelope(
                {
                    "ok": True,
                    "message": status["message"],
                    "position_mm": positions.get(side, 0.0) if isinstance(positions, dict) else 0.0,
                    "nativeManaged": True,
                    "details": status,
                }
            )
        result = await asyncio.to_thread(hardware.gripper.diagnose, config, side)
        logs.info("[GRIPPER]" if result.ok else "[GRIPPER]", result.message)
        return envelope(result.__dict__)

    # 启动夹爪遥操作。
    @app.post("/api/teleop/gripper/start")
    async def gripper_tele_start() -> ApiEnvelope:
        gripper_tele.start("manual")
        return envelope(gripper_tele.get_status())

    # 停止夹爪遥操作。
    @app.post("/api/teleop/gripper/stop")
    async def gripper_tele_stop() -> ApiEnvelope:
        gripper_tele.stop("manual")
        return envelope(gripper_tele.get_status())

    # 查询夹爪遥操作状态。
    @app.get("/api/teleop/gripper/status")
    async def gripper_tele_status() -> ApiEnvelope:
        return envelope(gripper_tele.get_status())

    # 切换遥操作离合状态。
    @app.post("/api/teleop/clutch_toggle")
    async def clutch_toggle() -> ApiEnvelope:
        logs.info("[HAL]", "teleop clutch toggle requested")
        return envelope({"clutchActive": True})

    # 设置遥操作速度倍率。
    @app.post("/api/teleop/speed")
    async def teleop_speed(payload: dict[str, Any]) -> ApiEnvelope:
        mode = payload.get("mode", "unknown")
        logs.info("[HAL]", f"teleop speed mode={mode}")
        return envelope({"mode": mode})

    # 建立指定侧 Omega.7 遥操作逻辑连接。
    @app.post("/api/teleop/{side}/connect")
    async def teleop_connect(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        side_name = cast(SideName, side)
        config = await asyncio.to_thread(set_teleop_logical_connection, side_name, True)
        mapped_side = teleop_target_side_for_source(side_name, config)
        schedule_teleop_background(
            sync_teleop_logical_connection(side_name, True, config),
            f"teleop-{side}-connect-sync",
        )
        logs.info(
            "[HAL]",
            f"{side} Omega.7 logical connect accepted; background sync scheduled",
        )
        return envelope(
            {
                "side": side,
                "connected": True,
                "mappedSide": mapped_side,
                "backgroundSync": True,
                "message": "logical connection accepted; hardware sync continues in background",
            }
        )

    # 断开指定侧 Omega.7 遥操作逻辑连接。
    @app.post("/api/teleop/{side}/disconnect")
    async def teleop_disconnect(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        side_name = cast(SideName, side)
        config = await asyncio.to_thread(set_teleop_logical_connection, side_name, False)
        mapped_side = teleop_target_side_for_source(side_name, config)
        schedule_teleop_background(
            sync_teleop_logical_connection(side_name, False, config),
            f"teleop-{side}-disconnect-sync",
        )
        logs.info("[HAL]", f"{side} Omega.7 logical disconnect accepted; background sync scheduled")
        return envelope({"side": side, "connected": False, "stoppedSide": mapped_side, "backgroundSync": True})

    # 查询遥操作设备状态。
    @app.get("/api/teleop/state")
    async def teleop_state() -> ApiEnvelope:
        try:
            omega_state = await hal.omega_state()
            config = await get_config_async()
            return envelope(mask_omega_state_for_logical_connection(omega_state, config))
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "OMEGA_UNAVAILABLE", "message": str(exc)}) from exc

    # 查询遥操作映射状态。
    @app.get("/api/teleop/mapping/status")
    async def teleop_mapping_status() -> ApiEnvelope:
        config = await get_config_async()
        return envelope(teleop_mapper.status(config))

    # 设置指定侧重力补偿状态。
    @app.post("/api/teleop/{side}/gravity_compensation")
    async def teleop_gravity_compensation(side: str, payload: dict[str, Any] | None = None) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        enabled = bool((payload or {}).get("enabled", True))
        config = await get_config_async()
        config["teleop"][f"{side}GravityCompensation"] = enabled
        config["teleop"][f"{side}ForceFeedback"] = enabled
        saved = await asyncio.to_thread(settings.save_config, config, emit_log=False)
        await hal.command(
            "omega7.gravity_compensation",
            {
                "leftEnabled": bool(saved["teleop"].get("leftGravityCompensation", False)),
                "rightEnabled": bool(saved["teleop"].get("rightGravityCompensation", False)),
            },
        )
        logs.info("[HAL]", f"{side} Omega.7 gravity compensation={enabled}")
        return envelope({"side": side, "enabled": enabled, "config": saved})

    # 对指定侧力反馈执行归零。
    @app.post("/api/teleop/{side}/zero_force_feedback")
    async def teleop_zero_force_feedback(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        config = await get_config_async()
        open_id = int(config["teleop"].get(f"{side}OpenId", 0 if side == "left" else 1))
        await hal.command("omega7.zero_force_feedback", {"openId": open_id})
        logs.info("[HAL]", f"{side} Omega.7 zero force feedback requested")
        return envelope({"side": side, "openId": open_id})

    # 探测指定相机可用性。
    @app.post("/api/cameras/{camera}/enumerate")
    async def camera_enumerate(camera: str) -> ApiEnvelope:
        config = await get_config_async()
        probe = await asyncio.to_thread(hardware.cameras.probe, config)
        logs.info("[CAMERA]", f"{camera} enumerate requested: {probe.message}")
        return envelope({"camera": camera, "ok": probe.ok, "message": probe.message})

    # 重新连接指定相机。
    @app.post("/api/cameras/{camera}/reconnect")
    async def camera_reconnect(camera: str) -> ApiEnvelope:
        config = await get_config_async()
        probe = await asyncio.to_thread(hardware.cameras.reconnect, config, camera)
        logs.info("[CAMERA]", f"{camera} reconnect requested: {probe.message}")
        return envelope({"camera": camera, "ok": probe.ok, "message": probe.message})

    # 应用指定相机调参配置。
    @app.post("/api/cameras/{camera}/tuning/apply")
    async def camera_tuning_apply(camera: str, config: AppConfig | None = None) -> ApiEnvelope:
        if camera not in {"global", "wrist_left", "wrist_right"}:
            raise HTTPException(
                status_code=400,
                detail={"code": "BAD_CAMERA", "message": "camera must be global, wrist_left, or wrist_right"},
            )
        active = (
            await asyncio.to_thread(settings.save_config, config.model_dump(mode="json"), emit_log=False)
            if config is not None
            else await get_config_async()
        )
        try:
            result = await asyncio.to_thread(hardware.cameras.apply_tuning, active, camera)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "CAMERA_UNAVAILABLE", "message": str(exc)}) from exc
        logs.info("[CAMERA]", f"{camera} tuning applied: {result['profile']}")
        return envelope(result)

    # 获取指定相机 JPEG 快照。
    @app.get("/api/cameras/{camera}/snapshot")
    async def camera_snapshot(camera: str) -> Response:
        if camera not in {"global", "wrist_left", "wrist_right"}:
            raise HTTPException(
                status_code=400,
                detail={"code": "BAD_CAMERA", "message": "camera must be global, wrist_left, or wrist_right"},
            )
        try:
            config = await get_config_async()
            jpeg = await asyncio.to_thread(hardware.cameras.snapshot, config, camera)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "CAMERA_UNAVAILABLE", "message": str(exc)}) from exc
        return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # 获取指定相机 MJPEG 视频流。
    @app.get("/api/cameras/{camera}/stream")
    async def camera_stream(camera: str) -> StreamingResponse:
        if camera not in {"global", "wrist_left", "wrist_right"}:
            raise HTTPException(
                status_code=400,
                detail={"code": "BAD_CAMERA", "message": "camera must be global, wrist_left, or wrist_right"},
            )

        async def frames() -> AsyncIterator[bytes]:
            period = 1.0 / 30.0
            last_sequence = -1
            while True:
                started = time.monotonic()
                try:
                    config = await get_config_async()
                    last_sequence, jpeg = await asyncio.to_thread(
                        hardware.cameras.wait_for_frame,
                        config,
                        camera,
                        last_sequence,
                        2.0,
                    )
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Cache-Control: no-store\r\n\r\n"
                        + jpeg
                        + b"\r\n"
                    )
                except RuntimeError:
                    await asyncio.sleep(0.25)
                    continue
                await asyncio.sleep(max(0.0, period - (time.monotonic() - started)))

        return StreamingResponse(
            frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )

    # 枚举系统相机设备。
    @app.get("/api/cameras/enumerate")
    async def camera_enumerate_all() -> ApiEnvelope:
        config = await get_config_async()
        devices = await asyncio.to_thread(hardware.cameras.enumerate_devices, config)
        return envelope({"devices": devices})

    # 连接 PICO ADB 设备。
    @app.post("/api/pico/adb/connect")
    async def pico_adb_connect() -> ApiEnvelope:
        config = await get_config_async()
        result = await asyncio.to_thread(hardware.pico.connect, config)
        logs.info("[CAMERA]", result.message)
        return envelope(result.__dict__)

    # 启动 PICO 视觉服务。
    @app.post("/api/pico/vision/start")
    async def pico_vision_start() -> ApiEnvelope:
        config = await get_config_async()
        result = await asyncio.to_thread(hardware.pico.start_vision, config)
        logs.info("[CAMERA]", result.message)
        return envelope(result.__dict__)

    # 停止 PICO 视觉服务。
    @app.post("/api/pico/vision/stop")
    async def pico_vision_stop() -> ApiEnvelope:
        config = await get_config_async()
        result = await asyncio.to_thread(hardware.pico.stop_vision, config)
        logs.info("[CAMERA]", result.message)
        return envelope(result.__dict__)

    # 检查 PICO 设备状态。
    @app.post("/api/pico/status/check")
    async def pico_status_check() -> ApiEnvelope:
        config = await get_config_async()
        result = await asyncio.to_thread(hardware.pico.status, config)
        logs.info("[CAMERA]", result.message)
        return envelope(result.__dict__)

    # 创建数据录制会话。
    @app.post("/api/record/session/create")
    async def create_record_session(payload: dict[str, Any]) -> ApiEnvelope:
        dataset_name = payload.get("dataset_name", "dataset")
        task = payload.get("task", "")
        try:
            await require_hardware_recognized("record", require_camera=True)
            if await native_teleop_enabled_async():
                await stop_aux_native_teleop_sources("record session create")
            result = await recorder.start_session(str(dataset_name), str(task))
            await start_gripper_teleop_source("recording")
            return envelope(result)
        except RuntimeError as exc:
            if "native LeRobot dataset is required" in str(exc):
                raise HTTPException(
                    status_code=503,
                    detail={"code": "NATIVE_DATASET_UNAVAILABLE", "message": str(exc)},
                ) from exc
            raise HTTPException(status_code=409, detail={"code": "RECORDING_BUSY", "message": str(exc)}) from exc

    # 保存当前录制片段。
    @app.post("/api/record/episode/save")
    async def save_episode() -> ApiEnvelope:
        try:
            await stop_aux_native_teleop_sources("record episode save")
            try:
                result = await recorder.save_episode()
            finally:
                gripper_tele.stop("recording")
            return envelope(result)
        except DatasetSaveError as exc:
            raise HTTPException(status_code=500, detail={"code": "RECORDING_SAVE_FAILED", "message": str(exc)}) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail={"code": "RECORDING_NOT_ACTIVE", "message": str(exc)}) from exc

    # 丢弃当前录制片段。
    @app.post("/api/record/episode/discard")
    async def discard_episode() -> ApiEnvelope:
        try:
            if await native_teleop_enabled_async():
                await stop_aux_native_teleop_sources("record episode discard")
            try:
                result = await recorder.discard_episode()
            finally:
                gripper_tele.stop("recording")
            return envelope(result)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail={"code": "RECORDING_NOT_ACTIVE", "message": str(exc)}) from exc

    # 结束当前录制会话。
    @app.post("/api/record/session/finish")
    async def finish_session() -> ApiEnvelope:
        await stop_aux_native_teleop_sources("record session finish")
        try:
            result = await recorder.finish_session()
        finally:
            gripper_tele.stop("recording")
        return envelope(result)

    # 跳过录制重置步骤。
    @app.post("/api/record/reset/skip")
    async def skip_reset() -> ApiEnvelope:
        try:
            if await native_teleop_enabled_async():
                record_state = await asyncio.to_thread(recorder.status)
                if bool(record_state.get("resetPending")) and not bool(record_state.get("resetReady")):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "RECORD_RESET_NOT_READY",
                            "message": "record reset work origin is not ready",
                        },
                    )
                await stop_aux_native_teleop_sources("record reset skip")
            result = await recorder.skip_reset()
            await start_gripper_teleop_source("recording")
            return envelope(result)
        except RuntimeError as exc:
            message = str(exc)
            code = (
                "RECORD_RESET_NOT_READY"
                if "reset is not ready" in message or "reset work origin" in message
                else "RECORDING_NOT_ACTIVE"
            )
            raise HTTPException(status_code=409, detail={"code": code, "message": str(exc)}) from exc

    # 查询录制状态。
    @app.get("/api/record/status")
    async def record_status() -> ApiEnvelope:
        return envelope(await asyncio.to_thread(recorder.status))

    # 列出数据集。
    @app.get("/api/datasets")
    async def list_datasets() -> ApiEnvelope:
        datasets = await asyncio.to_thread(recorder.list_datasets)
        return envelope({"datasets": datasets})

    # 创建数据集。
    @app.post("/api/datasets")
    async def create_dataset(payload: dict[str, Any]) -> ApiEnvelope:
        try:
            return envelope(await asyncio.to_thread(recorder.create_dataset, payload))
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "NATIVE_DATASET_UNAVAILABLE", "message": str(exc)},
            ) from exc

    # 更新 Hugging Face Hub 上传开关，不接收或保存 token。
    @app.patch("/api/datasets/hub")
    async def update_dataset_hub(payload: dict[str, Any]) -> ApiEnvelope:
        return envelope(await asyncio.to_thread(recorder.update_hub_settings, payload))

    # 更新指定数据集元信息。
    @app.patch("/api/datasets/{dataset_id}")
    async def update_dataset(dataset_id: str, payload: dict[str, Any]) -> ApiEnvelope:
        try:
            return envelope(await asyncio.to_thread(recorder.update_dataset, dataset_id, payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc

    # 删除指定数据集。
    @app.delete("/api/datasets/{dataset_id}")
    async def delete_dataset(dataset_id: str) -> ApiEnvelope:
        try:
            return envelope(await asyncio.to_thread(recorder.delete_dataset, dataset_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "DATASET_DELETE_REFUSED", "message": str(exc)},
            ) from exc

    # 保存指定数据集的审核结果。
    @app.post("/api/datasets/{dataset_id}/review/save")
    async def save_dataset_review(dataset_id: str) -> ApiEnvelope:
        try:
            return envelope(await asyncio.to_thread(recorder.save_review, dataset_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc

    # 导出指定数据集。
    @app.post("/api/datasets/{dataset_id}/export")
    async def export_dataset(dataset_id: str) -> ApiEnvelope:
        try:
            return envelope(await asyncio.to_thread(recorder.export_dataset, dataset_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc

    # 查询指定数据集统计信息。
    @app.get("/api/datasets/{dataset_id}/stats")
    async def dataset_stats(dataset_id: str) -> ApiEnvelope:
        try:
            return envelope(await asyncio.to_thread(recorder.dataset_stats, dataset_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc

    # 拆分指定数据集。
    @app.post("/api/datasets/{dataset_id}/split")
    async def split_dataset(dataset_id: str, payload: dict[str, Any]) -> ApiEnvelope:
        try:
            return envelope(await asyncio.to_thread(recorder.split_dataset, dataset_id, payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc

    # 清理指定数据集。
    @app.post("/api/datasets/{dataset_id}/clean")
    async def clean_dataset(dataset_id: str, payload: dict[str, Any]) -> ApiEnvelope:
        try:
            return envelope(await asyncio.to_thread(recorder.clean_dataset, dataset_id, payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc

    # 推送指定数据集到外部目标。
    @app.post("/api/datasets/{dataset_id}/push")
    async def push_dataset(dataset_id: str, payload: dict[str, Any]) -> ApiEnvelope:
        try:
            return envelope(await asyncio.to_thread(recorder.push_dataset, dataset_id, payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail={"code": "DATASET_PUSH_FAILED", "message": str(exc)}) from exc

    # 查询指定数据集片段详情。
    @app.get("/api/datasets/{dataset_id}/episodes/{episode_id}")
    async def dataset_episode_detail(dataset_id: str, episode_id: str) -> ApiEnvelope:
        """Return one episode review detail in the standard envelope."""
        try:
            return envelope(await asyncio.to_thread(recorder.episode_detail, dataset_id, episode_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "EPISODE_NOT_FOUND", "message": episode_id}) from exc

    # 更新指定数据集片段信息。
    @app.patch("/api/datasets/{dataset_id}/episodes/{episode_id}")
    async def update_dataset_episode(dataset_id: str, episode_id: str, payload: dict[str, Any]) -> ApiEnvelope:
        try:
            return envelope(await asyncio.to_thread(recorder.update_episode, dataset_id, episode_id, payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "EPISODE_NOT_FOUND", "message": episode_id}) from exc

    # 删除指定数据集片段。
    @app.delete("/api/datasets/{dataset_id}/episodes/{episode_id}")
    async def delete_dataset_episode(dataset_id: str, episode_id: str) -> ApiEnvelope:
        try:
            return envelope(await asyncio.to_thread(recorder.delete_episode, dataset_id, episode_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "EPISODE_NOT_FOUND", "message": episode_id}) from exc

    # 下载指定数据集文件。
    @app.get("/api/datasets/{dataset_id}/file")
    async def dataset_file(dataset_id: str, path: str) -> FileResponse:
        try:
            target = await asyncio.to_thread(recorder.resolve_file, dataset_id, path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "FILE_NOT_FOUND", "message": path}) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail={"code": "BAD_FILE_PATH", "message": str(exc)}) from exc
        return FileResponse(target)

    # 获取指定数据集帧图像。
    @app.get("/api/datasets/{dataset_id}/frame_image")
    async def dataset_frame_image(dataset_id: str, episode_id: str, camera: str, frame: int) -> Response:
        try:
            jpeg = await asyncio.to_thread(recorder.resolve_frame_image, dataset_id, episode_id, camera, frame)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "FRAME_IMAGE_NOT_FOUND", "message": f"{episode_id}:{camera}:{frame}"},
            ) from exc
        return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # 推送遥测、日志与运行状态的 WebSocket 通道。
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        shutdown_task = app.state.shutdown_task
        if shutdown_task is not None:
            if not shutdown_task.done():
                shutdown_task.cancel()
            app.state.shutdown_task = None
            logs.info("[BACKEND]", "runtime shutdown cancelled; WebSocket client reconnected")
        client_token = id(ws)
        app.state.ws_clients.add(client_token)
        last_log_id = 0
        last_loop_error = ""
        last_loop_error_at = 0.0
        # Cache HAL HTTP reads so the WS loop is not dominated by localhost round trips.
        cached_health = await hal.health()
        last_health_at = time.monotonic()
        cached_motion_state: dict[str, Any] | None = None
        cached_omega_hands: list[dict[str, Any]] | None = None
        last_motion_state_at = 0.0
        last_omega_state_at = 0.0
        # 30Hz upstream is plenty for charts/UI; the underlying motion thread runs at 1kHz.
        # Higher rates were the dominant source of frontend jank.
        ws_period = float(os.environ.get("APPSTATION_WS_PERIOD_SEC", "0.033"))
        health_period = float(os.environ.get("APPSTATION_HEALTH_PERIOD_SEC", "1.0"))
        state_period = float(os.environ.get("APPSTATION_HAL_STATE_PERIOD_SEC", "0.05"))
        try:
            while True:
                try:
                    now = time.monotonic()
                    if now - last_health_at >= health_period:
                        try:
                            cached_health = await hal.health()
                        except Exception as exc:  # noqa: BLE001
                            logs.error("[HAL]", f"health refresh failed: {exc}")
                        last_health_at = now
                    hal_health = cached_health
                    active_config = await get_config_async()

                    motion_state: dict[str, Any] | None = None
                    if hal_health.connected and hal_health.ltdmc_ok:
                        if now - last_motion_state_at >= state_period:
                            last_motion_state_at = now
                            try:
                                # 运动轴状态变化快，但 50ms 缓存足够支撑 UI，并减少 HAL HTTP 往返。
                                cached_motion_state = await hal.motion_state()
                            except RuntimeError as exc:
                                cached_motion_state = None
                                logs.error("[HAL]", f"motion state failed: {exc}")
                        motion_state = cached_motion_state
                    motion_positions = None
                    motion_pulses = None
                    motion_estop_active = None
                    motion_enabled = None
                    motion_axis_enabled = None
                    if motion_state is not None:
                        raw_positions = motion_state.get("positions")
                        if isinstance(raw_positions, list) and len(raw_positions) == 12:
                            motion_positions = [float(value) for value in raw_positions]
                        raw_pulses = motion_state.get("pulses")
                        if isinstance(raw_pulses, list) and len(raw_pulses) == 12:
                            motion_pulses = [float(value) for value in raw_pulses]
                        if motion_positions is not None:
                            motion_positions = relative_motion_positions(
                                active_config,
                                motion_positions,
                                motion_pulses,
                            )
                        if isinstance(motion_state.get("estop_active"), bool):
                            motion_estop_active = bool(motion_state["estop_active"])
                        raw_enabled = motion_state.get("enabled")
                        if isinstance(raw_enabled, list) and len(raw_enabled) == 12:
                            left_axis_enabled = normalize_motion_axis_enabled("left", raw_enabled[:6])
                            right_axis_enabled = normalize_motion_axis_enabled("right", raw_enabled[6:12])
                            motion_axis_enabled = {
                                "left": left_axis_enabled,
                                "right": right_axis_enabled,
                            }
                            left_known_enabled = [value for value in left_axis_enabled if value is not None]
                            right_known_enabled = [value for value in right_axis_enabled if value is not None]
                            motion_enabled = {
                                "left": (
                                    all(value is True for value in left_known_enabled)
                                    if left_known_enabled
                                    else None
                                ),
                                "right": (
                                    all(value is True for value in right_known_enabled)
                                    if right_known_enabled
                                    else None
                                ),
                            }
                        elif isinstance(raw_enabled, dict):
                            raw_left_enabled = raw_enabled.get("left")
                            raw_right_enabled = raw_enabled.get("right")
                            motion_enabled = {
                                "left": raw_left_enabled if isinstance(raw_left_enabled, bool) else None,
                                "right": raw_right_enabled if isinstance(raw_right_enabled, bool) else None,
                            }
                    omega_hands: list[dict[str, Any]] | None = None
                    if hal_health.connected and hal_health.omega7_ok:
                        if now - last_omega_state_at >= state_period:
                            last_omega_state_at = now
                            try:
                                # Omega.7 状态随遥操作连接一起推送，前端再按逻辑连接过滤显示。
                                omega_state = await hal.omega_state()
                                raw_hands = omega_state.get("hands")
                                cached_omega_hands = [
                                    item for item in raw_hands if isinstance(item, dict)
                                ] if isinstance(raw_hands, list) else None
                                if cached_omega_hands is not None:
                                    emit_omega_device_logs(logs, active_config, cached_omega_hands)
                            except RuntimeError as exc:
                                cached_omega_hands = None
                                logs.error("[HAL]", f"omega state failed: {exc}")
                        omega_hands = cached_omega_hands
                    hal_ok = hal_health.connected and (hal_health.mode != "real" or hal_health.ltdmc_ok)
                    use_gripper_workers = gripper_workers.is_enabled(active_config)
                    frame = telemetry.next_frame(
                        motion_positions,
                        motion_estop_active=motion_estop_active,
                        motion_enabled=motion_enabled,
                        motion_axis_enabled=motion_axis_enabled,
                        omega_hands=omega_hands,
                        native_gripper_status=(
                            native_gripper_status(active_config)
                            if native_teleop_config(active_config) and not use_gripper_workers
                            else None
                        ),
                        hal_ok=hal_ok,
                    )
                    frame.halOk = frame.halOk and hal_ok
                    frame.processStatus[0].label = "HalServer.exe" if hal_health.mode == "real" else "Test HAL boundary"
                    frame.processStatus[0].status = "running" if hal_health.connected else "error"
                    await ws.send_json({"type": "telemetry", "data": frame.model_dump(mode="json")})
                    # 日志和遥测走同一条 WebSocket，前端不需要再轮询日志接口。
                    for entry in logs.entries_after(last_log_id):
                        await ws.send_json({"type": "log", "data": entry.model_dump(mode="json")})
                        last_log_id = max(last_log_id, entry.id)
                    await asyncio.sleep(ws_period)
                except WebSocketDisconnect:
                    return
                except Exception as exc:
                    if ws.client_state != WebSocketState.CONNECTED:
                        return
                    now = time.monotonic()
                    message = str(exc)
                    if message != last_loop_error or now - last_loop_error_at > 2:
                        logs.error("[BACKEND]", f"websocket telemetry loop recovered: {message}")
                        last_loop_error = message
                        last_loop_error_at = now
                    await asyncio.sleep(0.1)
        except WebSocketDisconnect:
            return
        finally:
            app.state.ws_clients.discard(client_token)

    return app


def make_hal_client(config: dict[str, Any], logs: LogService) -> HalClient:
    # HAL 模式优先读取环境变量，方便测试脚本覆盖持久化配置。
    env_mode = os.environ.get("APPSTATION_HAL_MODE")
    hal_config = config.get("hal", {})
    mode = str(env_mode or hal_config.get("mode", "real")).lower()
    if mode == "real":
        base_url = str(os.environ.get("APPSTATION_HAL_BASE_URL") or hal_config.get("baseUrl", "http://localhost:8091"))
        timeout_ms = int(hal_config.get("timeoutMs", 5000))
        logs.warning("[HAL]", f"Real HAL mode enabled: {base_url}")
        return RealHalClient(base_url, timeout_ms, logs)
    logs.info("[HAL]", "Test HAL mode enabled")
    return TestHalClient(logs)
