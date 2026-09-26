"""续录前核对已有数据的采集条件；本模块不读写文件或连接设备。"""
from __future__ import annotations

import json
import math
from typing import Any

from backend.core.data_contract import validate_data_contract
from backend.core.participation import hardware_sides, participation


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _pulse_vector(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 6 and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value
    )


def resume_metadata_error(
    info: dict[str, Any],
    saved: dict[str, Any],
    *,
    fps: int,
    origin: dict[str, Any],
    selected: dict[str, Any] | None,
    cameras: dict[str, Any],
    resolutions: dict[str, Any],
    features: dict[str, Any],
    motion: dict[str, Any],
) -> str:
    """返回第一个无法证明兼容的原因；缺失信息不能用当前配置补造。"""
    for metadata in (info, saved):
        try:
            validate_data_contract(metadata.get("dataContract"))
        except ValueError as exc:
            return f"数据格式契约不兼容：{exc}"
    if not _positive_number(info.get("fps")) or info["fps"] != fps:
        return "录制帧率不一致或旧帧率无效"
    for count in ("total_frames", "total_episodes"):
        value = info.get(count)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return f"已有数据集的 {count} 缺失或无效"
    if bool(info["total_frames"]) != bool(info["total_episodes"]):
        return "已有数据集的帧数与片段数不一致"
    if "participation" not in saved:
        return "缺少原采集参与侧记录，无法确认续录条件"
    try:
        old_selected = participation(saved["participation"]) if saved["participation"] is not None else None
        new_selected = participation(selected) if selected is not None else None
    except ValueError:
        return "采集参与侧记录无效"
    if old_selected != new_selected:
        return "参与采集的机械臂或夹爪发生变化"
    sides = hardware_sides(new_selected) if new_selected is not None else ["left", "right"]
    old_origin = saved.get("sessionOrigin")
    if not isinstance(old_origin, dict) or not isinstance(origin, dict):
        return "缺少工作原点 W 的记录"
    for side in sides:
        for value in (old_origin, origin):
            if value.get(f"{side}Valid", value.get("valid")) is not True:
                return f"硬件 {side} 侧工作原点 W 无效"
            if not _pulse_vector(value.get(f"{side}Pulse")):
                return f"硬件 {side} 侧工作原点 W 含缺失或非有限数值"
        if any(abs(a - b) > 0.5 for a, b in zip(old_origin[f"{side}Pulse"], origin[f"{side}Pulse"])):
            return f"硬件 {side} 侧工作原点 W 已改变"
    old_features = info.get("features")
    if not isinstance(old_features, dict):
        return "缺少已有数据的字段格式"
    for name, expected in features.items():
        actual = old_features.get(name)
        if not isinstance(actual, dict) or any(
            actual.get(key) != (list(expected[key]) if key == "shape" else expected[key])
            for key in ("dtype", "shape", "names")
        ):
            return f"数据字段 {name} 的类型、形状或通道顺序不一致"
    hardware = saved.get("hardware")
    if not isinstance(hardware, dict):
        return "缺少原始硬件配置"
    old_cameras = hardware.get("cameras")
    old_resolutions = hardware.get("cameraResolutions")
    if not isinstance(old_cameras, dict) or not isinstance(old_resolutions, dict):
        return "缺少相机身份或分辨率记录"
    if (not _positive_number(old_cameras.get("fps")) or not _positive_number(cameras.get("fps"))
            or old_cameras["fps"] != cameras["fps"]):
        return "相机采集帧率不一致或原记录无效"
    identities = []
    for role, key in (("global", "globalIdentity"), ("wrist_left", "wristLeftIdentity"), ("wrist_right", "wristRightIdentity")):
        before, after = old_cameras.get(key), cameras.get(key)
        if not isinstance(before, str) or not isinstance(after, str) or not before.strip() or not after.strip():
            return f"相机 {role} 缺少稳定身份，请先确认绑定；无法凭设备序号认定同一相机"
        if before.strip().casefold() != after.strip().casefold():
            return f"相机 {role} 的身份或角色绑定已改变"
        identities.append(after.strip().casefold())
        old_size, new_size = old_resolutions.get(role), resolutions.get(role)
        if not isinstance(old_size, dict) or not isinstance(new_size, dict):
            return f"缺少相机 {role} 的分辨率记录"
        for kind in ("capture", "saved"):
            if not isinstance(old_size.get(kind), str) or not old_size[kind] or old_size[kind] != new_size.get(kind):
                return f"相机 {role} 的采集或保存分辨率不一致"
    if len(set(identities)) != len(identities):
        return "多个相机角色绑定到同一身份，无法确认续录条件"
    old_motion = hardware.get("motion")
    if not isinstance(old_motion, dict) or not isinstance(old_motion.get("kinematics"), dict):
        return "缺少原始运动标定记录"
    # 时间和整份配置哈希不参与比较，避免无关界面设置阻止续录。
    keys = ["axisOrder", "axisUnitSpec"] + [f"{side}SignedPulsePerUnit" for side in sides]
    for key in keys:
        before = old_motion["kinematics"].get(key)
        after = motion["kinematics"].get(key)
        if before is None or before != after:
            return f"运动标定 {key} 缺失或发生变化"
        try:
            json.dumps(before, allow_nan=False)
        except (TypeError, ValueError):
            return f"运动标定 {key} 含无效数值"
    if old_motion.get("stateUnitSpec") != motion.get("stateUnitSpec"):
        return "运动状态单位发生变化"
    history = saved.get("sessionHistory", [])
    if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
        return "采集会话历史损坏"
    return ""
