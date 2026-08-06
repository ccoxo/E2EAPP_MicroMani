from __future__ import annotations

import math
import os
from typing import Any


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _number(value: object, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _number_array(value: object, name: str, size: int, *, nonnegative: bool) -> list[float]:
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f"{name} must contain exactly {size} numbers")
    result = [_number(item, f"{name}[{index}]") for index, item in enumerate(value)]
    if nonnegative and any(item < 0 for item in result):
        raise ValueError(f"{name} values must be nonnegative")
    return result


def _force_axis_signs(config: dict[str, Any]) -> dict[str, list[float]]:
    force = _mapping(config.get("force"), "force")
    axis_sign = _mapping(force.get("axisSign"), "force.axisSign")
    result: dict[str, list[float]] = {}
    for side in ("left", "right"):
        values = _number_array(
            axis_sign.get(side),
            f"force.axisSign.{side}",
            6,
            nonnegative=False,
        )
        if any(value not in {-1.0, 1.0} for value in values):
            raise ValueError(f"force.axisSign.{side} values must be either -1 or 1")
        result[side] = values
    return result


def validate_force_config(config: dict[str, Any]) -> None:
    force = _mapping(config.get("force"), "force")
    safety = _mapping(config.get("safety"), "safety")
    source = str(force.get("source", "nidaq")).lower()
    if source not in {"nidaq", "hkvl_serial"}:
        raise ValueError("force.source must be nidaq or hkvl_serial")

    serial = _mapping(force.get("serial"), "force.serial")
    if source == "hkvl_serial":
        if str(serial.get("protocol", "")) != "hkvl_active_v1":
            raise ValueError("HKVL force protocol must be hkvl_active_v1")
        left_port = str(serial.get("leftPort", "")).strip().upper()
        right_port = str(serial.get("rightPort", "")).strip().upper()
        if not left_port or not right_port or left_port == right_port:
            raise ValueError("HKVL left and right serial ports must be present and different")
        if int(serial.get("baudrate", 0)) != 1_000_000:
            raise ValueError("HKVL serial baudrate must be fixed at 1 Mbps")
        if int(serial.get("expectedSampleHz", 0)) != 1000:
            raise ValueError("HKVL expected sample rate must be 1000 Hz")
        _force_axis_signs(config)

    thresholds = (
        ("fxy", "fxyWarnN", "fxyStopN", 30.0),
        ("fz", "fzWarnN", "fzStopN", 30.0),
        ("moment", "momentWarnNm", "momentStopNm", 1.0),
    )
    for label, warn_key, stop_key, upper_bound in thresholds:
        warn = _number(safety.get(warn_key), f"safety.{warn_key}")
        stop = _number(safety.get(stop_key), f"safety.{stop_key}")
        if not 0 < warn < stop <= upper_bound:
            raise ValueError(
                f"{label} thresholds must satisfy 0 < {warn_key} < {stop_key} <= {upper_bound:g}"
            )
    if _number(safety.get("watchdogMs"), "safety.watchdogMs") <= 0:
        raise ValueError("safety.watchdogMs must be positive")

    compliance = _mapping(force.get("compliance"), "force.compliance")
    enabled = bool(compliance.get("enabled", False))
    if enabled and source != "hkvl_serial":
        raise ValueError("force.compliance requires force.source=hkvl_serial")
    for side in ("left", "right"):
        side_config = _mapping(compliance.get(side), f"force.compliance.{side}")
        _number_array(
            side_config.get("matrix"),
            f"force.compliance.{side}.matrix",
            4,
            nonnegative=False,
        )
        for key in ("deadbandN", "gainUmPerNs", "maxStepUm", "maxOffsetUm"):
            _number_array(
                side_config.get(key),
                f"force.compliance.{side}.{key}",
                2,
                nonnegative=True,
            )
        if enabled and not bool(side_config.get("mappingConfirmed", False)):
            raise ValueError(
                f"force.compliance.{side}.mappingConfirmed must be true before enabling compliance"
            )


def hal_force_config_payload(config: dict[str, Any]) -> dict[str, Any]:
    validate_force_config(config)
    force = _mapping(config["force"], "force")
    serial = _mapping(force["serial"], "force.serial")
    safety = _mapping(config["safety"], "safety")
    compliance = _mapping(force["compliance"], "force.compliance")
    axis_signs = _force_axis_signs(config)
    source = str(force["source"]).lower()
    left_port = str(serial["leftPort"])
    right_port = str(serial["rightPort"])
    if source == "hkvl_serial":
        left_port = os.getenv("APPSTATION_HKVL_LEFT_PORT", left_port).strip().upper()
        right_port = os.getenv("APPSTATION_HKVL_RIGHT_PORT", right_port).strip().upper()
        if not left_port or not right_port or left_port == right_port:
            raise ValueError("HKVL runtime ports must be present and different")
    payload: dict[str, Any] = {
        "source": source,
        "protocol": str(serial["protocol"]),
        "leftPort": left_port,
        "rightPort": right_port,
        "leftAxisSign": axis_signs["left"],
        "rightAxisSign": axis_signs["right"],
        "baudrate": int(serial["baudrate"]),
        "expectedSampleHz": int(serial["expectedSampleHz"]),
        "lowpassEnabled": bool(force.get("lowpassEnabled", True)),
        "lowpassCutoffHz": _number(force.get("lowpassCutoffHz", 10), "force.lowpassCutoffHz"),
        "fxyWarnN": _number(safety["fxyWarnN"], "safety.fxyWarnN"),
        "fxyStopN": _number(safety["fxyStopN"], "safety.fxyStopN"),
        "fzWarnN": _number(safety["fzWarnN"], "safety.fzWarnN"),
        "fzStopN": _number(safety["fzStopN"], "safety.fzStopN"),
        "momentWarnNm": _number(safety["momentWarnNm"], "safety.momentWarnNm"),
        "momentStopNm": _number(safety["momentStopNm"], "safety.momentStopNm"),
        "watchdogMs": _number(safety["watchdogMs"], "safety.watchdogMs"),
        "acknowledgeStableMs": 500,
        "complianceEnabled": bool(compliance.get("enabled", False)),
    }
    for side in ("left", "right"):
        side_config = _mapping(compliance[side], f"force.compliance.{side}")
        prefix = side
        payload[f"{prefix}MappingConfirmed"] = bool(side_config.get("mappingConfirmed", False))
        payload[f"{prefix}ComplianceMatrix"] = _number_array(
            side_config["matrix"],
            f"force.compliance.{side}.matrix",
            4,
            nonnegative=False,
        )
        for config_key, payload_suffix in (
            ("deadbandN", "DeadbandN"),
            ("gainUmPerNs", "GainUmPerNs"),
            ("maxStepUm", "MaxStepUm"),
            ("maxOffsetUm", "MaxOffsetUm"),
        ):
            payload[f"{prefix}Compliance{payload_suffix}"] = _number_array(
                side_config[config_key],
                f"force.compliance.{side}.{config_key}",
                2,
                nonnegative=True,
            )
    return payload
