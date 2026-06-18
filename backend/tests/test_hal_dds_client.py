from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from backend.core.logging import LogService
from backend.hal_client.dds_client import DdsHalClient
from backend.hal_client.dds_types import (
    TOPIC_HAL_HEALTH,
    TOPIC_HAL_MOTION_STATE,
    HalCommandReply,
    JsonEnvelope,
)


class FakeDdsTransport:
    def __init__(self) -> None:
        self.started = False
        self.closed = False
        self.latest: dict[str, JsonEnvelope] = {}
        self.requests: list[Any] = []
        self.emergency_requests: list[Any] = []
        self.replies: dict[str, HalCommandReply] = {}
        self.waits: list[tuple[str, float]] = []

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True

    def get_latest(self, topic_name: str) -> JsonEnvelope | None:
        return self.latest.get(topic_name)

    def publish_command_request(self, request: Any) -> None:
        self.requests.append(request)

    def publish_emergency_stop(self, request: Any) -> None:
        self.emergency_requests.append(request)

    def wait_for_command_reply(self, request_id: str, timeout_s: float) -> HalCommandReply | None:
        self.waits.append((request_id, timeout_s))
        return self.replies.get(request_id)


def test_dds_hal_client_reads_health_from_topic_cache() -> None:
    transport = FakeDdsTransport()
    transport.latest[TOPIC_HAL_HEALTH] = JsonEnvelope(
        stamp_unix_ms=123,
        stamp_monotonic_ms=456,
        source="bridge",
        payload_json=json.dumps(
            {
                "ltdmc_ok": True,
                "omega7_ok": False,
                "version": "real-hal/1.2.3",
                "uptime_s": 7.5,
            }
        ),
    )

    client = DdsHalClient(LogService(emit_startup=False), transport=transport)
    health = asyncio.run(client.health())

    assert transport.started is True
    assert health.connected is True
    assert health.mode == "real"
    assert health.ltdmc_ok is True
    assert health.omega7_ok is False
    assert health.version == "real-hal/1.2.3"
    assert health.uptime_s == 7.5


def test_dds_hal_client_reads_motion_state_from_topic_cache() -> None:
    transport = FakeDdsTransport()
    transport.latest[TOPIC_HAL_MOTION_STATE] = JsonEnvelope(
        stamp_unix_ms=123,
        stamp_monotonic_ms=456,
        source="bridge",
        payload_json='{"positions":[1,2,3]}',
    )

    client = DdsHalClient(LogService(emit_startup=False), transport=transport)
    state = asyncio.run(client.motion_state())

    assert state["positions"] == [1, 2, 3]
    assert isinstance(state["timestamp_ms"], int)
    assert isinstance(state["received_timestamp_ms"], int)
    assert isinstance(state["received_monotonic_ms"], int)


def test_dds_hal_client_emergency_stop_uses_dedicated_topic_and_matches_reply() -> None:
    transport = FakeDdsTransport()

    def publish_and_reply(request: Any) -> None:
        transport.emergency_requests.append(request)
        transport.replies[request.request_id] = HalCommandReply(
            request_id=request.request_id,
            ok=True,
            result_json='{"ok":true}',
            error="",
        )

    transport.publish_emergency_stop = publish_and_reply  # type: ignore[method-assign]
    client = DdsHalClient(LogService(emit_startup=False), transport=transport, reply_timeout_s=0.25)

    result = asyncio.run(client.command("motion.emergency_stop", {"reason": "test"}))

    assert transport.requests == []
    request = transport.emergency_requests[0]
    assert request.name == "motion.emergency_stop"
    assert json.loads(request.payload_json) == {"reason": "test"}
    assert transport.waits == [(request.request_id, 0.25)]
    assert result == {
        "mode": "real",
        "transport": "dds",
        "command": "motion.emergency_stop",
        "response": {"ok": True},
    }


def test_dds_hal_client_routes_teleop_target_update_through_command_request() -> None:
    transport = FakeDdsTransport()

    def publish_and_reply(request: Any) -> None:
        transport.requests.append(request)
        transport.replies[request.request_id] = HalCommandReply(
            request_id=request.request_id,
            ok=True,
            result_json='{"ok":true,"appliedDeltas":[1,2,3,4,5,6]}',
            error="",
        )

    transport.publish_command_request = publish_and_reply  # type: ignore[method-assign]
    client = DdsHalClient(LogService(emit_startup=False), transport=transport, reply_timeout_s=0.25)

    result = asyncio.run(
        client.command(
            "motion.teleop_target_update",
            {
                "side": "right",
                "deltas": {
                    "X": 1.0,
                    "Y": 2.0,
                    "Z": 3.0,
                    "Roll": 4.0,
                    "Pitch": 5.0,
                    "Yaw": 6.0,
                },
                "translationStepLimitPulse": 4000.0,
                "rotationStepLimitPulse": 1250.0,
                "translationPulseDeadband": 2.0,
                "rotationPulseDeadband": 3.0,
                "enabledAxes": [True, False, True, False, True, False],
                "syncZeroDeltaTarget": True,
                "softLimitMin": [-1.0, -2.0, -3.0, -4.0, -5.0, -6.0],
                "softLimitMax": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                "translationVelocityUiPerSec": 8000.0,
                "rotationVelocityUiPerSec": 12.0,
                "translationStartVelocityUiPerSec": 600.0,
                "rotationStartVelocityUiPerSec": 1.0,
                "accTimeSec": 0.05,
                "decTimeSec": 0.06,
            },
        )
    )

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.name == "motion.teleop_target_update"
    request_payload = json.loads(request.payload_json)
    assert request_payload["side"] == "right"
    assert request_payload["X"] == 1.0
    assert request_payload["Yaw"] == 6.0
    assert request_payload["deltas"] == {
        "X": 1.0,
        "Y": 2.0,
        "Z": 3.0,
        "Roll": 4.0,
        "Pitch": 5.0,
        "Yaw": 6.0,
    }
    assert result == {
        "mode": "real",
        "transport": "dds",
        "command": "motion.teleop_target_update",
        "response": {"ok": True, "appliedDeltas": [1, 2, 3, 4, 5, 6]},
    }


def test_dds_hal_client_retries_non_home_command_after_timeout() -> None:
    transport = FakeDdsTransport()

    def publish_and_reply_on_retry(request: Any) -> None:
        transport.requests.append(request)
        if len(transport.requests) == 2:
            transport.replies[request.request_id] = HalCommandReply(
                request_id=request.request_id,
                ok=True,
                result_json='{"stopped":true}',
                error="",
            )

    transport.publish_command_request = publish_and_reply_on_retry  # type: ignore[method-assign]
    client = DdsHalClient(LogService(emit_startup=False), transport=transport, reply_timeout_s=0.25)

    result = asyncio.run(client.command("teleop.native.stop", {}))

    assert [request.name for request in transport.requests] == ["teleop.native.stop", "teleop.native.stop"]
    assert len({request.request_id for request in transport.requests}) == 2
    assert transport.waits == [
        (transport.requests[0].request_id, 0.25),
        (transport.requests[1].request_id, 0.25),
    ]
    assert result == {
        "mode": "real",
        "transport": "dds",
        "command": "teleop.native.stop",
        "response": {"stopped": True},
    }


def test_dds_hal_client_uses_long_timeout_without_retry_for_home_command() -> None:
    transport = FakeDdsTransport()

    def publish_and_reply(request: Any) -> None:
        transport.requests.append(request)
        transport.replies[request.request_id] = HalCommandReply(
            request_id=request.request_id,
            ok=True,
            result_json='{"homed":true}',
            error="",
        )

    transport.publish_command_request = publish_and_reply  # type: ignore[method-assign]
    client = DdsHalClient(LogService(emit_startup=False), transport=transport, reply_timeout_s=0.25)

    result = asyncio.run(client.command("motion.home_side", {"side": "left"}))

    assert len(transport.requests) == 1
    assert transport.waits == [(transport.requests[0].request_id, 75.0)]
    assert result["response"] == {"homed": True}


def test_dds_hal_client_command_reports_timeout_and_negative_reply() -> None:
    transport = FakeDdsTransport()
    client = DdsHalClient(LogService(emit_startup=False), transport=transport, reply_timeout_s=0.01)

    with pytest.raises(RuntimeError, match="DDS HAL command timed out"):
        asyncio.run(client.command("motion.emergency_stop", {}))

    def publish_and_reply(request: Any) -> None:
        transport.emergency_requests.append(request)
        transport.replies[request.request_id] = HalCommandReply(
            request_id=request.request_id,
            ok=False,
            result_json="{}",
            error="HAL HTTP 500",
        )

    transport.publish_emergency_stop = publish_and_reply  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="HAL HTTP 500"):
        asyncio.run(client.command("motion.emergency_stop", {}))


def test_dds_hal_client_default_runtime_reports_missing_fastdds_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("APPSTATION_FASTDDS_BINDING_DLL", str(tmp_path / "missing-fastdds.dll"))
    with pytest.raises(RuntimeError, match="Fast-DDS Python bindings are required"):
        DdsHalClient(LogService(emit_startup=False))
