from __future__ import annotations

from typing import Any

DEFAULT_ICF_TARGET_MIN_GAP_MM = 1.02


def gripper_stroke_mm(config: dict[str, Any]) -> float:
    gripper = config.get("gripper", {}) if isinstance(config.get("gripper"), dict) else {}
    return max(0.001, float(gripper.get("strokeMm", 26.0)))


def icf_target_protection_enabled(config: dict[str, Any]) -> bool:
    gripper = config.get("gripper", {}) if isinstance(config.get("gripper"), dict) else {}
    return bool(gripper.get("icfTargetProtectionEnabled", True))


def icf_target_min_gap_mm(config: dict[str, Any]) -> float:
    if not icf_target_protection_enabled(config):
        return 0.0
    gripper = config.get("gripper", {}) if isinstance(config.get("gripper"), dict) else {}
    stroke = gripper_stroke_mm(config)
    raw = float(gripper.get("icfTargetMinGapMm", DEFAULT_ICF_TARGET_MIN_GAP_MM))
    return min(max(raw, 0.0), stroke)


def protected_gripper_target_mm(config: dict[str, Any], target_mm: float) -> float:
    stroke = gripper_stroke_mm(config)
    target = min(max(float(target_mm), 0.0), stroke)
    return min(max(target, icf_target_min_gap_mm(config)), stroke)


def protected_gripper_target_mm_from_values(
    target_mm: float,
    stroke_mm: float,
    protection_enabled: bool,
    min_gap_mm: float,
) -> float:
    stroke = max(0.001, float(stroke_mm))
    target = min(max(float(target_mm), 0.0), stroke)
    if not protection_enabled:
        return target
    min_gap = min(max(float(min_gap_mm), 0.0), stroke)
    return min(max(target, min_gap), stroke)
