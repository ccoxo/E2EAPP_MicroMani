from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from backend.core.config import SettingsService
from backend.core.logging import LogService, now_ms
from backend.hal_client.client import HalClient


class PolicyService:
    def __init__(self, settings: SettingsService, hal: HalClient, logs: LogService) -> None:
        self.settings = settings
        self.hal = hal
        self.logs = logs
        self._lock = asyncio.Lock()
        self._models: dict[str, dict[str, Any]] = {
            "act": self._model("act", "ACT", "ready", "local baseline policy", 32),
            "diffusion_policy": self._model("diffusion_policy", "Diffusion Policy", "ready", "async policy", 108),
            "smolvla": self._model("smolvla", "SmolVLA / OpenVLA", "not_loaded", "VLA checkpoint pending", 146),
        }
        self._active_model_id = ""
        self._auto_running = False
        self._action_queue: list[dict[str, Any]] = []
        self._fine_tune_jobs: list[dict[str, Any]] = []
        self._last_dispatch: dict[str, Any] | None = None

    def list_models(self) -> dict[str, Any]:
        return {"models": list(self._models.values()), "activeModelId": self._active_model_id}

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

    def auto_status(self) -> dict[str, Any]:
        config = self.settings.get_config()
        auto_config = config.get("auto", {})
        dispatch_enabled = bool(auto_config.get("allowHardwareDispatch", False))
        return {
            "running": self._auto_running,
            "activeModelId": self._active_model_id,
            "queueDepth": len(self._action_queue),
            "dispatchEnabled": dispatch_enabled,
            "safetyCaps": self._safety_caps(),
            "lastDispatch": self._last_dispatch,
            "queue": list(self._action_queue[-20:]),
        }

    async def auto_start(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = payload or {}
        model_id = str(request.get("modelId") or self._active_model_id or "act")
        async with self._lock:
            if model_id not in self._models:
                raise FileNotFoundError(model_id)
            self._active_model_id = model_id
            self._models[model_id]["status"] = "running"
            self._auto_running = True
        self.logs.info("[POLICY]", f"auto execution started with model={model_id}")
        return self.auto_status()

    async def auto_stop(self) -> dict[str, Any]:
        async with self._lock:
            self._auto_running = False
            self._action_queue.clear()
        self.logs.warning("[POLICY]", "auto execution stopped; action queue cleared")
        return self.auto_status()

    async def queue_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = self._validated_action(payload)
        async with self._lock:
            self._action_queue.append(action)
            self._action_queue = self._action_queue[-200:]
        self.logs.info("[POLICY]", f"action queued: {action['id']}")
        return {"action": action, "status": self.auto_status()}

    async def dispatch_next(self) -> dict[str, Any]:
        async with self._lock:
            if not self._auto_running:
                return {"dispatched": False, "reason": "auto execution is not running", "status": self.auto_status()}
            action = self._action_queue.pop(0) if self._action_queue else None
        if action is None:
            return {"dispatched": False, "reason": "action queue is empty", "status": self.auto_status()}
        config = self.settings.get_config()
        dispatch_enabled = bool(config.get("auto", {}).get("allowHardwareDispatch", False))
        if not dispatch_enabled:
            self._last_dispatch = {"action": action, "mode": "dry-run", "ts": now_ms()}
            return {
                "dispatched": False,
                "reason": "hardware dispatch disabled",
                "action": action,
                "status": self.auto_status(),
            }
        result = await self._dispatch_action_to_hal(action)
        self._last_dispatch = {"action": action, "mode": "hal", "result": result, "ts": now_ms()}
        return {"dispatched": True, "action": action, "hal": result, "status": self.auto_status()}

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
        return await self.hal.command("motion.manual_axis_move", payload)

    def _validated_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action_type = str(payload.get("type") or "manual_axis_move")
        side = str(payload.get("side") or "left")
        axis = str(payload.get("axis") or "X")
        direction = int(payload.get("direction") or 1)
        step = float(payload.get("step") or 0.0)
        speed_mode = str(payload.get("speedMode") or "fine")
        if action_type != "manual_axis_move":
            raise RuntimeError("only manual_axis_move actions are accepted")
        if side not in {"left", "right"}:
            raise RuntimeError("side must be left or right")
        if axis not in {"X", "Y", "Z", "Roll", "Pitch", "Yaw"}:
            raise RuntimeError("axis must be X/Y/Z/Roll/Pitch/Yaw")
        if direction not in {-1, 1}:
            raise RuntimeError("direction must be -1 or 1")
        caps = self._safety_caps()
        is_translation = axis in {"X", "Y", "Z"}
        max_step = caps["translationStepUm"] if is_translation else caps["rotationStepDeg"]
        if abs(step) > max_step:
            raise RuntimeError(f"step exceeds auto safety cap: {max_step}")
        velocity = float(payload.get("maxVelocityUiPerSec") or (50.0 if is_translation else 0.05))
        max_velocity = caps["translationVelocityUmS"] if is_translation else caps["rotationVelocityDegS"]
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

    def _safety_caps(self) -> dict[str, float]:
        config = self.settings.get_config()
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
