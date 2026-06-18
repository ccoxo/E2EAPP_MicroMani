from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, cast

from backend.core.defaults import (
    DEFAULT_SOFT_LIMITS,
    ICF_CAMERA_DEFAULTS,
    ICF_CAMERA_TUNING_DEFAULTS_VERSION,
    ICF_HOME_REFERENCE_VERSION,
    ICF_KINEMATICS_DEFAULTS,
    ICF_LEFT_MOTION_LEGACY_ANCHORED_LIMITS,
    ICF_LEFT_MOTION_MECHANICAL_LIMITS,
    ICF_LEFT_MOTION_SOFT_LIMITS,
    ICF_RELATIVE_SOFT_LIMIT_DEFAULTS,
    ICF_RIGHT_MOTION_LEGACY_ANCHORED_LIMITS,
    ICF_RIGHT_MOTION_MECHANICAL_LIMITS,
    ICF_RIGHT_MOTION_SOFT_LIMITS,
    ICF_ROTATION_WORK_LIMIT_DEFAULTS,
    ICF_TELEOP_DEFAULTS,
    ICF_TELEOP_STRATEGY_VERSION,
    ICF_WORK_ORIGIN_DEFAULTS,
    ICF_WORK_ORIGIN_OFFSET_DEFAULTS,
    ICF_WORK_ORIGIN_VERSION,
    default_config,
    rotation_work_limits_from_soft_limits,
)
from backend.core.logging import LogService, now_ms, stable_config_hash
from backend.core.motion_limits import (
    WorkOriginMissing,
    config_limit_to_ui,
    effective_limits_ui,
    side_origin_ui,
    ui_limit_to_config,
)
from backend.core.schemas import (
    AppConfig,
    MotionCardSnapshotConfig,
    ParameterSnapshot,
    SnapshotCreateRequest,
    SnapshotScope,
)

SNAPSHOT_ID_SAFE = re.compile(r"[^a-zA-Z0-9_.-]+")
AXIS_KEYS = ("x", "y", "z", "roll", "pitch", "yaw")
CARD0_YAW_DISABLED_AXES = (True, True, True, True, True, False)


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
        return cast(dict[str, Any], json.loads(json.dumps(fallback)))
    try:
        return {
            axis: {
                "min": ui_limit_to_config(float(mins[index]), index),
                "max": ui_limit_to_config(float(maxes[index]), index),
            }
            for index, axis in enumerate(AXIS_KEYS)
        }
    except (TypeError, ValueError):
        return cast(dict[str, Any], json.loads(json.dumps(fallback)))


def _six_float_list(value: Any, fallback: list[float] | None = None) -> list[float]:
    if isinstance(value, list):
        try:
            values = [float(item) for item in value[:6]]
            return values + [0.0] * max(0, 6 - len(values))
        except (TypeError, ValueError):
            pass
    return list(fallback) if fallback is not None else [0.0] * 6


def _side_valid(root: dict[str, Any], side: str) -> bool:
    key = "leftValid" if side == "left" else "rightValid"
    return bool(root.get(key, root.get("valid", False)))


def _relative_soft_limits_from_motion(config: dict[str, Any], side: str) -> dict[str, Any]:
    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    relative = motion.get("relativeSoftLimits", {}) if isinstance(motion, dict) else {}
    raw_side = relative.get(side) if isinstance(relative, dict) else None
    fallback = _relative_soft_limits_from_teleop(config, side)
    if not isinstance(raw_side, dict):
        return fallback
    normalized: dict[str, Any] = {}
    try:
        for axis_key in AXIS_KEYS:
            raw_axis = raw_side.get(axis_key)
            if not isinstance(raw_axis, dict):
                raise ValueError(axis_key)
            normalized[axis_key] = {
                "min": float(raw_axis["min"]),
                "max": float(raw_axis["max"]),
            }
    except (KeyError, TypeError, ValueError):
        return fallback
    return normalized


def _ensure_home_reference_model(config: dict[str, Any], has_current_home_reference_strategy: bool) -> None:
    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    if not isinstance(motion, dict):
        return
    origin = motion.get("origin", {}) if isinstance(motion.get("origin"), dict) else {}
    raw_reference = motion.get("homeReference") if has_current_home_reference_strategy else {}
    raw_offset = motion.get("workOriginOffset") if has_current_home_reference_strategy else {}
    reference = raw_reference if isinstance(raw_reference, dict) else {}
    offset = raw_offset if isinstance(raw_offset, dict) else {}
    default_reference = ICF_WORK_ORIGIN_DEFAULTS
    default_offset = ICF_WORK_ORIGIN_OFFSET_DEFAULTS
    updated_at = reference.get("updatedAt", origin.get("updatedAt", default_reference["updatedAt"]))
    offset_updated_at = offset.get("updatedAt", origin.get("updatedAt", default_offset["updatedAt"]))
    try:
        updated_at = int(updated_at)
    except (TypeError, ValueError):
        updated_at = int(default_reference["updatedAt"])
    try:
        offset_updated_at = int(offset_updated_at)
    except (TypeError, ValueError):
        offset_updated_at = int(default_offset["updatedAt"])

    next_reference: dict[str, Any] = {"updatedAt": updated_at}
    next_offset: dict[str, Any] = {"updatedAt": offset_updated_at}
    for side in ("left", "right"):
        pulse_key = "leftPulse" if side == "left" else "rightPulse"
        delta_key = "leftPulseDelta" if side == "left" else "rightPulseDelta"
        valid_key = "leftValid" if side == "left" else "rightValid"
        default_pulse = list(default_reference[pulse_key])
        if has_current_home_reference_strategy:
            side_reference = _six_float_list(reference.get(pulse_key), default_pulse)
            side_offset = _six_float_list(offset.get(delta_key), list(default_offset[delta_key]))
            side_reference_valid = _side_valid(reference, side)
            side_offset_valid = _side_valid(offset, side)
        else:
            side_reference = _six_float_list(origin.get(pulse_key), default_pulse)
            side_offset = [0.0] * 6
            side_reference_valid = _side_valid(origin, side)
            side_offset_valid = side_reference_valid
        next_reference[pulse_key] = side_reference
        next_reference[valid_key] = side_reference_valid
        next_offset[delta_key] = side_offset
        next_offset[valid_key] = side_offset_valid
    next_reference["valid"] = bool(next_reference["leftValid"] and next_reference["rightValid"])
    next_offset["valid"] = bool(next_offset["leftValid"] and next_offset["rightValid"])
    motion["homeReferenceVersion"] = ICF_HOME_REFERENCE_VERSION
    motion["homeReference"] = next_reference
    motion["workOriginOffset"] = next_offset

    motion["relativeSoftLimits"] = {
        "left": (
            _relative_soft_limits_from_motion(config, "left")
            if has_current_home_reference_strategy
            else _relative_soft_limits_from_teleop(config, "left")
        ),
        "right": (
            _relative_soft_limits_from_motion(config, "right")
            if has_current_home_reference_strategy
            else _relative_soft_limits_from_teleop(config, "right")
        ),
    }
    for side in ("left", "right"):
        if not isinstance(motion["relativeSoftLimits"].get(side), dict):
            motion["relativeSoftLimits"][side] = json.loads(json.dumps(ICF_RELATIVE_SOFT_LIMIT_DEFAULTS[side]))


def _motion_card_no(config: dict[str, Any], side: str) -> int:
    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    fallback = 1 if side == "left" else 0
    try:
        return int(motion.get(f"{side}CardNo", fallback))
    except (TypeError, ValueError):
        return fallback


def _origin_validation_axes(config: dict[str, Any], side: str) -> tuple[bool, bool, bool, bool, bool, bool]:
    return CARD0_YAW_DISABLED_AXES if _motion_card_no(config, side) == 0 else (True, True, True, True, True, True)


def _origin_outside_effective_limits(config: dict[str, Any], side: str) -> bool:
    origin = side_origin_ui(config, side)
    if origin is None:
        return False
    try:
        limits = effective_limits_ui(config, side)
    except WorkOriginMissing:
        return False
    enabled_axes = _origin_validation_axes(config, side)
    for axis_index in range(3, 6):
        if not enabled_axes[axis_index]:
            continue
        limit = limits[axis_index]
        target = origin[axis_index]
        if limit.min > limit.max or target < limit.min or target > limit.max:
            return True
    return False


def _reanchor_stale_origin_windows(config: dict[str, Any]) -> None:
    for side in ("left", "right"):
        if _origin_outside_effective_limits(config, side):
            reanchor_motion_soft_limits_to_current_origin(config, side)


def _invalidate_origin_sides_outside_effective_limits(config: dict[str, Any]) -> None:
    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    origin = motion.get("origin", {}) if isinstance(motion, dict) else {}
    offset = motion.get("workOriginOffset", {}) if isinstance(motion, dict) else {}
    if not isinstance(origin, dict):
        return
    for side in ("left", "right"):
        valid_key = "leftValid" if side == "left" else "rightValid"
        if not bool(origin.get(valid_key, origin.get("valid", False))):
            continue
        if not _origin_outside_effective_limits(config, side):
            continue
        origin[valid_key] = False
        if isinstance(offset, dict):
            offset[valid_key] = False
    origin["valid"] = bool(origin.get("leftValid", False) and origin.get("rightValid", False))
    if isinstance(offset, dict):
        offset["valid"] = bool(offset.get("leftValid", False) and offset.get("rightValid", False))


def sync_rotation_work_limits_from_relative_soft_limits(config: dict[str, Any], side: str | None = None) -> None:
    """Keep relative rotation work windows in sync without rewriting mechanical soft limits."""
    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    if not isinstance(motion, dict):
        return
    work_limits = motion.get("rotationWorkLimits", {})
    next_work_limits = dict(work_limits) if isinstance(work_limits, dict) else {}
    next_work_limits["enabled"] = bool(next_work_limits.get("enabled", True))
    sides = ("left", "right") if side is None else (side,)
    for active_side in sides:
        if active_side not in {"left", "right"}:
            continue
        current_side = next_work_limits.get(active_side)
        next_side = dict(current_side) if isinstance(current_side, dict) else {}
        relative = _relative_soft_limits_from_motion(config, active_side)
        try:
            for axis_index, axis_key in enumerate(AXIS_KEYS[3:], start=3):
                if isinstance(next_side.get(axis_key), dict):
                    continue
                raw_axis = relative[axis_key]
                next_side[axis_key] = {
                    "min": config_limit_to_ui(raw_axis["min"], axis_index),
                    "max": config_limit_to_ui(raw_axis["max"], axis_index),
                }
        except (KeyError, TypeError, ValueError):
            continue
        next_work_limits[active_side] = next_side
    motion["rotationWorkLimits"] = next_work_limits


def reanchor_motion_soft_limits_to_current_origin(config: dict[str, Any], side: str | None = None) -> None:
    """Compatibility wrapper; mechanical soft limits stay stable."""
    sync_rotation_work_limits_from_relative_soft_limits(config, side)


def _float_close(value: Any, expected: float, tolerance: float = 1e-6) -> bool:
    try:
        return abs(float(value) - expected) <= tolerance
    except (TypeError, ValueError):
        return False


def _normalize_card0_yaw_disabled(config: dict[str, Any]) -> None:
    teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
    if not isinstance(teleop, dict):
        return
    for side in ("left", "right"):
        key = f"{side}EnabledAxes"
        raw = teleop.get(key)
        if isinstance(raw, list) and len(raw) >= 6:
            axes = [bool(value) for value in raw[:6]]
        else:
            axes = [True] * 6
        if _motion_card_no(config, side) == 0:
            axes[5] = False
        elif side == "right" and axes == [True, True, True, True, True, False]:
            axes[5] = True
        teleop[key] = axes


def _normalize_left_yaw_window(config: dict[str, Any]) -> None:
    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    work_limits = motion.get("rotationWorkLimits", {}) if isinstance(motion, dict) else {}
    left_work = work_limits.get("left", {}) if isinstance(work_limits, dict) else {}
    left_yaw_work = left_work.get("yaw", {}) if isinstance(left_work, dict) else {}
    if (
        isinstance(left_yaw_work, dict)
        and _float_close(left_yaw_work.get("min"), -7.0)
        and _float_close(left_yaw_work.get("max"), 7.0)
    ):
        return


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
        mins[3] = -95.0
        maxes[3] = 5.0

    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    work_limits = motion.get("rotationWorkLimits", {}) if isinstance(motion, dict) else {}
    right_work = work_limits.get("right", {}) if isinstance(work_limits, dict) else {}
    right_roll_work = right_work.get("roll", {}) if isinstance(right_work, dict) else {}
    if (
        isinstance(right_roll_work, dict)
        and _float_close(right_roll_work.get("min"), -100.0)
        and (_float_close(right_roll_work.get("max"), 0.0) or _float_close(right_roll_work.get("max"), 100.0))
    ):
        right_roll_work["min"] = -95.0
        right_roll_work["max"] = 5.0


def _normalize_right_pitch_window(config: dict[str, Any]) -> None:
    teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
    mins = teleop.get("rightSoftLimitMin") if isinstance(teleop, dict) else None
    maxes = teleop.get("rightSoftLimitMax") if isinstance(teleop, dict) else None
    if isinstance(mins, list) and isinstance(maxes, list) and len(mins) >= 5 and len(maxes) >= 5:
        mins[4] = -30.0
        maxes[4] = 30.0

    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    work_limits = motion.get("rotationWorkLimits", {}) if isinstance(motion, dict) else {}
    right_work = work_limits.get("right", {}) if isinstance(work_limits, dict) else {}
    right_pitch_work = right_work.get("pitch", {}) if isinstance(right_work, dict) else {}
    if isinstance(right_pitch_work, dict):
        right_pitch_work["min"] = -30.0
        right_pitch_work["max"] = 30.0


def _default_rotation_work_limits_present(work_limits: dict[str, Any]) -> bool:
    for side in ("left", "right"):
        raw_side = work_limits.get(side)
        default_side = ICF_ROTATION_WORK_LIMIT_DEFAULTS[side]
        if not isinstance(raw_side, dict):
            return False
        for axis_key in ("roll", "pitch", "yaw"):
            raw_axis = raw_side.get(axis_key)
            default_axis = default_side[axis_key]
            if not isinstance(raw_axis, dict):
                return False
            if not _float_close(raw_axis.get("min"), default_axis["min"]):
                return False
            if not _float_close(raw_axis.get("max"), default_axis["max"]):
                return False
    return True


def _reenable_default_rotation_work_limits(config: dict[str, Any]) -> None:
    motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
    if not isinstance(motion, dict):
        return
    work_limits = motion.get("rotationWorkLimits", {})
    if not isinstance(work_limits, dict) or bool(work_limits.get("enabled", False)):
        return
    if motion.get("leftSoftLimits") != ICF_LEFT_MOTION_MECHANICAL_LIMITS:
        return
    if motion.get("rightSoftLimits") != ICF_RIGHT_MOTION_MECHANICAL_LIMITS:
        return
    if not _default_rotation_work_limits_present(work_limits):
        return
    work_limits["enabled"] = True


def _normalize_camera_tuning_defaults(config: dict[str, Any], has_current_camera_tuning_defaults: bool) -> None:
    cameras = config.get("cameras", {}) if isinstance(config.get("cameras"), dict) else {}
    if not isinstance(cameras, dict):
        return
    if has_current_camera_tuning_defaults:
        return
    tuning = cameras.get("tuning", {})
    if not isinstance(tuning, dict):
        tuning = {}
        cameras["tuning"] = tuning
    default_tuning = ICF_CAMERA_DEFAULTS["tuning"]
    for role, default_profile in default_tuning.items():
        profile = tuning.get(role)
        if not isinstance(profile, dict):
            tuning[role] = json.loads(json.dumps(default_profile))
            continue
        if (
            profile.get("autoExposure") is False
            and profile.get("autoWhiteBalance") is False
            and _float_close(profile.get("exposure"), default_profile["exposure"])
            and _float_close(profile.get("gain"), default_profile["gain"])
        ):
            profile["autoExposure"] = True
            profile["autoWhiteBalance"] = True
    cameras["tuningDefaultsVersion"] = ICF_CAMERA_TUNING_DEFAULTS_VERSION


class SettingsService:
    def __init__(self, runtime_dir: Path, logs: LogService) -> None:
        self.runtime_dir = runtime_dir
        self.config_path = runtime_dir / "config.json"
        self.snapshot_dir = runtime_dir / "snapshots"
        self.work_origin_backup_dir = runtime_dir / "_work_origin_backups"
        self.logs = logs
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.work_origin_backup_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.save_config(default_config(), emit_log=False)

    def get_config(self) -> dict[str, Any]:
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            raw_teleop = data.get("teleop", {}) if isinstance(data, dict) else {}
            raw_motion = data.get("motion", {}) if isinstance(data, dict) else {}
            raw_cameras = data.get("cameras", {}) if isinstance(data, dict) else {}
            has_current_teleop_strategy = (
                isinstance(raw_teleop, dict)
                and raw_teleop.get("strategyVersion") == ICF_TELEOP_STRATEGY_VERSION
            )
            has_current_work_origin_strategy = (
                isinstance(raw_motion, dict)
                and raw_motion.get("workOriginStrategyVersion") == ICF_WORK_ORIGIN_VERSION
            )
            has_current_home_reference_strategy = (
                isinstance(raw_motion, dict)
                and raw_motion.get("homeReferenceVersion") == ICF_HOME_REFERENCE_VERSION
            )
            has_current_camera_tuning_defaults = (
                isinstance(raw_cameras, dict)
                and raw_cameras.get("tuningDefaultsVersion") == ICF_CAMERA_TUNING_DEFAULTS_VERSION
            )
            merged = self._migrate_config(
                self._merge_defaults(data),
                has_current_teleop_strategy,
                has_current_work_origin_strategy,
                has_current_home_reference_strategy,
                has_current_camera_tuning_defaults,
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
            _normalize_card0_yaw_disabled(config)
            _normalize_right_pitch_window(config)
            raw_motion = config.get("motion", {}) if isinstance(config.get("motion"), dict) else {}
            _ensure_home_reference_model(
                config,
                isinstance(raw_motion, dict)
                and raw_motion.get("homeReferenceVersion") == ICF_HOME_REFERENCE_VERSION,
            )
        validated = AppConfig.model_validate(config).model_dump(mode="json")
        old_hash = stable_config_hash(old_config) if old_config else "-"
        new_hash = stable_config_hash(validated)
        self._backup_current_work_origin(old_config)
        self._atomic_write_json(self.config_path, validated)
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

    def _backup_current_work_origin(self, config: dict[str, Any]) -> None:
        motion = config.get("motion") if isinstance(config, dict) else None
        if not isinstance(motion, dict):
            return
        origin = motion.get("origin")
        if not isinstance(origin, dict):
            return
        if not bool(origin.get("valid") or origin.get("leftValid") or origin.get("rightValid")):
            return
        origin_copy = json.loads(json.dumps(origin))
        origin_hash = stable_config_hash({"origin": origin_copy})
        backup_path = self.work_origin_backup_dir / f"work-origin-{origin_hash[:12]}.json"
        if backup_path.exists():
            return
        payload: dict[str, Any] = {
            "createdAt": now_ms(),
            "sourceConfigPath": str(self.config_path),
            "sourceConfigHash": stable_config_hash(config),
            "originHash": origin_hash,
            "origin": origin_copy,
        }
        home_reference = motion.get("homeReference")
        if isinstance(home_reference, dict):
            payload["homeReference"] = json.loads(json.dumps(home_reference))
        work_origin_offset = motion.get("workOriginOffset")
        if isinstance(work_origin_offset, dict):
            payload["workOriginOffset"] = json.loads(json.dumps(work_origin_offset))
        self._atomic_write_json(backup_path, payload)

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f"{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_name = temp_file.name
                temp_file.write(json.dumps(payload, ensure_ascii=False, indent=2))
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_name, path)
        finally:
            if temp_name is not None:
                temp_path = Path(temp_name)
                if temp_path.exists():
                    temp_path.unlink(missing_ok=True)

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
        has_current_home_reference_strategy: bool,
        has_current_camera_tuning_defaults: bool,
    ) -> dict[str, Any]:
        teleop = config.get("teleop", {})
        if isinstance(teleop, dict) and not has_current_teleop_strategy:
            for key, value in ICF_TELEOP_DEFAULTS.items():
                teleop[key] = json.loads(json.dumps(value))
            teleop["leftGravityCompensation"] = True
            teleop["rightGravityCompensation"] = True
            teleop["leftForceFeedback"] = True
            teleop["rightForceFeedback"] = True
            teleop["leftGravityScale"] = ICF_TELEOP_DEFAULTS["leftGravityScale"]
            teleop["rightGravityScale"] = ICF_TELEOP_DEFAULTS["rightGravityScale"]
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
                gripper_teleop["gripTorque"] = 1
                gripper_teleop["positionDeadbandCounts"] = 1
                gripper_teleop["minCommandIntervalMs"] = 20
                gripper_teleop["autoGapCalibration"] = True
                gripper_teleop["autoGapMinSpanMm"] = 2.0
                gripper_teleop["autoGapMarginMm"] = 1.0
                gripper_teleop["leftSourceHand"] = "PhysicalLeft"
                gripper_teleop["rightSourceHand"] = "PhysicalRight"
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
                scale_defaults = (
                    float(teleop.get("leftTranslationScale", 0.0)),
                    float(teleop.get("rightTranslationScale", 0.0)),
                    float(teleop.get("leftRotationScale", 0.0)),
                    float(teleop.get("rightRotationScale", 0.0)),
                )
            except (TypeError, ValueError):
                scale_defaults = (0.0, 0.0, 0.0, 0.0)
            if scale_defaults == (1.25, 1.25, 1.20, 1.20):
                teleop["leftTranslationScale"] = ICF_TELEOP_DEFAULTS["leftTranslationScale"]
                teleop["rightTranslationScale"] = ICF_TELEOP_DEFAULTS["rightTranslationScale"]
                teleop["leftRotationScale"] = ICF_TELEOP_DEFAULTS["leftRotationScale"]
                teleop["rightRotationScale"] = ICF_TELEOP_DEFAULTS["rightRotationScale"]
            try:
                translation_speed = (
                    float(teleop.get("translationStartVelocityUmS", 0.0)),
                    float(teleop.get("translationMaxVelocityUmS", 0.0)),
                )
            except (TypeError, ValueError):
                translation_speed = (0.0, 0.0)
            if translation_speed in {(400.0, 5000.0), (600.0, 8000.0), (900.0, 12000.0)}:
                teleop["translationStartVelocityUmS"] = ICF_TELEOP_DEFAULTS["translationStartVelocityUmS"]
                teleop["translationMaxVelocityUmS"] = ICF_TELEOP_DEFAULTS["translationMaxVelocityUmS"]
            try:
                rotation_speed = (
                    float(teleop.get("rotationStartVelocityDegS", 0.0)),
                    float(teleop.get("rotationMaxVelocityDegS", 0.0)),
                )
            except (TypeError, ValueError):
                rotation_speed = (0.0, 0.0)
            if rotation_speed in {(1.0, 12.0), (1.5, 18.0)}:
                teleop["rotationStartVelocityDegS"] = ICF_TELEOP_DEFAULTS["rotationStartVelocityDegS"]
                teleop["rotationMaxVelocityDegS"] = ICF_TELEOP_DEFAULTS["rotationMaxVelocityDegS"]
            try:
                profile_times = (
                    float(teleop.get("motionProfileAccSec", 0.0)),
                    float(teleop.get("motionProfileDecSec", 0.0)),
                )
            except (TypeError, ValueError):
                profile_times = (0.0, 0.0)
            if profile_times in {(0.05, 0.05), (0.04, 0.04)}:
                teleop["motionProfileAccSec"] = ICF_TELEOP_DEFAULTS["motionProfileAccSec"]
                teleop["motionProfileDecSec"] = ICF_TELEOP_DEFAULTS["motionProfileDecSec"]
            if teleop.get("leftAxisOutputScale") == [0.65, 0.45, 0.45, 0.60, 0.16, 0.20]:
                teleop["leftAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["leftAxisOutputScale"]))
            if teleop.get("rightAxisOutputScale") == [0.65, 0.45, 0.45, 0.55, 0.16, 0.25]:
                teleop["rightAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["rightAxisOutputScale"]))
            if teleop.get("leftAxisOutputScale") == [0.40, 0.25, 0.25, 0.40, 0.20, 0.20]:
                teleop["leftAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["leftAxisOutputScale"]))
            if teleop.get("rightAxisOutputScale") == [0.40, 0.25, 0.25, 0.40, 0.20, 0.20]:
                teleop["rightAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["rightAxisOutputScale"]))
            if teleop.get("leftAxisOutputScale") == [0.40, 0.25, 0.25, 0.40, 0.10, 0.15]:
                teleop["leftAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["leftAxisOutputScale"]))
            if teleop.get("rightAxisOutputScale") == [0.40, 0.25, 0.25, 0.40, 0.10, 0.15]:
                teleop["rightAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["rightAxisOutputScale"]))
            if teleop.get("leftAxisOutputScale") == [0.40, 0.25, 0.25, 0.40, 0.08, 0.10]:
                teleop["leftAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["leftAxisOutputScale"]))
            if teleop.get("rightAxisOutputScale") == [0.40, 0.25, 0.25, 0.35, 0.08, 0.15]:
                teleop["rightAxisOutputScale"] = json.loads(json.dumps(ICF_TELEOP_DEFAULTS["rightAxisOutputScale"]))
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
            if float(teleop.get("translationPulseDeadband", 0.0)) == 2:
                teleop["translationPulseDeadband"] = ICF_TELEOP_DEFAULTS["translationPulseDeadband"]
            if float(teleop.get("rotationPulseDeadband", 0.0)) == 2:
                teleop["rotationPulseDeadband"] = ICF_TELEOP_DEFAULTS["rotationPulseDeadband"]
            if float(teleop.get("translationInputEpsilon", 0.0)) in {0.000005, 0.00002}:
                teleop["translationInputEpsilon"] = ICF_TELEOP_DEFAULTS["translationInputEpsilon"]
            if float(teleop.get("rotationInputEpsilon", 0.0)) in {0.03, 0.08, 0.12}:
                teleop["rotationInputEpsilon"] = ICF_TELEOP_DEFAULTS["rotationInputEpsilon"]
            if float(teleop.get("translationMinActivePulse", 0.0)) == 3:
                teleop["translationMinActivePulse"] = ICF_TELEOP_DEFAULTS["translationMinActivePulse"]
            if float(teleop.get("rotationMinActivePulse", 0.0)) == 3:
                teleop["rotationMinActivePulse"] = ICF_TELEOP_DEFAULTS["rotationMinActivePulse"]
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
            has_previous_imx258_camera_defaults = (
                cameras.get("global") == "AR0234 / index 1"
                and cameras.get("wristLeft") == "IMX258 / index 2"
                and cameras.get("wristRight") == "IMX258 / index 0"
            )
            has_previous_imx335_camera_defaults = (
                cameras.get("global") == "IMX335 / index 1"
                and cameras.get("wristLeft") == "IMX335 / index 2"
                and cameras.get("wristRight") == "IMX335 / index 0"
            )
            if (
                has_legacy_reversed_wrist_cameras
                or has_legacy_cyclic_camera_roles
                or has_previous_imx258_camera_defaults
                or has_previous_imx335_camera_defaults
            ):
                for key, value in ICF_CAMERA_DEFAULTS.items():
                    if key == "tuning":
                        continue
                    cameras[key] = json.loads(json.dumps(value))
            _normalize_camera_tuning_defaults(config, has_current_camera_tuning_defaults)
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
            if motion.get("leftSoftLimits") == ICF_LEFT_MOTION_LEGACY_ANCHORED_LIMITS:
                motion["leftSoftLimits"] = json.loads(json.dumps(ICF_LEFT_MOTION_MECHANICAL_LIMITS))
            if motion.get("rightSoftLimits") == ICF_RIGHT_MOTION_LEGACY_ANCHORED_LIMITS:
                motion["rightSoftLimits"] = json.loads(json.dumps(ICF_RIGHT_MOTION_MECHANICAL_LIMITS))
        if isinstance(motion, dict) and not has_current_work_origin_strategy:
            origin = motion.get("origin", {})
            origin_valid = isinstance(origin, dict) and bool(origin.get("valid", False))
            if not origin_valid:
                motion["origin"] = json.loads(json.dumps(ICF_WORK_ORIGIN_DEFAULTS))
            left_limits = motion.get("leftSoftLimits")
            right_limits = motion.get("rightSoftLimits")
            left_limits = left_limits if isinstance(left_limits, dict) else ICF_LEFT_MOTION_SOFT_LIMITS
            right_limits = right_limits if isinstance(right_limits, dict) else ICF_RIGHT_MOTION_SOFT_LIMITS
            motion["rotationWorkLimits"] = rotation_work_limits_from_soft_limits(left_limits, right_limits)
            motion["leftSoftLimits"] = json.loads(json.dumps(ICF_LEFT_MOTION_MECHANICAL_LIMITS))
            motion["rightSoftLimits"] = json.loads(json.dumps(ICF_RIGHT_MOTION_MECHANICAL_LIMITS))
            motion["workOriginStrategyVersion"] = ICF_WORK_ORIGIN_VERSION
        if isinstance(motion, dict):
            motion.setdefault("rotationWorkLimits", json.loads(json.dumps(ICF_ROTATION_WORK_LIMIT_DEFAULTS)))
            _ensure_home_reference_model(config, has_current_home_reference_strategy)
        _normalize_card0_yaw_disabled(config)
        _normalize_left_yaw_window(config)
        _normalize_right_roll_window(config)
        _normalize_right_pitch_window(config)
        _reenable_default_rotation_work_limits(config)
        if isinstance(motion, dict):
            reanchor_motion_soft_limits_to_current_origin(config)
            if has_current_home_reference_strategy:
                _invalidate_origin_sides_outside_effective_limits(config)
        if not has_current_home_reference_strategy:
            _reanchor_stale_origin_windows(config)
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
            if (
                gripper_teleop.get("leftSourceHand") == "PhysicalRight"
                and gripper_teleop.get("rightSourceHand") == "PhysicalLeft"
            ):
                gripper_teleop["leftSourceHand"] = "PhysicalLeft"
                gripper_teleop["rightSourceHand"] = "PhysicalRight"
            gripper_teleop.setdefault("leftSourceHand", "PhysicalLeft")
            gripper_teleop.setdefault("rightSourceHand", "PhysicalRight")
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
