# 阅读导航 04｜后端业务与采集
# 职责：管理模型、自动执行状态和微调任务；具体能力与返回值需结合方法实现阅读。
# 先看：PolicyService。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from backend.core.config import SettingsService
from backend.core.logging import LogService, now_ms
from backend.core.motion_safety import MotionSafetyGate
from backend.hal_client.client import HalClient


class PolicyService:
    def __init__(self, settings: SettingsService, hal: HalClient, logs: LogService, safety: MotionSafetyGate | None = None) -> None:
        self.settings = settings
        self.hal = hal
        self.logs = logs
        self.safety = safety if safety is not None else MotionSafetyGate()
        self._lock = asyncio.Lock()
        self._models: dict[str, dict[str, Any]] = {
            "act": self._model("act", "ACT", "ready", "local baseline policy", 32),
            "diffusion_policy": self._model("diffusion_policy", "Diffusion Policy", "ready", "async policy", 108),
            "smolvla": self._model("smolvla", "SmolVLA / OpenVLA", "not_loaded", "VLA checkpoint pending", 146),
        }
        self._active_model_id = ""
        self._auto_running = False
        self._stop_generation = 0
        self._action_queue: list[dict[str, Any]] = []
        self._fine_tune_jobs: list[dict[str, Any]] = []
        self._last_dispatch: dict[str, Any] | None = None
        self.validate_hardware_action: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    def list_models(self) -> dict[str, Any]:
        return {"models": list(self._models.values()), "activeModelId": self._active_model_id}

    async def _get_config_async(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.settings.get_config)

    async def import_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or payload.get("modelId") or "local_checkpoint").strip()
        path = str(payload.get("path") or "").strip()
        model_id = self._safe_id(name)
        async with self._lock:
            self._models[model_id] = self._model(
                model_id,
                name,
                "ready" if path else "registered",
                f"checkpoint={path or 'not provided'}",
                float(payload.get("latencyMs", 0) or 0),
            )
        self.logs.info("[POLICY]", f"model registered: {model_id}")
        return self.list_models()

    async def start_model(self, model_id: str) -> dict[str, Any]:
        async with self._lock:
            if model_id not in self._models:
                raise FileNotFoundError(model_id)
            self._active_model_id = model_id
            self._models[model_id]["status"] = "running"
        self.logs.info("[POLICY]", f"model service active: {model_id}")
        return self.list_models()

    async def stop_model(self, model_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            target = model_id or self._active_model_id
            if target and target in self._models:
                self._models[target]["status"] = "ready"
            if not model_id or model_id == self._active_model_id:
                self._active_model_id = ""
        self.logs.warning("[POLICY]", "model service stopped")
        return self.list_models()

    def auto_status(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = config if config is not None else self.settings.get_config()
        auto_config = config.get("auto", {})
        dispatch_enabled = bool(auto_config.get("allowHardwareDispatch", False))
        return {
            "running": self._auto_running,
            "activeModelId": self._active_model_id,
            "queueDepth": len(self._action_queue),
            "dispatchEnabled": dispatch_enabled,
            "safetyCaps": self._safety_caps(config),
            "lastDispatch": self._last_dispatch,
            "queue": list(self._action_queue[-20:]),
        }

    async def auto_start(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        safety_token = self.safety.capture()
        self.safety.check(safety_token)
        generation = self._stop_generation
        request = payload or {}
        model_id = str(request.get("modelId") or self._active_model_id or "act")
        async with self._lock:
            if generation != self._stop_generation:
                raise RuntimeError("auto start cancelled by a newer stop")
            self.safety.check(safety_token)
            if model_id not in self._models:
                raise FileNotFoundError(model_id)
            self._active_model_id = model_id
            self._models[model_id]["status"] = "running"
            self._auto_running = True
        self.logs.info("[POLICY]", f"auto execution started with model={model_id}")
        return self.auto_status(await self._get_config_async())

    def invalidate_pending_actions(self) -> None:
        # 在任何 await / 清理之前使已取出和仍在准备的动作失效。
        self._stop_generation += 1
        self._auto_running = False
        self._action_queue.clear()

    async def auto_stop(self) -> dict[str, Any]:
        self.invalidate_pending_actions()
        self.logs.warning("[POLICY]", "auto execution stopped; action queue cleared")
        return self.auto_status(await self._get_config_async())

    async def queue_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        safety_token = self.safety.capture()
        self.safety.check(safety_token)
        generation = self._stop_generation
        config = await self._get_config_async()
        action = self._validated_action(payload, config)
        async with self._lock:
            if generation != self._stop_generation:
                raise RuntimeError("action cancelled by a newer stop")
            self.safety.check(safety_token)
            self._action_queue.append(action)
            self._action_queue = self._action_queue[-200:]
        self.logs.info("[POLICY]", f"action queued: {action['id']}")
        return {"action": action, "status": self.auto_status(config)}

    async def dispatch_next(self) -> dict[str, Any]:
        safety_token = self.safety.capture()
        generation = self._stop_generation
        async with self._lock:
            auto_running = self._auto_running
            if not auto_running:
                action = None
            else:
                action = self._action_queue.pop(0) if self._action_queue else None
        if not auto_running:
            config = await self._get_config_async()
            return {
                "dispatched": False,
                "reason": "auto execution is not running",
                "status": self.auto_status(config),
            }
        if action is None:
            config = await self._get_config_async()
            return {"dispatched": False, "reason": "action queue is empty", "status": self.auto_status(config)}
        config = await self._get_config_async()
        if generation != self._stop_generation or not self._auto_running:
            return {"dispatched": False, "reason": "action cancelled by a newer stop", "status": self.auto_status(config)}
        dispatch_enabled = bool(config.get("auto", {}).get("allowHardwareDispatch", False))
        if not dispatch_enabled:
            self._last_dispatch = {"action": action, "mode": "dry-run", "ts": now_ms()}
            return {
                "dispatched": False,
                "reason": "hardware dispatch disabled",
                "action": action,
                "status": self.auto_status(config),
            }
        self.safety.check(safety_token)
        try:
            result = await self._dispatch_action_to_hal(action)
        except (RuntimeError, asyncio.CancelledError) as exc:
            # 派发边界会记录是否进入 HAL；不能把已发布但无应答标成未执行。
            if self._last_dispatch and self._last_dispatch.get("action") is action:
                self._last_dispatch.update(outcome="unknown", error=str(exc))
            raise
        if generation != self._stop_generation:
            self._last_dispatch = {"action": action, "mode": "hal", "result": result, "outcome": "interrupted", "ts": now_ms()}
            return {"dispatched": True, "outcome": "interrupted", "action": action, "hal": result,
                    "reason": "dispatch interrupted by a newer stop; motion may have occurred", "status": self.auto_status(config)}
        self._last_dispatch = {"action": action, "mode": "hal", "result": result, "ts": now_ms()}
        return {"dispatched": True, "action": action, "hal": result, "status": self.auto_status(config)}

    def list_fine_tune_jobs(self) -> dict[str, Any]:
        return {"jobs": list(self._fine_tune_jobs)}

    async def start_fine_tune(self, payload: dict[str, Any]) -> dict[str, Any]:
        dataset_id = str(payload.get("datasetId") or "").strip()
        base_model = str(payload.get("baseModel") or self._active_model_id or "act")
        output_dir = str(payload.get("outputDir") or Path("runs") / "fine_tune" / f"job-{now_ms()}")
        job = {
            "id": f"ft-{now_ms()}",
            "datasetId": dataset_id,
            "baseModel": base_model,
            "outputDir": output_dir,
            "status": "planned",
            "createdAt": now_ms(),
            "message": "Windows local fine-tune plan created; execution hook is intentionally manual",
        }
        async with self._lock:
            self._fine_tune_jobs.insert(0, job)
            self._fine_tune_jobs = self._fine_tune_jobs[:50]
        self.logs.info("[POLICY]", f"fine-tune job planned: {job['id']}")
        return {"job": job, "jobs": list(self._fine_tune_jobs)}

    async def cancel_fine_tune(self, job_id: str) -> dict[str, Any]:
        async with self._lock:
            for job in self._fine_tune_jobs:
                if job["id"] == job_id:
                    job["status"] = "cancelled"
                    job["updatedAt"] = now_ms()
                    break
            else:
                raise FileNotFoundError(job_id)
        self.logs.warning("[POLICY]", f"fine-tune job cancelled: {job_id}")
        return self.list_fine_tune_jobs()

    async def _dispatch_action_to_hal(self, action: dict[str, Any]) -> dict[str, Any]:
        if action["type"] != "manual_axis_move":
            raise RuntimeError(f"unsupported hardware action type: {action['type']}")
        payload = {
            "side": action["side"],
            "axis": action["axis"],
            "direction": action["direction"],
            "step": action["step"],
            "speedMode": action["speedMode"],
            "maxVelocityUiPerSec": action["maxVelocityUiPerSec"],
        }
        token = self.safety.capture(action["side"])
        generation = self._stop_generation
        with self.safety.operation(action["side"]):
            self.safety.check(token)
            config = await self._get_config_async()
            self._validated_action(action, config)
            if self.validate_hardware_action is None:
                raise RuntimeError("hardware action validator is not configured")
            await self.validate_hardware_action(action)
            self.safety.check(token)
            if generation != self._stop_generation or not self._auto_running:
                raise RuntimeError("action cancelled by a newer stop before HAL dispatch")
            self._last_dispatch = {"action": action, "mode": "hal", "outcome": "pending", "ts": now_ms()}
            return await self.hal.command("motion.manual_axis_move", payload)

    def _validated_action(self, payload: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        action_type = str(payload.get("type") or "manual_axis_move")
        side = str(payload.get("side") or "left")
        axis = str(payload.get("axis") or "X")
        raw_direction = payload.get("direction", 1)
        if type(raw_direction) not in (int, float) or raw_direction not in {-1, 1}:
            raise RuntimeError("direction must be -1 or 1")
        direction = int(raw_direction)
        try:
            step = float(payload.get("step", 0.0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("step must be a finite number") from exc
        if not math.isfinite(step) or step < 0:
            raise RuntimeError("step must be finite and non-negative")
        speed_mode = str(payload.get("speedMode") or "fine")
        if action_type != "manual_axis_move":
            raise RuntimeError("only manual_axis_move actions are accepted")
        if side not in {"left", "right"}:
            raise RuntimeError("side must be left or right")
        if axis not in {"X", "Y", "Z", "Roll", "Pitch", "Yaw"}:
            raise RuntimeError("axis must be X/Y/Z/Roll/Pitch/Yaw")
        if direction not in {-1, 1}:
            raise RuntimeError("direction must be -1 or 1")
        caps = self._safety_caps(config)
        is_translation = axis in {"X", "Y", "Z"}
        max_step = caps["translationStepUm"] if is_translation else caps["rotationStepDeg"]
        if not math.isfinite(max_step) or max_step <= 0:
            raise RuntimeError("step cap must be finite and positive")
        if abs(step) > max_step:
            raise RuntimeError(f"step exceeds auto safety cap: {max_step}")
        try:
            velocity = float(payload.get("maxVelocityUiPerSec", 50.0 if is_translation else 0.05))
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("velocity must be a finite number") from exc
        max_velocity = caps["translationVelocityUmS"] if is_translation else caps["rotationVelocityDegS"]
        if not math.isfinite(velocity) or velocity <= 0 or not math.isfinite(max_velocity) or max_velocity <= 0:
            raise RuntimeError("velocity and velocity cap must be finite and positive")
        if velocity > max_velocity:
            raise RuntimeError(f"velocity exceeds auto safety cap: {max_velocity}")
        return {
            "id": f"act-{now_ms()}",
            "type": action_type,
            "side": side,
            "axis": axis,
            "direction": direction,
            "step": step,
            "speedMode": speed_mode if speed_mode in {"fine", "medium", "coarse"} else "fine",
            "maxVelocityUiPerSec": velocity,
            "createdAt": now_ms(),
        }

    def _safety_caps(self, config: dict[str, Any] | None = None) -> dict[str, float]:
        config = config if config is not None else self.settings.get_config()
        auto_config = config.get("auto", {})
        return {
            "translationStepUm": float(auto_config.get("translationStepUm", 200.0)),
            "rotationStepDeg": float(auto_config.get("rotationStepDeg", 0.2)),
            "translationVelocityUmS": float(auto_config.get("translationVelocityUmS", 1000.0)),
            "rotationVelocityDegS": float(auto_config.get("rotationVelocityDegS", 0.5)),
        }

    def _model(self, model_id: str, name: str, status: str, note: str, latency_ms: float) -> dict[str, Any]:
        return {
            "id": model_id,
            "name": name,
            "status": status,
            "note": note,
            "latencyMs": latency_ms,
            "updatedAt": now_ms(),
        }

    def _safe_id(self, value: str) -> str:
        return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")[:80] or "model"
