from __future__ import annotations
# ruff: noqa: E402, I001

import argparse
import configparser
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_CONFIG = REPO_ROOT / "backend" / "runtime" / "config.json"
DEFAULT_ICF_CONFIG = Path("F:/ICFNewProject/QSerialTest3.0/QSerialTest/QSerialTest/config.ini")

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.defaults import (  # noqa: E402
    ICF_KINEMATICS_DEFAULTS,
    ICF_TELEOP_STRATEGY_VERSION,
    default_config,
)
from backend.core.schemas import AppConfig  # noqa: E402


AXIS_ORDER = ["x", "y", "z", "roll", "pitch", "yaw"]
TELEOP_SOFT_LIMIT_UNIT_SPEC = ["um", "um", "um", "deg", "deg", "deg"]
TRANSLATION_TRAVEL_UM = [50000.0, 75000.0, 75000.0]
ROLL_PITCH_LIMIT_DEG = 100.0
YAW_LIMIT_DEG = 7.0
LOGICAL_TO_PHYSICAL_AXIS = {
    0: 0,
    1: 1,
    2: 2,
    3: 3,
    4: 4,
    5: 5,
    6: 0,
    7: 1,
    8: 2,
    9: 3,
    10: 4,
    11: 5,
    12: 6,
    13: 7,
    14: 8,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import ICF teleop settings into E2E config.json.")
    parser.add_argument("--icf-config", type=Path, default=DEFAULT_ICF_CONFIG)
    parser.add_argument("--runtime-config", type=Path, default=DEFAULT_RUNTIME_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    icf_config = args.icf_config.resolve()
    runtime_config = args.runtime_config.resolve()
    if not icf_config.exists():
        raise FileNotFoundError(icf_config)

    parser_ini = configparser.ConfigParser()
    parser_ini.optionxform = str
    parser_ini.read(icf_config, encoding="utf-8-sig")
    config = load_runtime_config(runtime_config)
    apply_icf_config(config, parser_ini, icf_config)
    validated = AppConfig.model_validate(config).model_dump(mode="json")
    payload = json.dumps(validated, ensure_ascii=False, indent=2)
    if args.dry_run:
        print(payload)
        return 0
    runtime_config.write_text(payload + "\n", encoding="utf-8")
    print(f"Imported ICF teleop config from {icf_config} into {runtime_config}")
    return 0


def load_runtime_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_config()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_config()


def apply_icf_config(config: dict[str, Any], ini: configparser.ConfigParser, source_path: Path) -> None:
    teleop = config.setdefault("teleop", {})
    motion = config.setdefault("motion", {})
    safety = ini["Safety"]
    teleop_ini = ini["Teleop"]
    kinematics_ini = ini["Kinematics"]
    work_origin_ini = ini["WorkOrigin"] if ini.has_section("WorkOrigin") else None

    left_axis_map = int_array(value(teleop_ini, "LeftAxisMap", "0,1,3,5,4,2"))
    right_axis_map = int_array(value(teleop_ini, "RightAxisMap", "8,6,11,14,7,13"))
    left_pulse_per_unit = float_array(
        value(kinematics_ini, "LeftPulsePerUnit", "5000,10000,10000,1666.666667,2500,3333.333333")
    )
    right_pulse_per_unit = float_array(
        value(kinematics_ini, "RightPulsePerUnit", "5000,10000,10000,1666.666667,2500,3333.333333")
    )
    left_direction = int_array(value(teleop_ini, "LeftDirectionSign", "-1,-1,-1,1,1,1"))
    right_direction = int_array(value(teleop_ini, "RightDirectionSign", "-1,1,-1,1,-1,-1"))
    left_signed = signed_pulse_per_unit(left_pulse_per_unit, left_direction)
    right_signed = signed_pulse_per_unit(right_pulse_per_unit, right_direction)
    left_origin = float_array(value(work_origin_ini, "LeftOriginPulse", "0,0,0,0,0,0")) if work_origin_ini else [0.0] * 6
    right_origin = float_array(value(work_origin_ini, "RightOriginPulse", "0,0,0,0,0,0")) if work_origin_ini else [0.0] * 6

    motion["kinematics"] = {
        **ICF_KINEMATICS_DEFAULTS,
        "source": str(source_path),
        "axisOrder": AXIS_ORDER,
        "leftAxisMap": left_axis_map,
        "rightAxisMap": right_axis_map,
        "leftPhysicalAxis": physical_axis_map(left_axis_map),
        "rightPhysicalAxis": physical_axis_map(right_axis_map),
        "axisUnitSpec": str_array(value(kinematics_ini, "AxisUnitSpec", "mm,mm,mm,deg,deg,deg")),
        "leftPulsePerUnit": left_pulse_per_unit,
        "rightPulsePerUnit": right_pulse_per_unit,
        "leftDirectionSign": left_direction,
        "rightDirectionSign": right_direction,
        "leftSignedPulsePerUnit": left_signed,
        "rightSignedPulsePerUnit": right_signed,
        "syncActionPulseCoeff": bool_value(value(kinematics_ini, "SyncActionPulseCoeff", "true")),
        "updatedAt": value(kinematics_ini, "UpdatedAt", ICF_KINEMATICS_DEFAULTS["updatedAt"]),
    }
    if work_origin_ini:
        motion["origin"] = {
            "valid": bool_value(value(work_origin_ini, "Valid", "true")),
            "leftValid": bool_value(value(work_origin_ini, "Valid", "true")),
            "rightValid": bool_value(value(work_origin_ini, "Valid", "true")),
            "leftPulse": left_origin,
            "rightPulse": right_origin,
            "updatedAt": motion.get("origin", {}).get("updatedAt", 0),
            "previousValid": bool_value(value(work_origin_ini, "PreviousValid", "false")),
            "previousLeftPulse": float_array(value(work_origin_ini, "PreviousLeftOriginPulse", "0,0,0,0,0,0")),
            "previousRightPulse": float_array(value(work_origin_ini, "PreviousRightOriginPulse", "0,0,0,0,0,0")),
            "previousUpdatedAt": motion.get("origin", {}).get("previousUpdatedAt", 0),
        }

    teleop.update(
        {
            "strategyVersion": ICF_TELEOP_STRATEGY_VERSION,
            "swapHands": bool_value(value(teleop_ini, "LocalSwapHands", "false")),
            "swapTeleopChannels": bool_value(value(teleop_ini, "LocalSwapTeleopChannels", "true")),
            "stabilityMode": value(teleop_ini, "LocalStabilityMode", "hold").lower(),
            "leftOpenId": int(value(teleop_ini, "LocalLeftOpenId", "0")),
            "rightOpenId": int(value(teleop_ini, "LocalRightOpenId", "1")),
            "leftTranslationScale": float(value(teleop_ini, "LeftTranslationOutputScale", "0.30")),
            "rightTranslationScale": float(value(teleop_ini, "RightTranslationOutputScale", "0.30")),
            "leftRotationScale": float(value(teleop_ini, "LeftRotationOutputScale", "0.1")),
            "rightRotationScale": float(value(teleop_ini, "RightRotationOutputScale", "0.1")),
            "leftAxisOutputScale": float_array(value(teleop_ini, "LeftAxisOutputScale", "0.20,0.20,0.2,0.25,0.25,1.5")),
            "rightAxisOutputScale": float_array(value(teleop_ini, "RightAxisOutputScale", "0.20,0.20,0.5,0.25,0.25,1.5")),
            "translationDeadzone": float(value(teleop_ini, "TranslationDeadzone", "0.00002")),
            "rotationDeadzone": float(value(teleop_ini, "RotationDeadzone", "0.05")),
            "incrementalTranslationMinEffectiveDelta": float(
                value(teleop_ini, "IncrementalTranslationMinEffectiveDelta", "0.00005")
            ),
            "incrementalTranslationReverseDeadzone": float(
                value(teleop_ini, "IncrementalTranslationReverseDeadzone", "0.00010")
            ),
            "translationStepLimitPulse": float(value(teleop_ini, "TranslationStepLimit", "4000")),
            "rotationStepLimitPulse": float(value(teleop_ini, "RotationStepLimit", "1250")),
            "translationPulseDeadband": float(value(teleop_ini, "TranslationPulseDeadband", "0")),
            "rotationPulseDeadband": float(value(teleop_ini, "RotationPulseDeadband", "0")),
            "translationStepUm": float(teleop.get("translationStepUm", 5000.0)),
            "rotationStepDeg": float(teleop.get("rotationStepDeg", 0.2)),
            "translationStartVelocityUmS": float(value(teleop_ini, "TranslationStartSpeedMmPerSec", "0.3")) * 1000.0,
            "translationMaxVelocityUmS": float(value(teleop_ini, "TranslationMaxSpeedMmPerSec", "4.0")) * 1000.0,
            "rotationStartVelocityDegS": float(value(teleop_ini, "RotationStartSpeedDegPerSec", "0.5")),
            "rotationMaxVelocityDegS": float(value(teleop_ini, "RotationMaxSpeedDegPerSec", "6.0")),
            "motionProfileAccSec": float(value(teleop_ini, "MotionProfileAccSec", "0.05")),
            "motionProfileDecSec": float(value(teleop_ini, "MotionProfileDecSec", "0.05")),
            "leftEnabledAxes": bool_array(value(safety, "TeleopLeftEnabledAxes", "1,1,1,1,1,1")),
            "rightEnabledAxes": bool_array(value(safety, "TeleopRightEnabledAxes", "1,1,1,1,1,1")),
            "softLimitUnitSpec": TELEOP_SOFT_LIMIT_UNIT_SPEC,
            "leftSoftLimitMin": converted_soft_limit(value(safety, "TeleopLeftSoftLimitMin", ""), left_signed, left_origin),
            "leftSoftLimitMax": converted_soft_limit(value(safety, "TeleopLeftSoftLimitMax", ""), left_signed, left_origin),
            "rightSoftLimitMin": converted_soft_limit(value(safety, "TeleopRightSoftLimitMin", ""), right_signed, right_origin),
            "rightSoftLimitMax": converted_soft_limit(value(safety, "TeleopRightSoftLimitMax", ""), right_signed, right_origin),
        }
    )
    normalize_teleop_limits(teleop, "left")
    normalize_teleop_limits(teleop, "right")
    apply_gripper_config(config, ini)
    apply_motion_profiles_and_limits(config)


def apply_gripper_config(config: dict[str, Any], ini: configparser.ConfigParser) -> None:
    gripper = config.setdefault("gripper", {})
    teleop = config.setdefault("teleop", {})
    gripper_teleop = teleop.setdefault("gripperTeleop", {})
    left = ini["JodellGripper"] if ini.has_section("JodellGripper") else None
    right = ini["JodellGripperLeft"] if ini.has_section("JodellGripperLeft") else None
    if left is not None:
        gripper["leftEnabled"] = bool_value(value(left, "Enable", "1"))
        gripper["leftPort"] = f"COM{int(value(left, 'Port', '8'))}"
        gripper["leftSlaveId"] = int(value(left, "SlaveId", "10"))
        gripper_teleop["leftSourceHand"] = value(left, "SourceHand", "PhysicalRight")
        gripper_teleop["leftGapMinMm"] = float(value(left, "OmegaGapClosedMm", "0"))
        gripper_teleop["leftGapMaxMm"] = float(value(left, "OmegaGapOpenMm", "25"))
    if right is not None:
        gripper["rightEnabled"] = bool_value(value(right, "Enable", "1"))
        gripper["rightPort"] = f"COM{int(value(right, 'Port', '9'))}"
        gripper["rightSlaveId"] = int(value(right, "SlaveId", "9"))
        gripper_teleop["rightSourceHand"] = value(right, "SourceHand", "PhysicalLeft")
        gripper_teleop["rightGapMinMm"] = float(value(right, "OmegaGapClosedMm", "0"))
        gripper_teleop["rightGapMaxMm"] = float(value(right, "OmegaGapOpenMm", "25"))
    reference = right or left
    if reference is None:
        return
    gripper["strokeMm"] = float(value(reference, "JodellStrokeMm", str(gripper.get("strokeMm", 26))))
    gripper_teleop["enabled"] = bool_value(value(reference, "Enable", "1"))
    gripper_teleop["loopHz"] = int(value(reference, "LoopHz", str(gripper_teleop.get("loopHz", 100))))
    gripper_teleop["gripSpeed"] = int(value(reference, "GripSpeed", "128"))
    gripper_teleop["gripTorque"] = int(value(reference, "GripTorque", "192"))
    gripper_teleop["positionDeadbandCounts"] = int(value(reference, "PositionDeadbandCounts", "2"))
    gripper_teleop["minCommandIntervalMs"] = int(value(reference, "MinCommandIntervalMs", "50"))
    gripper_teleop["autoGapCalibration"] = bool_value(value(reference, "AutoGapCalibration", "true"))
    gripper_teleop["autoGapMinSpanMm"] = float(value(reference, "AutoGapMinSpanMm", "2.0"))
    gripper_teleop["autoGapMarginMm"] = float(value(reference, "AutoGapMarginMm", "1.0"))
    gripper_teleop["buttonFallback"] = bool_value(value(reference, "ButtonFallback", "true"))
    gripper_teleop["diagLog"] = bool_value(value(reference, "DiagLog", "false"))


def apply_motion_profiles_and_limits(config: dict[str, Any]) -> None:
    motion = config.setdefault("motion", {})
    safety = config.setdefault("safety", {})
    translation_profile = {
        "startSpeed": float(config["teleop"]["translationStartVelocityUmS"]),
        "maxSpeed": float(config["teleop"]["translationMaxVelocityUmS"]),
        "accTimeSec": float(config["teleop"]["motionProfileAccSec"]),
        "decTimeSec": float(config["teleop"]["motionProfileDecSec"]),
    }
    rotation_profile = {
        "startSpeed": float(config["teleop"]["rotationStartVelocityDegS"]),
        "maxSpeed": float(config["teleop"]["rotationMaxVelocityDegS"]),
        "accTimeSec": float(config["teleop"]["motionProfileAccSec"]),
        "decTimeSec": float(config["teleop"]["motionProfileDecSec"]),
    }
    for side in ("left", "right"):
        motion[f"{side}Profile"] = {
            "translation": dict(translation_profile),
            "rotation": dict(rotation_profile),
        }
        motion[f"{side}SoftLimits"] = motion_soft_limits(config["teleop"], side)
    yaw_limit = max(
        abs(float(config["teleop"]["leftSoftLimitMin"][5])),
        abs(float(config["teleop"]["leftSoftLimitMax"][5])),
        abs(float(config["teleop"]["rightSoftLimitMin"][5])),
        abs(float(config["teleop"]["rightSoftLimitMax"][5])),
    )
    motion["yawSoftLimitDeg"] = clean_number(yaw_limit)
    safety["yawSoftLimitDeg"] = clean_number(yaw_limit)


def motion_soft_limits(teleop: dict[str, Any], side: str) -> dict[str, dict[str, float]]:
    mins = teleop[f"{side}SoftLimitMin"]
    maxes = teleop[f"{side}SoftLimitMax"]
    return {
        axis: {
            "min": clean_number(mins[index] * (1000.0 if index >= 3 else 1.0)),
            "max": clean_number(maxes[index] * (1000.0 if index >= 3 else 1.0)),
        }
        for index, axis in enumerate(AXIS_ORDER)
    }


def value(section: configparser.SectionProxy, key: str, fallback: Any) -> str:
    return strip_quotes(str(section.get(key, fallback)))


def strip_quotes(raw: str) -> str:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    return text


def str_array(raw: str) -> list[str]:
    return [item.strip() for item in strip_quotes(raw).split(",")]


def float_array(raw: str) -> list[float]:
    return [float(item.strip()) for item in strip_quotes(raw).split(",")]


def int_array(raw: str) -> list[int]:
    return [int(item.strip()) for item in strip_quotes(raw).split(",")]


def bool_array(raw: str) -> list[bool]:
    return [bool(int(item.strip())) for item in strip_quotes(raw).split(",")]


def bool_value(raw: str) -> bool:
    return strip_quotes(raw).lower() in {"1", "true", "yes", "on"}


def signed_pulse_per_unit(pulse_per_unit: list[float], direction: list[int]) -> list[float]:
    return [pulse_per_unit[index] * direction[index] for index in range(6)]


def physical_axis_map(logical_axes: list[int]) -> list[int]:
    return [LOGICAL_TO_PHYSICAL_AXIS[axis] for axis in logical_axes]


def converted_soft_limit(raw: str, signed_pulse_per_unit_values: list[float], origin_pulses: list[float]) -> list[float]:
    pulses = float_array(raw)
    result: list[float] = []
    for index, pulse in enumerate(pulses):
        value_ui = (pulse - origin_pulses[index]) / signed_pulse_per_unit_values[index]
        if index < 3:
            value_ui *= 1000.0
        result.append(clean_number(value_ui))
    return result


def normalize_teleop_limits(teleop: dict[str, Any], side: str) -> None:
    min_key = f"{side}SoftLimitMin"
    max_key = f"{side}SoftLimitMax"
    mins = list(teleop[min_key])
    maxes = list(teleop[max_key])
    for index, (min_value, max_value) in enumerate(zip(mins, maxes, strict=True)):
        mins[index] = min(min_value, max_value)
        maxes[index] = max(min_value, max_value)
        if index < len(TRANSLATION_TRAVEL_UM):
            half_travel = TRANSLATION_TRAVEL_UM[index] / 2.0
            if maxes[index] - mins[index] > TRANSLATION_TRAVEL_UM[index]:
                mins[index] = -half_travel
                maxes[index] = half_travel
        elif index in {3, 4}:
            mins[index] = -ROLL_PITCH_LIMIT_DEG
            maxes[index] = ROLL_PITCH_LIMIT_DEG
        elif index == 5:
            mins[index] = -YAW_LIMIT_DEG
            maxes[index] = YAW_LIMIT_DEG
    teleop[min_key] = mins
    teleop[max_key] = maxes


def clean_number(value: float) -> float:
    rounded = round(value, 4)
    integer = round(rounded)
    if abs(rounded - integer) < 0.0001:
        return float(integer)
    return rounded


if __name__ == "__main__":
    raise SystemExit(main())
