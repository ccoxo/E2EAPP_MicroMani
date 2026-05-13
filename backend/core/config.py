from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from backend.core.defaults import (
    DEFAULT_SOFT_LIMITS,
    ICF_CAMERA_DEFAULTS,
    ICF_TELEOP_DEFAULTS,
    ICF_TELEOP_STRATEGY_VERSION,
    ICF_WORK_ORIGIN_DEFAULTS,
    ICF_WORK_ORIGIN_VERSION,
    default_config,
)
from backend.core.logging import LogService, now_ms
from backend.core.schemas import (
    AppConfig,
    MotionCardSnapshotConfig,
    ParameterSnapshot,
    SnapshotCreateRequest,
    SnapshotScope,
)

SNAPSHOT_ID_SAFE = re.compile(r"[^a-zA-Z0-9_.-]+")


class SettingsService:
    def __init__(self, runtime_dir: Path, logs: LogService) -> None:
        self.runtime_dir = runtime_dir
        self.config_path = runtime_dir / "config.json"
        self.snapshot_dir = runtime_dir / "snapshots"
        self.logs = logs
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.save_config(default_config(), emit_log=False)

    def get_config(self) -> dict[str, Any]:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            raw_teleop = data.get("teleop", {}) if isinstance(data, dict) else {}
            raw_motion = data.get("motion", {}) if isinstance(data, dict) else {}
            has_current_teleop_strategy = (
                isinstance(raw_teleop, dict)
                and raw_teleop.get("strategyVersion") == ICF_TELEOP_STRATEGY_VERSION
            )
            has_current_work_origin_strategy = (
                isinstance(raw_motion, dict)
                and raw_motion.get("workOriginStrategyVersion") == ICF_WORK_ORIGIN_VERSION
            )
            merged = self._migrate_config(
                self._merge_defaults(data),
                has_current_teleop_strategy,
                has_current_work_origin_strategy,
            )
            validated = AppConfig.model_validate(merged).model_dump(mode="json")
            if merged != data:
                self.save_config(validated, emit_log=False)
            return validated
        except (OSError, json.JSONDecodeError, ValueError):
            config = default_config()
            self.save_config(config)
            self.logs.warning("[BACKEND]", "config.json was invalid; default config restored")
            return config

    def save_config(self, config: dict[str, Any], emit_log: bool = True) -> dict[str, Any]:
        validated = AppConfig.model_validate(config).model_dump(mode="json")
        self.config_path.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
        if emit_log:
            self.logs.info("[BACKEND]", "settings saved to config.json")
        return validated

    def apply_config(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        active = self.save_config(config) if config is not None else self.get_config()
        self.logs.info("[HAL]", "settings applied to backend runtime config")
        return active

    def list_snapshots(self, scope: SnapshotScope | None = None) -> list[dict[str, Any]]:
        snapshots: list[ParameterSnapshot] = []
        for path in sorted(self.snapshot_dir.glob("*.json"), reverse=True):
            try:
                snapshot = ParameterSnapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if scope is None or snapshot.scope == scope:
                snapshots.append(snapshot)
        return [snapshot.model_dump(mode="json") for snapshot in snapshots]

    def create_snapshot(self, request: SnapshotCreateRequest) -> dict[str, Any]:
        config = request.config if request.config is not None else self._snapshot_config(request.scope)
        if isinstance(config, MotionCardSnapshotConfig):
            payload: dict[str, Any] | MotionCardSnapshotConfig = config
        else:
            payload = dict(config)
        snapshot = ParameterSnapshot(
            id=self._snapshot_id(request.scope, request.name),
            name=request.name,
            createdAt=now_ms(),
            scope=request.scope,
            config=payload,
        )
        self._write_snapshot(snapshot)
        self.logs.info("[BACKEND]", f"{self._scope_label(request.scope)}快照已保存：{request.name}")
        return snapshot.model_dump(mode="json")

    def apply_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        snapshot = self._read_snapshot(snapshot_id)
        active = self.get_config()
        if snapshot.scope == "all":
            next_config = AppConfig.model_validate(snapshot.config).model_dump(mode="json")
        else:
            motion_config = MotionCardSnapshotConfig.model_validate(snapshot.config)
            next_config = self._apply_motion_snapshot(active, snapshot.scope, motion_config)
        saved = self.save_config(next_config, emit_log=False)
        self.logs.info("[BACKEND]", f"{self._scope_label(snapshot.scope)}快照已应用：{snapshot.name}")
        return saved

    def delete_snapshot(self, snapshot_id: str) -> None:
        path = self._snapshot_path(snapshot_id)
        if not path.exists():
            raise FileNotFoundError(snapshot_id)
        path.unlink()
        self.logs.info("[BACKEND]", "参数快照已删除")

    def _snapshot_config(self, scope: SnapshotScope) -> dict[str, Any]:
        config = self.get_config()
        if scope == "all":
            return config
        side = "left" if scope == "motion-left" else "right"
        return {
            "cardNo": config["motion"][f"{side}CardNo"],
            "motionThreadHz": config["motion"]["motionThreadHz"],
            "yawSoftLimitDeg": config["motion"]["yawSoftLimitDeg"],
            "positionSource": config["motion"]["positionSource"],
            "profile": config["motion"][f"{side}Profile"],
            "softLimits": config["motion"][f"{side}SoftLimits"],
        }

    def _apply_motion_snapshot(
        self,
        config: dict[str, Any],
        scope: SnapshotScope,
        snapshot: MotionCardSnapshotConfig,
    ) -> dict[str, Any]:
        side = "left" if scope == "motion-left" else "right"
        next_config: dict[str, Any] = json.loads(json.dumps(config))
        next_config["motion"][f"{side}CardNo"] = snapshot.cardNo
        next_config["motion"]["motionThreadHz"] = snapshot.motionThreadHz
        next_config["motion"]["yawSoftLimitDeg"] = snapshot.yawSoftLimitDeg
        next_config["motion"]["positionSource"] = snapshot.positionSource
        next_config["motion"][f"{side}Profile"] = snapshot.profile.model_dump(mode="json")
        next_config["motion"][f"{side}SoftLimits"] = snapshot.softLimits.model_dump(mode="json")
        return next_config

    def _snapshot_id(self, scope: SnapshotScope, name: str) -> str:
        safe_name = SNAPSHOT_ID_SAFE.sub("-", name.strip())[:48].strip("-") or "snapshot"
        return f"{scope}-{now_ms()}-{safe_name}"

    def _snapshot_path(self, snapshot_id: str) -> Path:
        safe_id = SNAPSHOT_ID_SAFE.sub("-", snapshot_id)
        return self.snapshot_dir / f"{safe_id}.json"

    def _write_snapshot(self, snapshot: ParameterSnapshot) -> None:
        self._snapshot_path(snapshot.id).write_text(
            json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_snapshot(self, snapshot_id: str) -> ParameterSnapshot:
        path = self._snapshot_path(snapshot_id)
        if not path.exists():
            raise FileNotFoundError(snapshot_id)
        return ParameterSnapshot.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def _scope_label(self, scope: SnapshotScope) -> str:
        if scope == "all":
            return "全局硬件"
        if scope == "motion-left":
            return "左臂运动控制卡"
        return "右臂运动控制卡"

    def _merge_defaults(self, data: dict[str, Any]) -> dict[str, Any]:
        def merge(default: Any, current: Any) -> Any:
            if isinstance(default, dict) and isinstance(current, dict):
                result = dict(default)
                for key, value in current.items():
                    result[key] = merge(default.get(key), value) if key in default else value
                return result
            return current if current is not None else default

        return cast(dict[str, Any], merge(default_config(), data))

    def _migrate_config(
        self,
        config: dict[str, Any],
        has_current_teleop_strategy: bool,
        has_current_work_origin_strategy: bool,
    ) -> dict[str, Any]:
        teleop = config.get("teleop", {})
        if isinstance(teleop, dict) and not has_current_teleop_strategy:
            for key, value in ICF_TELEOP_DEFAULTS.items():
                teleop[key] = json.loads(json.dumps(value))
            motion = config.get("motion", {})
            if isinstance(motion, dict):
                motion["leftSoftLimits"] = json.loads(json.dumps(DEFAULT_SOFT_LIMITS))
                motion["rightSoftLimits"] = json.loads(json.dumps(DEFAULT_SOFT_LIMITS))
                motion["yawSoftLimitDeg"] = DEFAULT_SOFT_LIMITS["yaw"]["max"]
            safety = config.get("safety", {})
            if isinstance(safety, dict):
                safety["yawSoftLimitDeg"] = DEFAULT_SOFT_LIMITS["yaw"]["max"]
        if isinstance(teleop, dict):
            try:
                has_legacy_icf_translation_speed = (
                    float(teleop.get("translationStartVelocityUmS", 0.0)) == 400.0
                    and float(teleop.get("translationMaxVelocityUmS", 0.0)) == 5000.0
                )
            except (TypeError, ValueError):
                has_legacy_icf_translation_speed = False
            if has_legacy_icf_translation_speed:
                teleop["translationStartVelocityUmS"] = ICF_TELEOP_DEFAULTS["translationStartVelocityUmS"]
                teleop["translationMaxVelocityUmS"] = ICF_TELEOP_DEFAULTS["translationMaxVelocityUmS"]
        cameras = config.get("cameras", {})
        if isinstance(cameras, dict):
            has_legacy_reversed_wrist_cameras = (
                cameras.get("global") == "AR0234 / index 2"
                and cameras.get("wristLeft") == "IMX258 / index 1"
                and cameras.get("wristRight") == "IMX258 / index 0"
            )
            has_legacy_cyclic_camera_roles = (
                cameras.get("global") == "AR0234 / index 2"
                and cameras.get("wristLeft") == "IMX258 / index 0"
                and cameras.get("wristRight") == "IMX258 / index 1"
            )
            if (
                has_legacy_reversed_wrist_cameras
                or has_legacy_cyclic_camera_roles
            ):
                for key, value in ICF_CAMERA_DEFAULTS.items():
                    if key == "tuning":
                        continue
                    cameras[key] = json.loads(json.dumps(value))
        motion = config.get("motion", {})
        if isinstance(motion, dict) and not has_current_work_origin_strategy:
            origin = motion.get("origin", {})
            origin_valid = isinstance(origin, dict) and bool(origin.get("valid", False))
            if not origin_valid:
                motion["origin"] = json.loads(json.dumps(ICF_WORK_ORIGIN_DEFAULTS))
            motion["workOriginStrategyVersion"] = ICF_WORK_ORIGIN_VERSION
        gripper_teleop = teleop.get("gripperTeleop", {})
        if isinstance(gripper_teleop, dict):
            default_thresholds = (
                float(gripper_teleop.get("openThreshold", 0.30)) == 0.30
                and float(gripper_teleop.get("closeThreshold", 0.70)) == 0.70
            )
            if default_thresholds:
                for key in ("leftGapMaxMm", "rightGapMaxMm"):
                    if float(gripper_teleop.get(key, 25.0)) == 50.0:
                        gripper_teleop[key] = 25.0
        return config
