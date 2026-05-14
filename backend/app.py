from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from starlette.websockets import WebSocketState

from backend.core.config import SettingsService
from backend.core.logging import LogService, now_ms
from backend.core.schemas import (
    ApiEnvelope,
    AppConfig,
    GripperCommandRequest,
    ManualAxisMoveRequest,
    SettingsCommandRequest,
    SnapshotCreateRequest,
    SnapshotScope,
)
from backend.core.units import pulses_to_ui_state
from backend.hal_client.client import HalClient, RealHalClient, TestHalClient
from backend.services.command_service import CommandService
from backend.services.dataset_recorder import DatasetRecorderService, DatasetSaveError
from backend.services.gripper_tele_service import GripperTeleService
from backend.services.gripper_worker_service import GripperWorkerService
from backend.services.hardware_service import HardwareService
from backend.services.policy_service import PolicyService
from backend.services.stability_monitor import StabilityMonitorService
from backend.services.telemetry_hub import TelemetryHub
from backend.services.teleop_mapping import TeleopMappingService


def envelope(data: dict[str, Any] | None = None) -> ApiEnvelope:
    # 统一 API 响应外壳，便于前端按 ok/data/ts 的固定结构处理结果。
    return ApiEnvelope(ok=True, data=data or {}, ts=now_ms())


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
    relative_positions = pulses_to_ui_state(relative_pulses)
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
    logs = LogService()
    settings = SettingsService(runtime_dir or runtime_dir_from_env(), logs)
    hardware = HardwareService(settings, logs)
    gripper_workers = GripperWorkerService(settings, logs)
    telemetry = TelemetryHub(settings, hardware, gripper_workers)
    hal = make_hal_client(settings.get_config(), logs)
    commands = CommandService(settings, telemetry, hal, logs, hardware, gripper_workers)
    teleop_mapper = TeleopMappingService(settings, hal, logs)
    gripper_tele = GripperTeleService(settings, hal, hardware, logs, gripper_workers)
    recorder = DatasetRecorderService(settings, hardware, hal, telemetry, logs, teleop_mapper)
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
    stability.set_ws_client_count_provider(lambda: len(app.state.ws_clients))

    def set_teleop_logical_connection(side: str, connected: bool) -> dict[str, Any]:
        # 逻辑连接状态写入配置，让页面刷新后仍能保留操作员显式选择。
        config = settings.get_config()
        config["teleop"][f"{side}Connected"] = connected
        return settings.save_config(config, emit_log=False)

    async def release_runtime_handles(reason: str) -> dict[str, Any]:
        gripper_tele.stop()
        released_grippers: list[str] = []
        gripper_errors: dict[str, str] = {}
        for side in ("left", "right"):
            try:
                await commands.gripper_command(GripperCommandRequest(side=side, command="disable"))
                released_grippers.append(side)
            except RuntimeError as exc:
                gripper_errors[side] = str(exc)
                logs.error("[GRIPPER]", f"{side} gripper close-release failed: {exc}")
            set_teleop_logical_connection(side, False)
        config = settings.get_config()
        config["gripper"]["leftEnabled"] = False
        config["gripper"]["rightEnabled"] = False
        settings.save_config(config, emit_log=False)
        gripper_workers.stop_all()
        await teleop_mapper.stop("teleop-connect")
        await teleop_mapper.stop("recording")
        logs.warning("[BACKEND]", f"runtime handles released: {reason}")
        return {
            "releasedGrippers": released_grippers,
            "gripperErrors": gripper_errors,
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
    async def apply_startup_home_if_enabled() -> None:
        startup_config = settings.get_config().get("motion", {}).get("homeOnStartup", {})
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
        gripper_workers.stop_all()

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        hal_health = await hal.health()
        health_config = settings.get_config()
        use_gripper_workers = gripper_workers.is_enabled(health_config)
        hardware_status = await asyncio.to_thread(hardware.status, include_gripper=not use_gripper_workers)
        if use_gripper_workers:
            hardware_status["gripper"] = gripper_workers.status(health_config)
        return {
            "ok": True,
            "backend": "running",
            "mode": hal_health.mode,
            "hal": hal_health.__dict__,
            "hardware": hardware_status,
            "ts": now_ms(),
        }

    @app.get("/api/hardware/status")
    async def hardware_status() -> dict[str, Any]:
        config = settings.get_config()
        use_gripper_workers = gripper_workers.is_enabled(config)
        status = await asyncio.to_thread(hardware.status, include_gripper=not use_gripper_workers)
        if use_gripper_workers:
            status["gripper"] = gripper_workers.status(config)
        return status

    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        return settings.get_config()

    @app.put("/api/settings")
    async def put_settings(config: AppConfig) -> dict[str, Any]:
        saved = settings.save_config(config.model_dump(mode="json"))
        await asyncio.to_thread(gripper_workers.sync_config, saved)
        return saved

    @app.post("/api/settings/apply")
    async def apply_settings(config: AppConfig | None = None) -> ApiEnvelope:
        try:
            active = settings.apply_config(config.model_dump(mode="json") if config is not None else None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"code": "VALIDATION_ERROR", "message": str(exc)}) from exc
        await asyncio.to_thread(gripper_workers.sync_config, active)
        return envelope({"config": active})

    @app.get("/api/settings/snapshots")
    async def list_snapshots(scope: Annotated[SnapshotScope | None, Query()] = None) -> list[dict[str, Any]]:
        return settings.list_snapshots(scope)

    @app.post("/api/settings/snapshots")
    async def create_snapshot(request: SnapshotCreateRequest) -> ApiEnvelope:
        snapshot = settings.create_snapshot(request)
        return envelope({"snapshot": snapshot, "snapshots": settings.list_snapshots(request.scope)})

    @app.post("/api/settings/snapshots/{snapshot_id}/apply")
    async def apply_snapshot(snapshot_id: str) -> ApiEnvelope:
        try:
            config = settings.apply_snapshot(snapshot_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "snapshot not found"}) from exc
        return envelope({"config": config, "snapshots": settings.list_snapshots()})

    @app.delete("/api/settings/snapshots/{snapshot_id}")
    async def delete_snapshot(snapshot_id: str) -> ApiEnvelope:
        try:
            settings.delete_snapshot(snapshot_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "snapshot not found"}) from exc
        return envelope({"snapshots": settings.list_snapshots()})

    @app.post("/api/settings/log_command")
    async def settings_log_command(request: SettingsCommandRequest) -> ApiEnvelope:
        data = await commands.generic_command(request)
        return envelope(data)

    @app.post("/api/runtime/shutdown")
    async def runtime_shutdown(payload: dict[str, Any] | None = None) -> ApiEnvelope:
        reason = str((payload or {}).get("reason", "browser-close"))
        release = await release_runtime_handles(reason)
        existing_task = app.state.shutdown_task
        if existing_task is not None and not existing_task.done():
            existing_task.cancel()
        app.state.shutdown_task = asyncio.create_task(delayed_runtime_shutdown(reason))
        return envelope({"scheduled": True, "activeClients": len(app.state.ws_clients), "release": release})

    @app.post("/api/runtime/release_handles")
    async def runtime_release_handles(payload: dict[str, Any] | None = None) -> ApiEnvelope:
        reason = str((payload or {}).get("reason", "browser-close"))
        return envelope(await release_runtime_handles(reason))

    @app.post("/api/hal/reconnect")
    async def reconnect_hal() -> ApiEnvelope:
        return envelope(await commands.reconnect_hal())

    @app.post("/api/stability/start")
    async def stability_start(payload: dict[str, Any] | None = None) -> ApiEnvelope:
        try:
            return envelope(await stability.start(payload))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail={"code": "STABILITY_RUNNING", "message": str(exc)}) from exc

    @app.get("/api/stability/status")
    async def stability_status() -> ApiEnvelope:
        return envelope(stability.status())

    @app.post("/api/stability/stop")
    async def stability_stop() -> ApiEnvelope:
        return envelope(await stability.stop())

    @app.get("/api/models")
    async def list_models() -> ApiEnvelope:
        return envelope(policy.list_models())

    @app.post("/api/models/import")
    async def import_model(payload: dict[str, Any]) -> ApiEnvelope:
        return envelope(await policy.import_model(payload))

    @app.post("/api/models/{model_id}/start")
    async def start_model(model_id: str) -> ApiEnvelope:
        try:
            return envelope(await policy.start_model(model_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "MODEL_NOT_FOUND", "message": model_id}) from exc

    @app.post("/api/models/{model_id}/stop")
    async def stop_model(model_id: str) -> ApiEnvelope:
        return envelope(await policy.stop_model(model_id))

    @app.get("/api/auto/status")
    async def auto_status() -> ApiEnvelope:
        return envelope(policy.auto_status())

    @app.post("/api/auto/start")
    async def auto_start(payload: dict[str, Any] | None = None) -> ApiEnvelope:
        try:
            return envelope(await policy.auto_start(payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "MODEL_NOT_FOUND", "message": str(exc)}) from exc

    @app.post("/api/auto/stop")
    async def auto_stop() -> ApiEnvelope:
        return envelope(await policy.auto_stop())

    @app.post("/api/auto/action")
    async def auto_action(payload: dict[str, Any]) -> ApiEnvelope:
        try:
            return envelope(await policy.queue_action(payload))
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail={"code": "ACTION_REJECTED", "message": str(exc)}) from exc

    @app.post("/api/auto/dispatch_next")
    async def auto_dispatch_next() -> ApiEnvelope:
        try:
            return envelope(await policy.dispatch_next())
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "HAL_DISPATCH_FAILED", "message": str(exc)}) from exc

    @app.get("/api/fine_tune/jobs")
    async def list_fine_tune_jobs() -> ApiEnvelope:
        return envelope(policy.list_fine_tune_jobs())

    @app.post("/api/fine_tune/jobs")
    async def start_fine_tune_job(payload: dict[str, Any]) -> ApiEnvelope:
        return envelope(await policy.start_fine_tune(payload))

    @app.post("/api/fine_tune/jobs/{job_id}/cancel")
    async def cancel_fine_tune_job(job_id: str) -> ApiEnvelope:
        try:
            return envelope(await policy.cancel_fine_tune(job_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": job_id}) from exc

    @app.post("/api/motion/emergency_stop")
    async def emergency_stop() -> ApiEnvelope:
        return envelope(await commands.emergency_stop())

    @app.post("/api/motion/home_all")
    async def home_all() -> ApiEnvelope:
        try:
            return envelope(await commands.home_all())
        except RuntimeError as exc:
            logs.error("[HAL]", f"home_all failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    @app.get("/api/motion/origin")
    async def motion_origin_status() -> ApiEnvelope:
        return envelope(commands.motion_origin_status())

    @app.post("/api/motion/origin/capture")
    async def capture_motion_origin_all() -> ApiEnvelope:
        try:
            return envelope(await commands.capture_motion_origin())
        except RuntimeError as exc:
            logs.error("[HAL]", f"capture_motion_origin failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    @app.post("/api/motion/origin/clear")
    async def clear_motion_origin_all() -> ApiEnvelope:
        return envelope(commands.clear_motion_origin())

    @app.post("/api/motion/{side}/origin/capture")
    async def capture_motion_origin_side(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        try:
            return envelope(await commands.capture_motion_origin(side))
        except RuntimeError as exc:
            logs.error("[HAL]", f"capture_motion_origin failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    @app.post("/api/motion/{side}/origin/clear")
    async def clear_motion_origin_side(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        return envelope(commands.clear_motion_origin(side))

    @app.post("/api/motion/{side}/enable_all")
    async def enable_motion_side(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        try:
            return envelope(await commands.enable_motion_side(side))
        except RuntimeError as exc:
            logs.error("[HAL]", f"enable_motion_side failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    @app.post("/api/motion/{side}/disable_all")
    async def disable_motion_side(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        try:
            return envelope(await commands.disable_motion_side(side))
        except RuntimeError as exc:
            logs.error("[HAL]", f"disable_motion_side failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    @app.post("/api/motion/{side}/stop")
    async def stop_motion_side(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        try:
            return envelope(await commands.stop_motion_side(side))
        except RuntimeError as exc:
            logs.error("[HAL]", f"stop_motion_side failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    @app.post("/api/motion/{side}/home")
    async def home_motion_side(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        try:
            return envelope(await commands.home_motion_side(side))
        except RuntimeError as exc:
            logs.error("[HAL]", f"home_motion_side failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    @app.post("/api/motion/manual_axis_move")
    async def manual_axis_move(request: ManualAxisMoveRequest) -> ApiEnvelope:
        try:
            return envelope(await commands.manual_axis_move(request))
        except RuntimeError as exc:
            logs.error("[HAL]", f"manual_axis_move failed: {exc}")
            raise HTTPException(status_code=503, detail={"code": "MOTION_UNAVAILABLE", "message": str(exc)}) from exc

    @app.post("/api/motion/safety/acknowledge")
    async def acknowledge_safety() -> ApiEnvelope:
        return envelope(await commands.acknowledge_safety())

    @app.post("/api/sensors/tare")
    async def tare_sensors() -> ApiEnvelope:
        try:
            return envelope(await commands.tare_force())
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "FORCE_UNAVAILABLE", "message": str(exc)}) from exc

    @app.post("/api/force/{side}/tare")
    async def tare_force(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        try:
            return envelope(await commands.tare_force(side))
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "FORCE_UNAVAILABLE", "message": str(exc)}) from exc

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

    @app.get("/api/gripper/{side}/position")
    async def gripper_position(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        config = settings.get_config()
        if gripper_workers.is_enabled(config):
            result = await asyncio.to_thread(gripper_workers.position, config, side)
        else:
            result = await asyncio.to_thread(hardware.gripper.position, config, side)
        if not result.ok:
            raise HTTPException(status_code=503, detail={"code": "GRIPPER_UNAVAILABLE", "message": result.message})
        return envelope(result.__dict__)

    @app.post("/api/gripper/{side}/diagnose")
    async def gripper_diagnose(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        config = settings.get_config()
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
        result = await asyncio.to_thread(hardware.gripper.diagnose, config, side)
        logs.info("[GRIPPER]" if result.ok else "[GRIPPER]", result.message)
        return envelope(result.__dict__)

    @app.post("/api/teleop/gripper/start")
    async def gripper_tele_start() -> ApiEnvelope:
        gripper_tele.start()
        return envelope(gripper_tele.get_status())

    @app.post("/api/teleop/gripper/stop")
    async def gripper_tele_stop() -> ApiEnvelope:
        gripper_tele.stop()
        return envelope(gripper_tele.get_status())

    @app.get("/api/teleop/gripper/status")
    async def gripper_tele_status() -> ApiEnvelope:
        return envelope(gripper_tele.get_status())

    @app.post("/api/teleop/clutch_toggle")
    async def clutch_toggle() -> ApiEnvelope:
        logs.info("[HAL]", "teleop clutch toggle requested")
        return envelope({"clutchActive": True})

    @app.post("/api/teleop/speed")
    async def teleop_speed(payload: dict[str, Any]) -> ApiEnvelope:
        mode = payload.get("mode", "unknown")
        logs.info("[HAL]", f"teleop speed mode={mode}")
        return envelope({"mode": mode})

    @app.post("/api/teleop/{side}/connect")
    async def teleop_connect(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        hal_health = await hal.health()
        connected = True
        physical_connected = True
        message = ""
        if hal_health.mode == "real":
            connected = False
            physical_connected = False
            if hal_health.omega7_ok:
                try:
                    # 连接按钮只建立“逻辑连接”；真实设备仍由 HAL 持有并持续采样。
                    omega_state = await hal.omega_state()
                    hand = next(
                        (
                            item
                            for item in omega_state.get("hands", [])
                            if isinstance(item, dict) and item.get("side") == side
                        ),
                        {},
                    )
                    physical_connected = bool(hand.get("connected", False))
                    connected = physical_connected and bool(hand.get("lastReadOk", False))
                    message = str(hand.get("message", ""))
                except RuntimeError as exc:
                    message = str(exc)
            else:
                message = hal_health.message or "Omega.7 not ready"
        set_teleop_logical_connection(side, connected)
        if connected:
            # 只在设备读数可信时启动映射，避免断连手柄触发运动命令。
            await teleop_mapper.start("teleop-connect")
        logs.info(
            "[HAL]",
            f"{side} Omega.7 logical connect requested; connected={connected}, physical={physical_connected}",
        )
        return envelope(
            {
                "side": side,
                "connected": connected,
                "physicalConnected": physical_connected,
                "omega7Ok": hal_health.omega7_ok,
                "message": message,
            }
        )

    @app.post("/api/teleop/{side}/disconnect")
    async def teleop_disconnect(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        config = set_teleop_logical_connection(side, False)
        teleop_config = config.get("teleop", {})
        if not bool(teleop_config.get("leftConnected", False)) and not bool(teleop_config.get("rightConnected", False)):
            await teleop_mapper.stop("teleop-connect")
        logs.info("[HAL]", f"{side} Omega.7 logical disconnect requested; HAL device handles remain open")
        return envelope({"side": side, "connected": False})

    @app.get("/api/teleop/state")
    async def teleop_state() -> ApiEnvelope:
        try:
            omega_state = await hal.omega_state()
            return envelope(mask_omega_state_for_logical_connection(omega_state, settings.get_config()))
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "OMEGA_UNAVAILABLE", "message": str(exc)}) from exc

    @app.post("/api/teleop/{side}/gravity_compensation")
    async def teleop_gravity_compensation(side: str, payload: dict[str, Any] | None = None) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        enabled = bool((payload or {}).get("enabled", True))
        logs.info("[HAL]", f"{side} Omega.7 gravity compensation={enabled}")
        return envelope({"side": side, "enabled": enabled})

    @app.post("/api/teleop/{side}/zero_force_feedback")
    async def teleop_zero_force_feedback(side: str) -> ApiEnvelope:
        if side not in {"left", "right"}:
            raise HTTPException(status_code=400, detail={"code": "BAD_SIDE", "message": "side must be left or right"})
        logs.info("[HAL]", f"{side} Omega.7 zero force feedback requested")
        return envelope({"side": side})

    @app.post("/api/cameras/{camera}/enumerate")
    async def camera_enumerate(camera: str) -> ApiEnvelope:
        probe = await asyncio.to_thread(hardware.cameras.probe, settings.get_config())
        logs.info("[CAMERA]", f"{camera} enumerate requested: {probe.message}")
        return envelope({"camera": camera, "ok": probe.ok, "message": probe.message})

    @app.post("/api/cameras/{camera}/reconnect")
    async def camera_reconnect(camera: str) -> ApiEnvelope:
        probe = await asyncio.to_thread(hardware.cameras.reconnect, settings.get_config(), camera)
        logs.info("[CAMERA]", f"{camera} reconnect requested: {probe.message}")
        return envelope({"camera": camera, "ok": probe.ok, "message": probe.message})

    @app.post("/api/cameras/{camera}/tuning/apply")
    async def camera_tuning_apply(camera: str, config: AppConfig | None = None) -> ApiEnvelope:
        if camera not in {"global", "wrist_left", "wrist_right"}:
            raise HTTPException(
                status_code=400,
                detail={"code": "BAD_CAMERA", "message": "camera must be global, wrist_left, or wrist_right"},
            )
        active = (
            settings.save_config(config.model_dump(mode="json"), emit_log=False)
            if config is not None
            else settings.get_config()
        )
        try:
            result = await asyncio.to_thread(hardware.cameras.apply_tuning, active, camera)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "CAMERA_UNAVAILABLE", "message": str(exc)}) from exc
        logs.info("[CAMERA]", f"{camera} tuning applied: {result['profile']}")
        return envelope(result)

    @app.get("/api/cameras/{camera}/snapshot")
    async def camera_snapshot(camera: str) -> Response:
        if camera not in {"global", "wrist_left", "wrist_right"}:
            raise HTTPException(
                status_code=400,
                detail={"code": "BAD_CAMERA", "message": "camera must be global, wrist_left, or wrist_right"},
            )
        try:
            jpeg = await asyncio.to_thread(hardware.cameras.snapshot, settings.get_config(), camera)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"code": "CAMERA_UNAVAILABLE", "message": str(exc)}) from exc
        return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/cameras/{camera}/stream")
    async def camera_stream(camera: str) -> StreamingResponse:
        if camera not in {"global", "wrist_left", "wrist_right"}:
            raise HTTPException(
                status_code=400,
                detail={"code": "BAD_CAMERA", "message": "camera must be global, wrist_left, or wrist_right"},
            )

        async def frames():
            period = 1.0 / 30.0
            last_sequence = -1
            while True:
                started = time.monotonic()
                try:
                    last_sequence, jpeg = await asyncio.to_thread(
                        hardware.cameras.wait_for_frame,
                        settings.get_config(),
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

    @app.get("/api/cameras/enumerate")
    async def camera_enumerate_all() -> ApiEnvelope:
        devices = await asyncio.to_thread(hardware.cameras.enumerate_devices, settings.get_config())
        return envelope({"devices": devices})

    @app.post("/api/pico/adb/connect")
    async def pico_adb_connect() -> ApiEnvelope:
        result = hardware.pico.connect(settings.get_config())
        logs.info("[CAMERA]", result.message)
        return envelope(result.__dict__)

    @app.post("/api/pico/vision/start")
    async def pico_vision_start() -> ApiEnvelope:
        result = hardware.pico.start_vision(settings.get_config())
        logs.info("[CAMERA]", result.message)
        return envelope(result.__dict__)

    @app.post("/api/pico/vision/stop")
    async def pico_vision_stop() -> ApiEnvelope:
        result = hardware.pico.stop_vision(settings.get_config())
        logs.info("[CAMERA]", result.message)
        return envelope(result.__dict__)

    @app.post("/api/pico/status/check")
    async def pico_status_check() -> ApiEnvelope:
        result = hardware.pico.status(settings.get_config())
        logs.info("[CAMERA]", result.message)
        return envelope(result.__dict__)

    @app.post("/api/record/session/create")
    async def create_record_session(payload: dict[str, Any]) -> ApiEnvelope:
        dataset_name = payload.get("dataset_name", "dataset")
        task = payload.get("task", "")
        try:
            return envelope(await recorder.start_session(str(dataset_name), str(task)))
        except RuntimeError as exc:
            if "native LeRobot dataset is required" in str(exc):
                raise HTTPException(
                    status_code=503,
                    detail={"code": "NATIVE_DATASET_UNAVAILABLE", "message": str(exc)},
                ) from exc
            raise HTTPException(status_code=409, detail={"code": "RECORDING_BUSY", "message": str(exc)}) from exc

    @app.post("/api/record/episode/save")
    async def save_episode() -> ApiEnvelope:
        try:
            return envelope(await recorder.save_episode())
        except DatasetSaveError as exc:
            raise HTTPException(status_code=500, detail={"code": "RECORDING_SAVE_FAILED", "message": str(exc)}) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail={"code": "RECORDING_NOT_ACTIVE", "message": str(exc)}) from exc

    @app.post("/api/record/episode/discard")
    async def discard_episode() -> ApiEnvelope:
        try:
            return envelope(await recorder.discard_episode())
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail={"code": "RECORDING_NOT_ACTIVE", "message": str(exc)}) from exc

    @app.post("/api/record/session/finish")
    async def finish_session() -> ApiEnvelope:
        return envelope(await recorder.finish_session())

    @app.post("/api/record/reset/skip")
    async def skip_reset() -> ApiEnvelope:
        try:
            return envelope(await recorder.skip_reset())
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail={"code": "RECORDING_NOT_ACTIVE", "message": str(exc)}) from exc

    @app.get("/api/record/status")
    async def record_status() -> ApiEnvelope:
        return envelope(recorder.status())

    @app.get("/api/datasets")
    async def list_datasets() -> ApiEnvelope:
        return envelope({"datasets": recorder.list_datasets()})

    @app.post("/api/datasets")
    async def create_dataset(payload: dict[str, Any]) -> ApiEnvelope:
        try:
            return envelope(recorder.create_dataset(payload))
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "NATIVE_DATASET_UNAVAILABLE", "message": str(exc)},
            ) from exc

    @app.patch("/api/datasets/{dataset_id}")
    async def update_dataset(dataset_id: str, payload: dict[str, Any]) -> ApiEnvelope:
        try:
            return envelope(recorder.update_dataset(dataset_id, payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc

    @app.delete("/api/datasets/{dataset_id}")
    async def delete_dataset(dataset_id: str) -> ApiEnvelope:
        try:
            return envelope(recorder.delete_dataset(dataset_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "DATASET_DELETE_REFUSED", "message": str(exc)},
            ) from exc

    @app.post("/api/datasets/{dataset_id}/review/save")
    async def save_dataset_review(dataset_id: str) -> ApiEnvelope:
        try:
            return envelope(recorder.save_review(dataset_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc

    @app.post("/api/datasets/{dataset_id}/export")
    async def export_dataset(dataset_id: str) -> ApiEnvelope:
        try:
            return envelope(recorder.export_dataset(dataset_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc

    @app.get("/api/datasets/{dataset_id}/stats")
    async def dataset_stats(dataset_id: str) -> ApiEnvelope:
        try:
            return envelope(recorder.dataset_stats(dataset_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc

    @app.post("/api/datasets/{dataset_id}/split")
    async def split_dataset(dataset_id: str, payload: dict[str, Any]) -> ApiEnvelope:
        try:
            return envelope(recorder.split_dataset(dataset_id, payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc

    @app.post("/api/datasets/{dataset_id}/clean")
    async def clean_dataset(dataset_id: str, payload: dict[str, Any]) -> ApiEnvelope:
        try:
            return envelope(recorder.clean_dataset(dataset_id, payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc

    @app.post("/api/datasets/{dataset_id}/push")
    async def push_dataset(dataset_id: str, payload: dict[str, Any]) -> ApiEnvelope:
        try:
            return envelope(recorder.push_dataset(dataset_id, payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "DATASET_NOT_FOUND", "message": dataset_id}) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail={"code": "DATASET_PUSH_FAILED", "message": str(exc)}) from exc

    @app.get("/api/datasets/{dataset_id}/episodes/{episode_id}")
    async def dataset_episode_detail(dataset_id: str, episode_id: str) -> ApiEnvelope:
        """返回单条 episode 复核详情，保持 ok/data/ts envelope 契约。"""
        try:
            return envelope(recorder.episode_detail(dataset_id, episode_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "EPISODE_NOT_FOUND", "message": episode_id}) from exc

    @app.patch("/api/datasets/{dataset_id}/episodes/{episode_id}")
    async def update_dataset_episode(dataset_id: str, episode_id: str, payload: dict[str, Any]) -> ApiEnvelope:
        try:
            return envelope(recorder.update_episode(dataset_id, episode_id, payload))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "EPISODE_NOT_FOUND", "message": episode_id}) from exc

    @app.delete("/api/datasets/{dataset_id}/episodes/{episode_id}")
    async def delete_dataset_episode(dataset_id: str, episode_id: str) -> ApiEnvelope:
        try:
            return envelope(recorder.delete_episode(dataset_id, episode_id))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "EPISODE_NOT_FOUND", "message": episode_id}) from exc

    @app.get("/api/datasets/{dataset_id}/file")
    async def dataset_file(dataset_id: str, path: str) -> FileResponse:
        try:
            target = recorder.resolve_file(dataset_id, path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "FILE_NOT_FOUND", "message": path}) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail={"code": "BAD_FILE_PATH", "message": str(exc)}) from exc
        return FileResponse(target)

    @app.get("/api/datasets/{dataset_id}/frame_image")
    async def dataset_frame_image(dataset_id: str, episode_id: str, camera: str, frame: int) -> Response:
        try:
            jpeg = recorder.resolve_frame_image(dataset_id, episode_id, camera, frame)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "FRAME_IMAGE_NOT_FOUND", "message": f"{episode_id}:{camera}:{frame}"},
            ) from exc
        return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

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
                                settings.get_config(),
                                motion_positions,
                                motion_pulses,
                            )
                        if isinstance(motion_state.get("estop_active"), bool):
                            motion_estop_active = bool(motion_state["estop_active"])
                        raw_enabled = motion_state.get("enabled")
                        if isinstance(raw_enabled, list) and len(raw_enabled) == 12:
                            left_axis_enabled = [bool(value) for value in raw_enabled[:6]]
                            right_axis_enabled: list[bool | None] = [bool(value) for value in raw_enabled[6:12]]
                            if any(value is True for index, value in enumerate(right_axis_enabled) if index != 3):
                                right_axis_enabled[3] = True if right_axis_enabled[3] is True else None
                            motion_axis_enabled = {
                                "left": left_axis_enabled,
                                "right": right_axis_enabled,
                            }
                            motion_enabled = {
                                "left": all(motion_axis_enabled["left"]),
                                "right": all(motion_axis_enabled["right"]),
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
                            except RuntimeError as exc:
                                cached_omega_hands = None
                                logs.error("[HAL]", f"omega state failed: {exc}")
                        omega_hands = cached_omega_hands
                    hal_ok = hal_health.connected and (hal_health.mode != "real" or hal_health.ltdmc_ok)
                    frame = telemetry.next_frame(
                        motion_positions,
                        motion_estop_active=motion_estop_active,
                        motion_enabled=motion_enabled,
                        motion_axis_enabled=motion_axis_enabled,
                        omega_hands=omega_hands,
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


app = create_app()
