from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

DEFAULT_DDS_DOMAIN_ID = 42

# Topic 名称必须和 HAL C++ Fast-DDS 侧保持完全一致，否则 DDS discovery 能发现进程但收不到样本。
TOPIC_HAL_HEALTH = "AppStation.Hal.Health"
TOPIC_HAL_MOTION_STATE = "AppStation.Hal.MotionState"
TOPIC_HAL_OMEGA_STATE = "AppStation.Hal.OmegaState"
TOPIC_HAL_NATIVE_TELEOP_STATUS = "AppStation.Hal.NativeTeleopStatus"
TOPIC_HAL_COMMAND_REQUEST = "AppStation.Hal.CommandRequest"
TOPIC_HAL_COMMAND_REPLY = "AppStation.Hal.CommandReply"
TOPIC_HAL_EMERGENCY_STOP = "AppStation.Hal.EmergencyStop"
TOPIC_TELEOP_LEADER_STATE = "AppStation.Teleop.LeaderState"
TOPIC_TELEOP_FOLLOWER_TARGET_PREVIEW = "AppStation.Teleop.FollowerTargetPreview"
TOPIC_TELEOP_HARDWARE_TARGET = "AppStation.Teleop.HardwareTarget"

@dataclass(frozen=True)
class JsonEnvelope:
    # 第一阶段保留 JSON envelope，避免前端和 FastAPI 对外语义跟着 DDS 接入一起变化。
    stamp_unix_ms: int
    stamp_monotonic_ms: int
    source: str
    payload_json: str


@dataclass(frozen=True)
class HalCommandRequest:
    # request_id 是 command request/reply 的唯一匹配键，不能依赖 topic 顺序来判断响应归属。
    request_id: str
    stamp_unix_ms: int
    name: str
    payload_json: str


@dataclass(frozen=True)
class HalCommandReply:
    request_id: str
    ok: bool
    result_json: str
    error: str


def now_unix_ms() -> int:
    return int(time.time() * 1000)


def now_monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def make_json_envelope(source: str, payload: dict[str, Any]) -> JsonEnvelope:
    return JsonEnvelope(
        stamp_unix_ms=now_unix_ms(),
        stamp_monotonic_ms=now_monotonic_ms(),
        source=source,
        payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
    )
