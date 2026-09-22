"""采集与回放共用的操作者侧参与契约。"""
from copy import deepcopy
from backend.core.operator_view import hardware_side_for_operator_side

VERSION = "appstation.participation.v1"


def participation(value):
    if not isinstance(value, dict) or value.get("version") != VERSION:
        raise ValueError("缺少有效的参与侧配置，请明确选择参与臂与夹爪")
    result = {"version": VERSION}
    for key in ("arms", "grippers"):
        sides = value.get(key)
        if not isinstance(sides, list) or any(s not in ("left", "right") for s in sides) or len(set(sides)) != len(sides):
            raise ValueError("参与侧必须是无重复的 left/right 列表")
        result[key] = [s for s in ("left", "right") if s in sides]
    if not result["arms"] or not set(result["grippers"]).issubset(result["arms"]):
        raise ValueError("至少选择一只臂，夹爪必须属于参与臂")
    return result


def action_mask(value):
    value = participation(value)
    return [enabled for side in ("left", "right")
            for enabled in ([side in value["arms"]] * 6 + [side in value["grippers"]])]


def hardware_sides(value, key="arms"):
    return [hardware_side_for_operator_side(s) for s in participation(value)[key]]


def source_sides(config, value):
    sides = hardware_sides(value)
    return [hardware_side_for_operator_side(s) for s in sides] if config.get("teleop", {}).get("swapTeleopChannels", True) else sides


def scoped_config(config, value):
    """只限制会话中的输出，不改写持久配置或启用未授权设备。"""
    result = deepcopy(config)
    arms = hardware_sides(value)
    grippers = hardware_sides(value, "grippers")
    sources = source_sides(config, value)
    for side in ("left", "right"):
        if side not in sources:
            result["teleop"][f"{side}Connected"] = False
        if side not in arms:
            result["teleop"][f"{side}EnabledAxes"] = [False] * 6
        result["teleop"][f"{side}GripperParticipating"] = side in grippers
    return result
