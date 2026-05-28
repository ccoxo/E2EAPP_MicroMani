from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from backend.core.defaults import (
    DEFAULT_SOFT_LIMITS,
    ICF_CAMERA_DEFAULTS,
    ICF_KINEMATICS_DEFAULTS,
    ICF_LEFT_MOTION_MECHANICAL_LIMITS,
    ICF_LEFT_MOTION_SOFT_LIMITS,
    ICF_RIGHT_MOTION_MECHANICAL_LIMITS,
    ICF_RIGHT_MOTION_SOFT_LIMITS,
    ICF_ROTATION_WORK_LIMIT_DEFAULTS,
    ICF_TELEOP_DEFAULTS,
    ICF_TELEOP_STRATEGY_VERSION,
    ICF_WORK_ORIGIN_DEFAULTS,
    ICF_WORK_ORIGIN_VERSION,
    anchored_mechanical_soft_limits,
    default_config,
    rotation_work_limits_from_soft_limits,
)
from backend.core.logging import LogService, now_ms, stable_config_hash
from backend.core.motion_limits import ui_limit_to_config
from backend.core.schemas import (
    AppConfig,
    MotionCardSnapshotConfig,
    ParameterSnapshot,
    SnapshotCreateRequest,
    SnapshotScope,
)

SNAPSHOT_ID_SAFE = re.compile(r"[^a-zA-Z0-9_.-]+")
AXIS_KEYS = ("x", "y", "z", "roll", "pitch", "yaw")


def _compact_config_value(value: Any) -> Any:
    if isinstance(value, dict):
        return "{...}"
    if isinstance(value, list):
        return f"[{len(value)} items]"
    return value


def _changed_config_leaves(old: Any, new: Any, prefix: str = "") -> list[tuple[str, Any, Any]]:
    if isinstance(old, dict) and isinstance(new, dict):
        changes: list[tuple[str, Any, Any]] = []
        for key in sorted(set(old) | set(new)):
            path = f"{prefix}.{key}" if prefix else str(key)
            changes.extend(_changed_config_leaves(old.get(key), new.get(key), path))
        return changes
    if old != new:
        return [(prefix or "config", _compact_config_value(old), _compact_config_value(new))]
    return []


def _relative_soft_limits_from_teleop(config: dict[str, Any], side: str) -> dict[str, Any]:
    teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
    mins = teleop.get(f"{side}SoftLimitMin")
    maxes = teleop.get(f"{side}SoftLimitMax")
    fallback = ICF_LEFT_MOTION_SOFT_LIMITS if side == "left" else ICF_RIGHT_MOTION_SOFT_LIMITS
    if not isinstance(mins, list) or not isinstance(maxes, list) or len(mins) < 6 or len(maxes) < 6:
        return json.loads(json.dumps(fallback))
    try:
        return {
            axis: {
                "min": ui_limit_to_config(float(mins[index]), index),
                "max": ui_limit_to_config(float(maxes[index]), index),
            }
            for index, axis in enumerate(AXIS_KEYS)
        }
    except (TypeError, ValueError):
        return json.loads(json.dumps(fallback))


def reanchor_motion_soft_limits_to_current_origin(config: dict[str, Any], side: str | None = None) -> None:
    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    origin = motion.get("origin", {}) if isinstance(motion, dict) else {}
    kinematics = motion.get("kinematics", {}) if isinstance(motion, dict) else {}
    if not isinstance(origin, dict) or not isinstance(kinematics, dict):
        return
    sides = ("left", "right") if side is None else (side,)
    for active_side in sides:
        valid_key = "leftValid" if active_side == "left" else "rightValid"
        pulse_key = "leftPulse" if active_side == "left" else "rightPulse"
        signed_key = "leftSignedPulsePerUnit" if active_side == "left" else "rightSignedPulsePerUnit"
        origin_pulse = origin.get(pulse_key)
        signed_pulse_per_unit = kinematics.get(signed_key)
        if (
            not bool(origin.get(valid_key, origin.get("valid", False)))
            or not isinstance(origin_pulse, list)
            or len(origin_pulse) < 6
            or not isinstance(signed_pulse_per_unit, list)
            or len(signed_pulse_per_unit) < 6
        ):
            continue
        try:
            next_limits = anchored_mechanical_soft_limits(
                _relative_soft_limits_from_teleop(config, active_side),
                [float(value) for value in origin_pulse[:6]],
                [float(value) for value in signed_pulse_per_unit[:6]],
            )
            current_limits = motion.get(f"{active_side}SoftLimits")
            if isinstance(current_limits, dict):
                for axis_key in AXIS_KEYS[:3]:
                    current_axis = current_limits.get(axis_key)
                    if isinstance(current_axis, dict):
                        next_limits[axis_key] = {
                            "min": float(current_axis["min"]),
                            "max": float(current_axis["max"]),
                        }
            motion[f"{active_side}SoftLimits"] = next_limits
        except (TypeError, ValueError, ZeroDivisionError):
            continue


def _float_close(value: Any, expected: float, tolerance: float = 1e-6) -> bool:
    try:
        return abs(float(value) - expected) <= tolerance
    except (TypeError, ValueError):
        return False


def _right_axis_limits_for_current_origin(
    config: dict[str, Any], axis_index: int, min_deg: float, max_deg: float
) -> dict[str, float] | None:
    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    origin = motion.get("origin", {}) if isinstance(motion, dict) else {}
    kinematics = motion.get("kinematics", {}) if isinstance(motion, dict) else {}
    right_pulse = origin.get("rightPulse") if isinstance(origin, dict) else None
    right_signed = kinematics.get("rightSignedPulsePerUnit") if isinstance(kinematics, dict) else None
    if (
        not isinstance(origin, dict)
        or not bool(origin.get("rightValid", origin.get("valid", False)))
        or not isinstance(right_pulse, list)
        or len(right_pulse) <= axis_index
        or not isinstance(right_signed, list)
        or len(right_signed) <= axis_index
    ):
        return None
    try:
        origin_deg = float(right_pulse[axis_index]) / float(right_signed[axis_index])
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return {
        "min": ui_limit_to_config(origin_deg + min_deg, axis_index),
        "max": ui_limit_to_config(origin_deg + max_deg, axis_index),
    }


def _normalize_right_yaw_disabled(config: dict[str, Any]) -> None:
    teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
    if not isinstance(teleop, dict):
        return
    raw = teleop.get("rightEnabledAxes")
    axes = [True, True, True, True, True, False]
    if isinstance(raw, list) and len(raw) >= 6:
        axes = [bool(value) for value in raw[:6]]
        axes[5] = False
    teleop["rightEnabledAxes"] = axes


def _normalize_right_roll_window(config: dict[str, Any]) -> None:
    teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
    mins = teleop.get("rightSoftLimitMin") if isinstance(teleop, dict) else None
    maxes = teleop.get("rightSoftLimitMax") if isinstance(teleop, dict) else None
    if (
        isinstance(mins, list)
        and isinstance(maxes, list)
        and len(mins) >= 4
        and len(maxes) >= 4
        and _float_close(mins[3], -100.0)
        and (_float_close(maxes[3], 0.0) or _float_close(maxes[3], 100.0))
    ):
        mins[3] = -90.0
        maxes[3] = 100.0

    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    work_limits = motion.get("rotationWorkLimits", {}) if isinstance(motion, dict) else {}
    right_work = work_limits.get("right", {}) if isinstance(work_limits, dict) else {}
    right_roll_work = right_work.get("roll", {}) if isinstance(right_work, dict) else {}
    if (
        isinstance(right_roll_work, dict)
        and _float_close(right_roll_work.get("min"), -100.0)
        and (_float_close(right_roll_work.get("max"), 0.0) or _float_close(right_roll_work.get("max"), 100.0))
    ):
        right_roll_work["min"] = -90.0
        right_roll_work["max"] = 100.0

    right_soft_limits = motion.get("rightSoftLimits", {}) if isinstance(motion, dict) else {}
    right_roll_soft = right_soft_limits.get("roll", {}) if isinstance(right_soft_limits, dict) else {}
    old_negative_only_limits = _right_axis_limits_for_current_origin(config, 3, -100.0, 0.0)
    old_symmetric_limits = _right_axis_limits_for_current_origin(config, 3, -100.0, 100.0)
    next_limits = _right_axis_limits_for_current_origin(config, 3, -90.0, 100.0)
    if (
        isinstance(right_roll_soft, dict)
        and next_limits is not None
        and (
            (
                old_negative_only_limits is not None
                and _float_close(right_roll_soft.get("min"), old_negative_only_limits["min"], 1e-3)
                and _float_close(right_roll_soft.get("max"), old_negative_only_limits["max"], 1e-3)
            )
            or (
                old_symmetric_limits is not None
                and _float_close(right_roll_soft.get("min"), old_symmetric_limits["min"], 1e-3)
                and _float_close(right_roll_soft.get("max"), old_symmetric_limits["max"], 1e-3)
            )
        )
    ):
        right_soft_limits["roll"] = next_limits


def _normalize_right_pitch_window(config: dict[str, Any]) -> None:
    teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
    mins = teleop.get("rightSoftLimitMin") if isinstance(teleop, dict) else None
    maxes = teleop.get("rightSoftLimitMax") if isinstance(teleop, dict) else None
    if (
        isinstance(mins, list)
        and isinstance(maxes, list)
        and len(mins) >= 5
        and len(maxes) >= 5
        and _float_close(mins[4], -100.0)
        and _float_close(maxes[4], 100.0)
    ):
        mins[4] = -90.0
        maxes[4] = 90.0

    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    work_limits = motion.get("rotationWorkLimits", {}) if isinstance(motion, dict) else {}
    right_work = work_limits.get("right", {}) if isinstance(work_limits, dict) else {}
    right_pitch_work = right_work.get("pitch", {}) if isinstance(right_work, dict) else {}
    if (
        isinstance(right_pitch_work, dict)
        and _float_close(right_pitch_work.get("min"), -100.0)
        and _float_close(right_pitch_work.get("max"), 100.0)
    ):
        right_pitch_work["min"] = -90.0
        right_pitch_work["max"] = 90.0

    right_soft_limits = motion.get("rightSoftLimits", {}) if isinstance(motion, dict) else {}
    right_pitch_soft = right_soft_limits.get("pitch", {}) if isinstance(right_soft_limits, dict) else {}
    old_limits = _right_axis_limits_for_current_origin(config, 4, -100.0, 100.0)
    next_limits = _right_axis_limits_for_current_origin(config, 4, -90.0, 90.0)
    if (
        isinstance(right_pitch_soft, dict)
        and old_limits is not None
        and next_limits is not None
        and _float_close(right_pitch_soft.get("min"), old_limits["min"], 1e-3)
        and _float_close(right_pitch_soft.get("max"), old_limits["max"], 1e-3)
    ):
        right_soft_limits["pitch"] = next_limits


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
                self.save_config(validated, emit_log=False, source="startup")
            return validated
        except (OSError, json.JSONDecodeError, ValueError):
            config = default_config()
            self.save_config(config, source="startup")
            self.logs.warning("[BACKEND]", "config.json was invalid; default config restored")
            return config

    def save_config(
        self,
        config: dict[str, Any],
        emit_log: bool = True,
        *,
        source: str = "ui",
        op_id: str | None = None,
    ) -> dict[str, Any]:
        old_config: dict[str, Any] = {}
        if self.config_path.exists():
            try:
                loaded = json.loads(self.config_path.read_text(encoding="utf-8"))
                old_config = loaded if isinstance(loaded, dict) else {}
            except (OSError, json.JSONDecodeError):
                old_config = {}
        if isinstance(config, dict):
            _normalize_right_yaw_disabled(config)
            _normalize_right_pitch_window(config)
        validated = AppConfig.model_validate(config).model_dump(mode="json")
        old_hash = stable_config_hash(old_config) if old_config else "-"
        new_hash = stable_config_hash(validated)
        self.config_path.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
        if emit_log:
            changes = _changed_config_leaves(old_config, validated)
            for key, old, new in changes[:50]:
                self.logs.event(
                    "[BACKEND]",
                    "INFO",
                    "config_write",
                    component="CONFIG",
                    op_id=op_id,
                    source=source,
                    scope=key.split(".", 1)[0],
                    key=key,
                    old=old,
                    new=new,
                    oldHash=old_hash,
                    newHash=new_hash,
                    configPath=str(self.config_path),
                )
            if len(changes) > 50:
                self.logs.event(
                    "[BACKEND]",
                    "WARNING",
                    "config_write",
                    component="CONFIG",
                    op_id=op_id,
                    source=source,
                    scope="all",
                    key="truncated",
                    old=f"{len(changes)} changes",
                    new="first 50 logged",
                    oldHash=old_hash,
                    newHash=new_hash,
                    configPath=str(self.config_path),
                )
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
            teleop["leftGravityCompensation"] = True
            teleop["rightGravityCompensation"] = True
            teleop["leftForceFeedback"] = True
            teleop["rightForceFeedback"] = True
            motion = config.get("motion", {})
            if isinstance(motion, dict):
                motion["leftSoftLimits"] = json.loads(json.dumps(ICF_LEFT_MOTION_SOFT_LIMITS))
                motion["rightSoftLimits"] = json.loads(json.dumps(ICF_RIGHT_MOTION_SOFT_LIMITS))
                motion["yawSoftLimitDeg"] = 7
                motion["kinematics"] = json.loads(json.dumps(ICF_KINEMATICS_DEFAULTS))
            safety = config.get("safety", {})
            if isinstance(safety, dict):
                safety["yawSoftLimitDeg"] = 7
            gripper = config.get("gripper", {})
            if isinstance(gripper, dict):
                gripper["leftPort"] = "COM8"
                gripper["rightPort"] = "COM9"
                gripper["leftSlaveId"] = 10
                gripper["rightSlaveId"] = 9
                gripper["strokeMm"] = 26
            gripper_teleop = teleop.get("gripperTeleop", {})
            if isinstance(gripper_teleop, dict):
                gripper_teleop["enabled"] = True
                gripper_teleop["leftGapMinMm"] = 0.0
                gripper_teleop["leftGapMaxMm"] = 25.0
                gripper_teleop["rightGapMinMm"] = 0.0
                gripper_teleop["rightGapMaxMm"] = 25.0
                gripper_teleop["leftGapInvert"] = False
                gripper_teleop["rightGapInvert"] = False
                gripper_teleop["gripSpeed"] = 255
                gripper_teleop["gripTorque"] = 192
                gripper_teleop["positionDeadbandCounts"] = 1
                gripper_teleop["minCommandIntervalMs"] = 20
                gripper_teleop["autoGapCalibration"] = True
                gripper_teleop["autoGapMinSpanMm"] = 2.0
                gripper_teleop["autoGapMarginMm"] = 1.0
                gripper_teleop["leftSourceHand"] = "PhysicalRight"
                gripper_teleop["rightSourceHand"] = "PhysicalLeft"
        if isinstance(teleop, dict):
            teleop.setdefault("engine", "hal_native")
            teleop.setdefault("controlMode", "incremental_position")
            teleop.setdefault("mappingMode", ICF_TELEOP_DEFAULTS["mappingMode"])
            if teleop.get("mappingMode") not in {"direct", "legacy"}:
                teleop["mappingMode"] = ICF_TELEOP_DEFAULTS["mappingMode"]
            if teleop.get("engine") == "hal_native" and teleop.get("controlMode") == "incremental":
                teleop["controlMode"] = "incremental_position"
            if teleop.get("engine") == "hal_native" and teleop.get("controlMode") == "velocity_admittance":
                teleop["controlMode"] = "incremental_position"
            teleop.setdefault("nativeLoopHz", 100)
            teleop.setdefault("nativeTranslationDeadzoneM", 0.002)
            teleop.setdefault("nativeTranslationFullScaleM", 0.04)
            teleop.setdefault("nativeRotationDeadzoneDeg", 2.0)
            teleop.setdefault("nativeRotationFullScaleDeg", 30.0)
            teleop.setdefault("nativeVelocitySmoothingMs", 40.0)
        if isinstance(teleop, dict):
            if teleop.get("swapHands") is True:
                teleop["swapHands"] = ICF_TELEOP_DEFAULTS["swapHands"]
            if teleop.get("swapTeleopChannels") is False:
                teleop["swapTeleopChannels"] = ICF_TELEOP_DEFAULTS["swapTeleopChannels"]
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
            if teleop.get("leftAxisOutputScale") == [0.20, 0.20, 0.20, 0.25, 0.25, 1.00]:
                teleop["leftAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["leftAxisOutputScale"]))
            if teleop.get("rightAxisOutputScale") == [0.20, 0.20, 0.20, 0.25, 0.25, 1.00]:
                teleop["rightAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["rightAxisOutputScale"]))
            if teleop.get("leftAxisOutputScale") == [1, 1, 1, 1, 1, 1]:
                teleop["leftAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["leftAxisOutputScale"]))
            if teleop.get("rightAxisOutputScale") == [1, 1, 1, 1, 1, 1]:
                teleop["rightAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["rightAxisOutputScale"]))
            if teleop.get("leftAxisOutputScale") == [0.60, 0.30, 0.30, 1.50, 0.30, 1.00]:
                teleop["leftAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["leftAxisOutputScale"]))
            if teleop.get("rightAxisOutputScale") == [0.60, 0.30, 0.30, 1.50, 0.30, 0.30]:
                teleop["rightAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["rightAxisOutputScale"]))
            if teleop.get("leftAxisOutputScale") == [0.60, 0.30, 0.30, 1.50, 1.00, 1.00]:
                teleop["leftAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["leftAxisOutputScale"]))
            if teleop.get("rightAxisOutputScale") == [0.60, 0.30, 0.30, 1.50, 1.00, 0.30]:
                teleop["rightAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["rightAxisOutputScale"]))
            if float(teleop.get("translationDeadzone", 0.0)) == 0.00001:
                teleop["translationDeadzone"] = ICF_TELEOP_DEFAULTS["translationDeadzone"]
            if float(teleop.get("rotationDeadzone", 0.0)) == 0.02:
                teleop["rotationDeadzone"] = ICF_TELEOP_DEFAULTS["rotationDeadzone"]
            if float(teleop.get("translationInputEpsilon", 0.0)) == 1e-7:
                teleop["translationInputEpsilon"] = ICF_TELEOP_DEFAULTS["translationInputEpsilon"]
            if float(teleop.get("rotationInputEpsilon", 0.0)) in {0.001, 0.03}:
                teleop["rotationInputEpsilon"] = ICF_TELEOP_DEFAULTS["rotationInputEpsilon"]
            if int(teleop.get("continuousMicroConfirmTicks", 0)) == 2:
                teleop["continuousMicroConfirmTicks"] = ICF_TELEOP_DEFAULTS["continuousMicroConfirmTicks"]
            if teleop.get("leftDirectionSign") != ICF_TELEOP_DEFAULTS["leftDirectionSign"]:
                teleop["leftDirectionSign"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["leftDirectionSign"]))
            if teleop.get("rightDirectionSign") != ICF_TELEOP_DEFAULTS["rightDirectionSign"]:
                teleop["rightDirectionSign"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["rightDirectionSign"]))
            if teleop.get("leftImpulseCoeff") != ICF_TELEOP_DEFAULTS["leftImpulseCoeff"]:
                teleop["leftImpulseCoeff"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["leftImpulseCoeff"]))
            if teleop.get("rightImpulseCoeff") != ICF_TELEOP_DEFAULTS["rightImpulseCoeff"]:
                teleop["rightImpulseCoeff"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["rightImpulseCoeff"]))
            if teleop.get("syncImpulseCoeffFromKinematics") is not False:
                teleop["syncImpulseCoeffFromKinematics"] = False
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
        if isinstance(motion, dict):
            if self._uses_legacy_motion_profile(motion.get("leftProfile")):
                motion["leftProfile"] = json.loads(json.dumps(default_config()["motion"]["leftProfile"]))
            if self._uses_legacy_motion_profile(motion.get("rightProfile")):
                motion["rightProfile"] = json.loads(json.dumps(default_config()["motion"]["rightProfile"]))
            if self._uses_pre_icf_rotation_profile(motion.get("leftProfile")):
                motion["leftProfile"] = json.loads(json.dumps(default_config()["motion"]["leftProfile"]))
            if self._uses_pre_icf_rotation_profile(motion.get("rightProfile")):
                motion["rightProfile"] = json.loads(json.dumps(default_config()["motion"]["rightProfile"]))
            if motion.get("leftSoftLimits") == DEFAULT_SOFT_LIMITS:
                motion["leftSoftLimits"] = json.loads(json.dumps(ICF_LEFT_MOTION_SOFT_LIMITS))
            if motion.get("rightSoftLimits") == DEFAULT_SOFT_LIMITS:
                motion["rightSoftLimits"] = json.loads(json.dumps(ICF_RIGHT_MOTION_SOFT_LIMITS))
        if isinstance(motion, dict) and not has_current_work_origin_strategy:
            origin = motion.get("origin", {})
            origin_valid = isinstance(origin, dict) and bool(origin.get("valid", False))
            if not origin_valid:
                motion["origin"] = json.loads(json.dumps(ICF_WORK_ORIGIN_DEFAULTS))
                origin = motion["origin"]
            kinematics = motion.get("kinematics", {})
            if not isinstance(kinematics, dict):
                kinematics = ICF_KINEMATICS_DEFAULTS
            left_limits = motion.get("leftSoftLimits")
            right_limits = motion.get("rightSoftLimits")
            left_limits = left_limits if isinstance(left_limits, dict) else ICF_LEFT_MOTION_SOFT_LIMITS
            right_limits = right_limits if isinstance(right_limits, dict) else ICF_RIGHT_MOTION_SOFT_LIMITS
            left_origin = origin.get("leftPulse") if isinstance(origin, dict) else None
            right_origin = origin.get("rightPulse") if isinstance(origin, dict) else None
            left_signed = kinematics.get("leftSignedPulsePerUnit") if isinstance(kinematics, dict) else None
            right_signed = kinematics.get("rightSignedPulsePerUnit") if isinstance(kinematics, dict) else None
            motion["rotationWorkLimits"] = rotation_work_limits_from_soft_limits(left_limits, right_limits)
            if (
                isinstance(left_origin, list)
                and len(left_origin) >= 6
                and isinstance(left_signed, list)
                and len(left_signed) >= 6
            ):
                motion["leftSoftLimits"] = anchored_mechanical_soft_limits(left_limits, left_origin, left_signed)
            else:
                motion["leftSoftLimits"] = json.loads(json.dumps(ICF_LEFT_MOTION_MECHANICAL_LIMITS))
            if (
                isinstance(right_origin, list)
                and len(right_origin) >= 6
                and isinstance(right_signed, list)
                and len(right_signed) >= 6
            ):
                motion["rightSoftLimits"] = anchored_mechanical_soft_limits(right_limits, right_origin, right_signed)
            else:
                motion["rightSoftLimits"] = json.loads(json.dumps(ICF_RIGHT_MOTION_MECHANICAL_LIMITS))
            motion["workOriginStrategyVersion"] = ICF_WORK_ORIGIN_VERSION
        if isinstance(motion, dict):
            motion.setdefault("rotationWorkLimits", json.loads(json.dumps(ICF_ROTATION_WORK_LIMIT_DEFAULTS)))
        _normalize_right_yaw_disabled(config)
        _normalize_right_roll_window(config)
        _normalize_right_pitch_window(config)
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
            if gripper_teleop.get("leftSourceHand") != "PhysicalRight":
                gripper_teleop["leftSourceHand"] = "PhysicalRight"
            if gripper_teleop.get("rightSourceHand") != "PhysicalLeft":
                gripper_teleop["rightSourceHand"] = "PhysicalLeft"
            gripper_teleop.setdefault("leftGapInvert", False)
            if teleop.get("engine") == "hal_native" and gripper_teleop.get("rightGapInvert") is True:
                gripper_teleop["rightGapInvert"] = False
            gripper_teleop.setdefault("rightGapInvert", False)
        return config

    def _uses_legacy_motion_profile(self, profile: object) -> bool:
        if not isinstance(profile, dict):
            return False
        translation = profile.get("translation")
        rotation = profile.get("rotation")
        if not isinstance(translation, dict) or not isinstance(rotation, dict):
            return False
        try:
            return (
                float(translation.get("startSpeed", 0.0)) == 100.0
                and float(translation.get("maxSpeed", 0.0)) == 1000.0
                and float(rotation.get("startSpeed", 0.0)) == 0.3
                and float(rotation.get("maxSpeed", 0.0)) == 3.0
                and float(rotation.get("accTimeSec", 0.0)) == 0.02
                and float(rotation.get("decTimeSec", 0.0)) == 0.02
            )
        except (TypeError, ValueError):
            return False

    def _uses_pre_icf_rotation_profile(self, profile: object) -> bool:
        if not isinstance(profile, dict):
            return False
        rotation = profile.get("rotation")
        if not isinstance(rotation, dict):
            return False
        try:
            return (
                float(rotation.get("startSpeed", 0.0)) == 0.25
                and float(rotation.get("maxSpeed", 0.0)) == 3.0
            )
        except (TypeError, ValueError):
            return False
