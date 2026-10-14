"""实机策略调用方的夹爪到位判断，不把命令应答当作位置反馈。"""

import math
from typing import Any


def gripper_target_reached(
    feedback: dict[str, Any], *, target_mm: float, stroke_mm: float,
    sent_at_ms: float, now_ms: float,
) -> bool:
    if feedback.get("positionOk") is not True:
        return False
    values = (feedback.get("positionMm"), feedback.get("positionSampleTs"),
              feedback.get("lastCommandTs"), target_mm, stroke_mm)
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        return False
    position, sampled, commanded, _, _ = values
    if stroke_mm <= 0 or not 0 <= position <= stroke_mm or not 0 <= target_mm <= stroke_mm:
        return False
    if not sent_at_ms <= commanded <= sampled <= now_ms or now_ms - sampled > 500:
        return False
    # 与 HAL 的 lround 保持一致；26 mm 行程每个编码计数约为 0.102 mm。
    target_raw = math.floor((stroke_mm - target_mm) / stroke_mm * 255 + 0.5)
    actual_raw = math.floor((stroke_mm - position) / stroke_mm * 255 + 0.5)
    return abs(actual_raw - target_raw) <= 1


class GripperIncrementTracker:
    """仅用于已核验每步反馈的试运行；单次会话内累积夹爪欠行程。"""

    def __init__(self, stroke_mm: float = 26, min_gap_mm: float = 1.02, max_step_mm: float = .2):
        if not 0 <= min_gap_mm < stroke_mm or not 0 < max_step_mm <= stroke_mm:
            raise ValueError("invalid gripper limits")
        self.stroke = stroke_mm
        self.minimum = min_gap_mm
        self.max_step = max_step_mm
        self.target = None
        self.anchor = None
        self.direction = 0
        self.pending_travel = 0.0
        self.pending_steps = 0

    def next_target(self, position: float, delta: float) -> float:
        count_mm = self.stroke / 255
        # 最小开口经编码量化后可能略小于毫米配置值。
        if not math.isfinite(position) or not math.isfinite(delta) or not max(0, self.minimum - count_mm * .5) <= position <= self.stroke:
            raise ValueError("invalid gripper position or increment")
        direction = (delta > 0) - (delta < 0)
        # 停止、反向或真实前进一个计数时清除停滞累计；反向不携带旧目标。
        reversed_or_hold = direction != self.direction or direction == 0
        progressed = self.anchor is not None and direction * (position - self.anchor) >= count_mm * .5
        if reversed_or_hold or progressed or self.anchor is None:
            self.anchor = position
            self.pending_travel = 0.0
            self.pending_steps = 0
        if reversed_or_hold:
            self.target = position
        self.direction = direction
        residual = 0.0 if self.target is None else self.target - position
        residual = max(-count_mm, min(count_mm, residual))
        # 不增加反向残差，不超出原有每步幅度和机械开口保护。
        if direction * residual < 0:
            residual = 0.0
        step = max(-self.max_step, min(self.max_step, delta + residual))
        target = max(self.minimum, min(self.stroke, position + step))
        target_raw = math.floor((self.stroke - target) / count_mm + .5)
        actual_raw = math.floor((self.stroke - position) / count_mm + .5)
        at_endpoint = ((direction < 0 and target == self.minimum)
                       or (direction > 0 and target == self.stroke))
        if at_endpoint and abs(actual_raw - target_raw) <= 1:
            self.pending_travel = 0.0
            self.pending_steps = 0
        elif self.pending_steps >= 3 and self.pending_travel >= 2 * count_mm:
            raise RuntimeError("gripper no progress despite repeated target increments")
        self.pending_travel += min(abs(delta), abs(target - position), self.max_step)
        self.pending_steps += 1
        self.target = target
        return target
