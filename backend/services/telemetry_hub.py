from __future__ import annotations

import math
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from importlib import import_module
from typing import Any, Literal

from backend.core.config import SettingsService
from backend.core.schemas import CameraTelemetry, Omega7Telemetry, ProcessStatus, TelemetryFrame
from backend.services.hardware_service import HardwareService


class TelemetryHub:
    def __init__(
        self,
        settings: SettingsService,
        hardware: HardwareService | None = None,
        gripper_workers: Any | None = None,
    ) -> None:
        self.settings = settings
        self.hardware = hardware
        self.gripper_workers = gripper_workers
        self.started = time.monotonic()
        self.tick = 0
        self.axis_offsets = [0.0] * 12
        self.motion_positions = [0.0] * 12
        self.motion_enabled: dict[Literal["left", "right"], bool | None] = {"left": None, "right": None}
        self.motion_axis_enabled: dict[Literal["left", "right"], list[bool | None]] = {
            "left": [None] * 6,
            "right": [None] * 6,
        }
        self.gripper_positions = [-1.0, -1.0]
        self.estop_active = False
        self.force_tare_active = False
        self.force_left = [0.0] * 6
        self.force_right = [0.0] * 6
        self.force_ok = False
        self.gripper_samples: dict[str, dict[str, Any]] = {}
        self.recording = False
        self.episode_count = 0
        self.frame_count = 0
        self._last_gripper_sample_at = 0.0
        # Both grippers share the Jodell DLL's single active COM port, so reading
        # left then right back-to-back forces an open/close cycle (~140 ms each).
        # Alternating sides per poll keeps each tick to one port-switch worth of
        # work and stops user commands from contending with a 280ms sample.
        self._next_gripper_side = "left"
        self._last_force_sample_at = 0.0
        self._last_camera_sample_at = 0.0
        self._last_frame_at = 0.0
        self._ws_hz = 0.0
        self._hardware_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="telemetry-hardware")
        # 硬件采样放在线程池中，避免慢速 IO 阻塞 WebSocket 发送节奏。
        self._force_future: Future[Any] | None = None
        self._gripper_future: Future[Any] | None = None
        self._camera_future: Future[Any] | None = None
        self._cached_cameras: list[CameraTelemetry] = self._offline_cameras("checking")

    def next_frame(
        self,
        motion_positions: list[float] | None = None,
        *,
        motion_estop_active: bool | None = None,
        motion_enabled: dict[str, bool | None] | None = None,
        motion_axis_enabled: dict[str, list[bool | None]] | None = None,
        omega_hands: list[dict[str, Any]] | None = None,
        hal_ok: bool = True,
    ) -> TelemetryFrame:
        # 每一帧都合并 HAL、硬件轮询和测试夹具数据，给前端提供单一遥测快照。
        self.tick += 1
        now = time.monotonic()
        elapsed = now - self.started
        if self._last_frame_at > 0:
            interval = max(now - self._last_frame_at, 0.001)
            instant_hz = 1.0 / interval
            self._ws_hz = instant_hz if self._ws_hz <= 0 else self._ws_hz * 0.8 + instant_hz * 0.2
        self._last_frame_at = now
        config = self.settings.get_config()
        real_mode = self._real_hardware_mode(config)
        if real_mode:
            if motion_positions is not None and len(motion_positions) == 12:
                # 真机位置以 HAL 返回值为准，本地 offset 只服务于测试模式。
                self.motion_positions = [float(value) for value in motion_positions]
            if motion_enabled is not None:
                self.motion_enabled = {
                    "left": motion_enabled.get("left"),
                    "right": motion_enabled.get("right"),
                }
            if motion_axis_enabled is not None:
                self.motion_axis_enabled = {
                    "left": list(motion_axis_enabled.get("left", [None] * 6))[:6],
                    "right": list(motion_axis_enabled.get("right", [None] * 6))[:6],
                }
            joint_positions = list(self.motion_positions)
        else:
            joint_positions = [
                math.sin(elapsed * (0.22 + idx * 0.015) + idx * 0.74) * (520 if idx % 6 < 3 else 85)
                + self.axis_offsets[idx]
                for idx in range(12)
            ]
        if real_mode and motion_estop_active is not None:
            self.estop_active = motion_estop_active
        force_left = self._force_values(elapsed, left=True)
        force_right = self._force_values(elapsed, left=False)
        force_ok = True
        if self.hardware is not None and real_mode:
            # 真机传感器用缓存值出帧，采样频率由后台 future 控制。
            self._refresh_force_values(config, now)
            self.refresh_gripper_positions(config, now)
            force_ok = self.force_ok
            force_left = list(self.force_left)
            force_right = list(self.force_right)
        if real_mode:
            danger = 1.1 if self.estop_active else 0.0
        else:
            danger = 1.1 if self.estop_active else self._danger_index(force_left, force_right, config)
        if self.recording:
            self.frame_count += 1
        return TelemetryFrame(
            timestamp=int(time.time() * 1000),
            elapsedSec=elapsed,
            jointPositions=joint_positions,
            gripperPositions=list(self.gripper_positions),
            motionEnabled=dict(self.motion_enabled),
            motionAxisEnabled={
                "left": list(self.motion_axis_enabled["left"]),
                "right": list(self.motion_axis_enabled["right"]),
            },
            forceLeft=force_left,
            forceRight=force_right,
            dangerIndex=danger,
            recording=self.recording,
            episodeCount=self.episode_count,
            frameCount=self.frame_count,
            halOk=hal_ok and force_ok,
            wsOk=True,
            cameras=self._cameras(elapsed, config),
            teleopHands=self._teleop_hands(elapsed, config, omega_hands, real_mode),
            queueDepth=self._queue_depth(elapsed, real_mode),
            resource=self._resource_status(elapsed, real_mode),
            processStatus=self._process_status(elapsed, real_mode),
        )

    def apply_axis_move(self, side: str, axis: str, direction: int, step: float) -> float:
        axis_order = ["X", "Y", "Z", "Roll", "Pitch", "Yaw"]
        axis_idx = axis_order.index(axis)
        state_idx = (0 if side == "left" else 6) + axis_idx
        config = self.settings.get_config()
        limit_key = axis.lower()
        limits_key = "leftSoftLimits" if side == "left" else "rightSoftLimits"
        limits = config["motion"][limits_key][limit_key]
        next_offset = self.axis_offsets[state_idx] + step * direction
        # 测试模式也遵守软限位，确保演示界面不会展示不可达位置。
        next_offset = max(float(limits["min"]), min(float(limits["max"]), next_offset))
        applied = next_offset - self.axis_offsets[state_idx]
        self.axis_offsets[state_idx] = next_offset
        return applied

    def apply_gripper(self, side: str, command: str, target_mm: float | None) -> float:
        config = self.settings.get_config()
        idx = 0 if side == "left" else 1
        stroke = float(config["gripper"]["strokeMm"])
        if command == "open":
            target = stroke
        elif command in {"close", "home"}:
            target = 0.0
        elif command == "target":
            fallback = self.gripper_positions[idx] if self.gripper_positions[idx] >= 0 else 0.0
            target = float(target_mm if target_mm is not None else fallback)
        else:
            target = self.gripper_positions[idx] if self.gripper_positions[idx] >= 0 else 0.0
        target = max(0.0, min(stroke, target))
        self.gripper_positions[idx] = target
        return target

    def home_all(self) -> None:
        self.axis_offsets = [0.0] * 12
        self.estop_active = False

    def home_side(self, side: str) -> None:
        start = 0 if side == "left" else 6
        for idx in range(start, start + 6):
            self.axis_offsets[idx] = 0.0
        self.estop_active = False

    def set_motion_enabled(self, side: str, enabled: bool | None) -> None:
        if side in self.motion_enabled:
            self.motion_enabled[side] = enabled
            self.motion_axis_enabled[side] = [enabled] * 6

    def set_motion_axis_enabled(self, side: str, values: list[bool | None]) -> None:
        if side not in self.motion_enabled:
            return
        normalized = list(values[:6])
        while len(normalized) < 6:
            normalized.append(None)
        self.motion_axis_enabled[side] = normalized
        self.motion_enabled[side] = all(value is True for value in normalized)

    def emergency_stop(self) -> None:
        self.recording = False
        self.estop_active = True

    def acknowledge_safety(self) -> None:
        self.estop_active = False

    def tare_force(self) -> None:
        self.force_tare_active = True

    def _force_values(self, elapsed: float, *, left: bool) -> list[float]:
        if self.force_tare_active:
            return [
                math.sin(elapsed * 0.9) * 0.035,
                math.cos(elapsed * 0.8) * 0.028,
                math.sin(elapsed * 0.7) * 0.045,
                math.sin(elapsed * 0.6) * 0.003,
                math.cos(elapsed * 0.55) * 0.003,
                math.sin(elapsed * 0.5) * 0.002,
            ]
        if left:
            return [
                math.sin(elapsed * 1.8) * 0.8,
                math.cos(elapsed * 1.3) * 0.5,
                1.4 + math.sin(elapsed * 0.9) * 0.45,
                math.sin(elapsed * 1.2) * 0.012,
                math.cos(elapsed * 1.1) * 0.01,
                math.sin(elapsed * 0.7) * 0.009,
            ]
        return [
            math.cos(elapsed * 1.1) * 0.6,
            math.sin(elapsed * 1.6) * 0.7,
            0.95 + math.cos(elapsed * 0.8) * 0.35,
            math.cos(elapsed * 0.9) * 0.011,
            math.sin(elapsed * 1.4) * 0.013,
            math.cos(elapsed * 0.6) * 0.007,
        ]

    def _danger_index(self, force_left: list[float], force_right: list[float], config: dict[str, Any]) -> float:
        safety = config["safety"]
        fxy_stop = float(safety["fxyStopN"])
        fz_stop = float(safety["fzStopN"])
        moment_stop = float(safety["momentStopNm"])
        values = force_left + force_right
        return min(
            1.16,
            max(
                abs(values[0]) / fxy_stop,
                abs(values[1]) / fxy_stop,
                abs(values[2]) / fz_stop,
                abs(values[3]) / moment_stop,
                abs(values[4]) / moment_stop,
                abs(values[5]) / moment_stop,
            )
            * 0.75,
        )

    def _cameras(self, elapsed: float, config: dict[str, Any]) -> list[CameraTelemetry]:
        if self.hardware is not None and self._real_hardware_mode(config):
            self._refresh_cameras(config, time.monotonic())
            return list(self._cached_cameras)
        return [
            CameraTelemetry(
                key="global",
                label="全局相机",
                fps=29.8 + math.sin(elapsed * 0.4) * 0.2,
                timestampSkewMs=3 + math.sin(elapsed) * 2,
                frameAgeMs=26 + math.sin(elapsed * 1.3) * 6,
                health="ok",
            ),
            CameraTelemetry(
                key="wrist_left",
                label="左腕相机",
                fps=29.6 + math.cos(elapsed * 0.5) * 0.25,
                timestampSkewMs=5 + math.cos(elapsed * 1.2) * 3,
                frameAgeMs=31 + math.cos(elapsed * 1.1) * 7,
                health="ok",
            ),
            CameraTelemetry(
                key="wrist_right",
                label="右腕相机",
                fps=29.7 + math.sin(elapsed * 0.7) * 0.2,
                timestampSkewMs=4 + math.sin(elapsed * 0.9) * 3,
                frameAgeMs=29 + math.sin(elapsed * 1.5) * 5,
                health="ok",
            ),
        ]

    def _teleop_hands(
        self,
        elapsed: float,
        config: dict[str, Any],
        omega_hands: list[dict[str, Any]] | None,
        real_mode: bool,
    ) -> list[Omega7Telemetry]:
        disconnected_message = "logical teleop hand disconnected"
        if omega_hands is not None:
            hands: list[Omega7Telemetry] = []
            sides: tuple[Literal["left", "right"], Literal["left", "right"]] = ("left", "right")
            for idx, side in enumerate(sides):
                raw = next(
                    (item for item in omega_hands if isinstance(item, dict) and item.get("side") == side),
                    omega_hands[idx] if idx < len(omega_hands) and isinstance(omega_hands[idx], dict) else {},
                )
                logical_connected = bool(config["teleop"].get(f"{side}Connected", False))
                pose = raw.get("pose") if isinstance(raw, dict) else None
                if not isinstance(pose, list) or len(pose) != 6:
                    pose = [0.0] * 6
                if not logical_connected:
                    pose = [0.0] * 6
                hands.append(
                    Omega7Telemetry(
                        side=side,
                        connected=logical_connected and bool(raw.get("connected", False)),
                        calibrated=logical_connected and bool(raw.get("calibrated", False)),
                        openId=int(raw.get("openId", config["teleop"][f"{side}OpenId"])),
                        deviceId=int(raw.get("deviceId", -1)),
                        serial=str(raw.get("serial", "")),
                        systemName=str(raw.get("systemName", "")),
                        leftHanded=raw.get("leftHanded") if isinstance(raw.get("leftHanded"), bool) else None,
                        pose=[float(value) for value in pose],
                        clutchPressed=logical_connected and bool(raw.get("clutchPressed", False)),
                        gripperPressed=logical_connected and bool(raw.get("gripperPressed", False)),
                        gripperGapMm=(
                            float(raw["gripperGapMm"])
                            if logical_connected and raw.get("gripperGapMm") is not None
                            else None
                        ),
                        lastReadOk=logical_connected and bool(raw.get("lastReadOk", False)),
                        message=str(raw.get("message", "")) if logical_connected else disconnected_message,
                    )
                )
            return hands
        if real_mode:
            return self._offline_teleop_hands(config, "waiting for HAL omega state")
        left_connected = bool(config["teleop"].get("leftConnected", False))
        right_connected = bool(config["teleop"].get("rightConnected", False))
        return [
            Omega7Telemetry(
                side="left",
                connected=left_connected,
                calibrated=False,
                openId=int(config["teleop"].get("leftOpenId", 0)),
                deviceId=0,
                serial="test-left",
                systemName="test Omega.7",
                leftHanded=True,
                pose=[
                    math.sin(elapsed * 0.6) * 0.025,
                    math.cos(elapsed * 0.5) * 0.018,
                    math.sin(elapsed * 0.45 + 0.8) * 0.022,
                    0.0,
                    0.0,
                    math.sin(elapsed * 0.4) * 4.5,
                ]
                if left_connected
                else [0.0] * 6,
                clutchPressed=left_connected and math.sin(elapsed * 0.8) > 0.4,
                gripperPressed=left_connected and math.cos(elapsed * 0.7) > 0.45,
                gripperGapMm=None,
                lastReadOk=left_connected,
                message="" if left_connected else disconnected_message,
            ),
            Omega7Telemetry(
                side="right",
                connected=right_connected,
                calibrated=False,
                openId=int(config["teleop"].get("rightOpenId", 1)),
                deviceId=1,
                serial="test-right",
                systemName="test Omega.7",
                leftHanded=False,
                pose=[
                    math.sin(elapsed * 0.6 + 1.0) * 0.025,
                    math.cos(elapsed * 0.5 + 1.0) * 0.018,
                    math.sin(elapsed * 0.45 + 1.8) * 0.022,
                    0.0,
                    0.0,
                    math.sin(elapsed * 0.4 + 1.0) * 4.5,
                ]
                if right_connected
                else [0.0] * 6,
                clutchPressed=right_connected and math.sin(elapsed * 0.8 + 1.0) > 0.4,
                gripperPressed=right_connected and math.cos(elapsed * 0.7 + 1.0) > 0.45,
                gripperGapMm=None,
                lastReadOk=right_connected,
                message="" if right_connected else disconnected_message,
            ),
        ]

    def _offline_teleop_hands(self, config: dict[str, Any], message: str) -> list[Omega7Telemetry]:
        return [
            Omega7Telemetry(
                side="left",
                connected=False,
                calibrated=False,
                openId=int(config["teleop"].get("leftOpenId", 0)),
                deviceId=-1,
                pose=[0.0] * 6,
                message=message,
            ),
            Omega7Telemetry(
                side="right",
                connected=False,
                calibrated=False,
                openId=int(config["teleop"].get("rightOpenId", 1)),
                deviceId=-1,
                pose=[0.0] * 6,
                message=message,
            ),
        ]

    def _queue_depth(self, elapsed: float, real_mode: bool) -> dict[Literal["left", "right"], int]:
        if real_mode:
            return {"left": 0, "right": 0}
        return {
            "left": max(0, min(100, round(21 + math.sin(elapsed * 1.1) * 12))),
            "right": max(0, min(100, round(20 + math.cos(elapsed * 1.05) * 12))),
        }

    def _resource_status(
        self,
        elapsed: float,
        real_mode: bool,
    ) -> dict[Literal["uiFps", "wsHz", "cpuPct", "memMb"], float]:
        if real_mode:
            cpu_pct, mem_mb = self._process_metrics()
            return {"uiFps": 0.0, "wsHz": round(self._ws_hz, 1), "cpuPct": cpu_pct, "memMb": mem_mb}
        return {
            "uiFps": 60,
            "wsHz": 50,
            "cpuPct": 22 + math.sin(elapsed * 0.6) * 8,
            "memMb": 520 + math.cos(elapsed * 0.25) * 30,
        }

    def _process_status(self, elapsed: float, real_mode: bool) -> list[ProcessStatus]:
        if real_mode:
            backend_cpu, backend_mem = self._process_metrics()
            return [
                ProcessStatus(
                    name="hal",
                    label="HalServer.exe",
                    status="running",
                    pid=None,
                    cpuPct=0,
                    memMb=0,
                    autoRestart=True,
                ),
                ProcessStatus(
                    name="backend",
                    label="FastAPI Backend",
                    status="running",
                    pid=os.getpid(),
                    cpuPct=backend_cpu,
                    memMb=backend_mem,
                    autoRestart=True,
                ),
                ProcessStatus(name="policy", label="PolicyServer", status="not_running", cpuPct=0, memMb=0),
                ProcessStatus(
                    name="recorder",
                    label="DataRecorder",
                    status="running" if self.recording else "not_running",
                    cpuPct=0,
                    memMb=0,
                ),
                ProcessStatus(name="wsl", label="WSL2 Bridge", status="degraded", cpuPct=0, memMb=0),
            ]
        return [
            ProcessStatus(
                name="hal",
                label="Test HAL boundary",
                status="running",
                pid=None,
                cpuPct=1.5 + math.sin(elapsed * 0.6) * 0.4,
                memMb=32,
                autoRestart=True,
            ),
            ProcessStatus(
                name="backend",
                label="FastAPI Backend",
                status="running",
                pid=None,
                cpuPct=8 + math.sin(elapsed * 0.8) * 2,
                memMb=180 + math.sin(elapsed * 0.3) * 8,
                autoRestart=True,
            ),
            ProcessStatus(name="policy", label="PolicyServer", status="not_running", cpuPct=0, memMb=0),
            ProcessStatus(
                name="recorder",
                label="DataRecorder",
                status="running" if self.recording else "not_running",
                cpuPct=0,
                memMb=0,
            ),
            ProcessStatus(name="wsl", label="WSL2 Bridge", status="degraded", cpuPct=0, memMb=0),
        ]

    def _real_hardware_mode(self, config: dict[str, Any]) -> bool:
        mode = os.environ.get("APPSTATION_HAL_MODE") or config["hal"].get("mode", "real")
        return str(mode).lower() == "real"

    def _process_metrics(self) -> tuple[float, float]:
        try:
            psutil = import_module("psutil")
            process = psutil.Process(os.getpid())
            cpu_pct = float(process.cpu_percent(interval=None))
            mem_mb = float(process.memory_info().rss) / (1024 * 1024)
            return round(cpu_pct, 1), round(mem_mb, 1)
        except Exception:
            return 0.0, 0.0

    def _refresh_force_values(self, config: dict[str, Any], now: float) -> None:
        if self.hardware is None:
            return
        if self._force_future is not None and self._force_future.done():
            try:
                result = self._force_future.result()
                self.force_ok = bool(result.ok)
                self.force_left = [float(value) for value in result.left]
                self.force_right = [float(value) for value in result.right]
            except Exception:
                self.force_ok = False
            finally:
                self._force_future = None
        if self._force_future is None and now - self._last_force_sample_at >= 0.25:
            # 力传感器刷新慢于 WS 帧率，减少 USB/DAQ 轮询压力。
            self._last_force_sample_at = now
            self._force_future = self._hardware_executor.submit(self.hardware.force.sample, config)

    def _refresh_cameras(self, config: dict[str, Any], now: float) -> None:
        if self.hardware is None:
            return
        if self._camera_future is not None and self._camera_future.done():
            try:
                result = self._camera_future.result()
                self._cached_cameras = list(result.cameras)
            except Exception:
                self._cached_cameras = self._offline_cameras("error")
            finally:
                self._camera_future = None
        if self._camera_future is None and now - self._last_camera_sample_at >= 1.0:
            # 摄像头枚举成本较高，只周期性刷新健康状态。
            self._last_camera_sample_at = now
            self._camera_future = self._hardware_executor.submit(self.hardware.cameras.probe, config)

    def refresh_gripper_positions(self, config: dict[str, Any], now: float | None = None) -> None:
        """从当前采样后端刷新夹爪缓存位置。"""
        now = time.monotonic() if now is None else now
        if self.hardware is None:
            return
        if self.gripper_workers is not None and self.gripper_workers.is_enabled(config):
            samples = self.gripper_workers.samples(config)
            if samples:
                self.gripper_samples = samples
            updated = False
            latest_sample_at = 0.0
            for side, idx in (("left", 0), ("right", 1)):
                sample = samples.get(side, {})
                value = sample.get("positionMm")
                if value is not None:
                    self.gripper_positions[idx] = float(value)
                    updated = True
                monotonic_ms = sample.get("monotonicMs")
                if monotonic_ms is not None:
                    latest_sample_at = max(latest_sample_at, float(monotonic_ms) / 1000.0)
            if updated:
                self._last_gripper_sample_at = latest_sample_at or now
            return
        if self._gripper_future is not None and self._gripper_future.done():
            try:
                positions = self._gripper_future.result()
                for idx, value in enumerate(positions):
                    if value is not None:
                        self.gripper_positions[idx] = float(value)
                        side = "left" if idx == 0 else "right"
                        # 直接读取路径也写入统一样本缓存，录制端才能判断夹爪数据是否过期。
                        self.gripper_samples[side] = {
                            "ok": True,
                            "positionMm": float(value),
                            "sampleHz": 1.0,
                            "ageMs": 0.0,
                            "tsMs": int(time.time() * 1000),
                            "monotonicMs": int(now * 1000),
                            "message": "direct gripper sample",
                        }
                        self._last_gripper_sample_at = now
            except Exception:
                pass
            finally:
                self._gripper_future = None
        if self._gripper_future is None and now - self._last_gripper_sample_at >= 1.0:
            # 夹爪位置变化较慢，低频采样足够支撑状态面板。
            self._last_gripper_sample_at = now
            self._gripper_future = self._hardware_executor.submit(self._sample_gripper_positions, config)

    def _refresh_gripper_positions(self, config: dict[str, Any], now: float) -> None:
        self.refresh_gripper_positions(config, now)

    def _sample_gripper_positions(self, config: dict[str, Any]) -> list[float | None]:
        if self.hardware is None:
            return [None, None]
        # Read only one side per cycle — the Jodell DLL keeps a single COM port
        # active and switching costs ~140ms, which would otherwise block user
        # gripper commands behind the periodic sampling.
        side = self._next_gripper_side
        result = self.hardware.gripper.position(config, side)
        positions: list[float | None] = [None, None]
        if result.ok:
            positions[0 if side == "left" else 1] = result.position_mm
        # Toggle for the next cycle so each gripper still gets a fresh reading
        # roughly every 2× the gripper sample period (default 1s -> 2s/side).
        self._next_gripper_side = "right" if side == "left" else "left"
        return positions

    def _offline_cameras(self, health: Literal["ok", "warn", "error", "checking", "pending"]) -> list[CameraTelemetry]:
        return [
            CameraTelemetry(
                key="global",
                label="Global Camera",
                fps=0,
                timestampSkewMs=0,
                frameAgeMs=9999,
                health=health,
            ),
            CameraTelemetry(
                key="wrist_left",
                label="Left Wrist Camera",
                fps=0,
                timestampSkewMs=0,
                frameAgeMs=9999,
                health=health,
            ),
            CameraTelemetry(
                key="wrist_right",
                label="Right Wrist Camera",
                fps=0,
                timestampSkewMs=0,
                frameAgeMs=9999,
                health=health,
            ),
        ]
