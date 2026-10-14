"""连续绝对目标下发时监测实际进展，目标更新不能延后卡轴超时。"""

import math


class StreamProgressWatch:
    def __init__(self, initial, max_lead, tolerance, now, timeout=2.0):
        self.anchor = list(initial)
        self.progress_at = [now] * len(initial)
        self.max_lead = list(max_lead)
        self.tolerance = list(tolerance)
        self.timeout = timeout

    def check(self, actual, target, now):
        if len(actual) != len(self.anchor) or len(target) != len(self.anchor):
            raise RuntimeError("stream feedback shape mismatch")
        for i, (position, goal) in enumerate(zip(actual, target)):
            if not math.isfinite(position) or not math.isfinite(goal):
                raise RuntimeError("stream feedback is nonfinite")
            error = abs(position - goal)
            if error > self.max_lead[i]:
                raise RuntimeError(f"stream axis {i} target lead exceeded")
            if error <= self.tolerance[i] or abs(position - self.anchor[i]) > self.tolerance[i]:
                self.anchor[i] = position
                self.progress_at[i] = now
            elif now - self.progress_at[i] > self.timeout:
                raise RuntimeError(f"stream axis {i} made no progress")


def monotonic_z_target(current, delta, last_target_z, lower, upper):
    """使用绝对目标，避免旧采样让 Z 回退或增量积累失控。"""
    target = [a + b for a, b in zip(current, delta)]
    target[2] = max(current[2], last_target_z) + max(0.0, delta[2])
    if len(target) != 6 or any(not math.isfinite(x) or x < lo or x > hi
                               for x, lo, hi in zip(target, lower, upper)):
        raise RuntimeError("stream target outside configured workspace")
    return target
