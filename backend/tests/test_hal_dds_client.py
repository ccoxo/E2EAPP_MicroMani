# 阅读导航 07｜测试与验证
# 职责：回归验证：DDS 状态缓存、命令应答、急停发布、超时和重试。
# 先看：FakeDdsTransport → test_dds_hal_client_reads_health_from_topic_cache → test_dds_hal_client_reads_motion_state_from_topic_cache → test_dds_hal_client_reads_force_state_from_topic_cache。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from backend.core.logging import LogService
from backend.hal_client.dds_client import DdsHalClient
from backend.hal_client.dds_types import (
    TOPIC_HAL_FORCE_STATE,
    TOPIC_HAL_HEALTH,
    TOPIC_HAL_MOTION_STATE,
    TOPIC_HAL_NATIVE_TELEOP_STATUS,
    TOPIC_HAL_OMEGA_STATE,
    HalCommandReply,
    JsonEnvelope,
)


@pytest.fixture(autouse=True)
def fixed_dds_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.hal_client.dds_client.now_unix_ms", lambda: 100_000)


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


def test_recording_sources_can_read_concurrently_without_competing_for_two_slots():
    from threading import Barrier

    async def exercise():
        transport = FakeDdsTransport()
        barrier = Barrier(3, timeout=1)

        def get_latest(topic):
            barrier.wait()
            return JsonEnvelope(stamp_unix_ms=100_000, stamp_monotonic_ms=456,
                                source="test", payload_json=json.dumps({"topic": topic}))

        transport.get_latest = get_latest
        client = DdsHalClient(LogService(emit_startup=False), transport=transport, state_timeout_s=2)
        try:
            values = await asyncio.gather(client.motion_state(), client.omega_state(), client.force_state())
            assert [item["topic"] for item in values] == [TOPIC_HAL_MOTION_STATE, TOPIC_HAL_OMEGA_STATE, TOPIC_HAL_FORCE_STATE]
        finally:
            barrier.abort()
            await client.aclose()

    asyncio.run(exercise())


def test_dds_hal_client_reads_health_from_topic_cache() -> None:
    transport = FakeDdsTransport()
    transport.latest[TOPIC_HAL_HEALTH] = JsonEnvelope(
        stamp_unix_ms=100_000,
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
    assert health.source_valid_until_ms == 100_500


def test_dds_hal_client_reads_motion_state_from_topic_cache() -> None:
    transport = FakeDdsTransport()
    transport.latest[TOPIC_HAL_MOTION_STATE] = JsonEnvelope(
        stamp_unix_ms=100_000,
        stamp_monotonic_ms=456,
        source="bridge",
        payload_json='{"positions":[1,2,3]}',
    )

    client = DdsHalClient(LogService(emit_startup=False), transport=transport)
    state = asyncio.run(client.motion_state())

    assert state["positions"] == [1, 2, 3]
    assert state["timestamp_ms"] == 100_000
    assert state["monotonicMs"] == 456
    assert state["monotonic_s"] == pytest.approx(0.456)
    assert state["dds_stamp_unix_ms"] == 100_000
    assert state["dds_stamp_monotonic_ms"] == 456
    assert "received_monotonic_ms" not in state


def test_dds_hal_client_reads_force_state_from_topic_cache() -> None:
    transport = FakeDdsTransport()
    transport.latest[TOPIC_HAL_FORCE_STATE] = JsonEnvelope(
        stamp_unix_ms=100_000,
        stamp_monotonic_ms=456,
        source="hal-cpp",
        payload_json='{"source":"hkvl_serial","left":[1,2,3,4,5,6],"right":[6,5,4,3,2,1],"dangerIndex":0.5}',
    )

    client = DdsHalClient(LogService(emit_startup=False), transport=transport)
    state = asyncio.run(client.force_state())

    assert state["source"] == "hkvl_serial"
    assert state["left"] == [1, 2, 3, 4, 5, 6]
    assert state["right"] == [6, 5, 4, 3, 2, 1]
    assert state["dangerIndex"] == 0.5
    assert state["dds_stamp_monotonic_ms"] == 456


def test_dds_hal_client_reads_native_teleop_status_from_topic_cache() -> None:
    transport = FakeDdsTransport()
    transport.latest[TOPIC_HAL_NATIVE_TELEOP_STATUS] = JsonEnvelope(
        stamp_unix_ms=100_000,
        stamp_monotonic_ms=456000,
        source="hal-cpp",
        payload_json=json.dumps({"running": True, "lastAction": {"monotonicMs": 455900}}),
    )

    client = DdsHalClient(LogService(emit_startup=False), transport=transport)
    result = asyncio.run(client.command("teleop.native.status", {}))

    assert transport.requests == []
    assert transport.waits == []
    assert result["command"] == "teleop.native.status"
    response = result["response"]
    assert response["running"] is True
    assert response["timestamp_ms"] == 100_000
    assert response["monotonicMs"] == 456000
    assert response["monotonic_s"] == pytest.approx(456.0)
    assert response["dds_source"] == "hal-cpp"
    assert response["dds_stamp_unix_ms"] == 100_000
    assert response["dds_stamp_monotonic_ms"] == 456000
    assert "received_monotonic_ms" not in response


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


@pytest.mark.parametrize("command,delay_s,expected_timeout_s", [
    ("control.lease", 0.7, 1.0),
    ("motion.emergency_stop", 0.55, 0.75),
])
def test_dds_critical_reply_within_hal_lease_is_not_false_timeout(
    command: str, delay_s: float, expected_timeout_s: float
) -> None:
    transport = FakeDdsTransport()

    def wait_for_reply(request_id: str, timeout_s: float) -> HalCommandReply:
        transport.waits.append((request_id, timeout_s))
        time.sleep(delay_s)
        return HalCommandReply(request_id=request_id, ok=True, result_json='{"ok":true,"leaseFresh":true}', error="")

    transport.wait_for_command_reply = wait_for_reply  # type: ignore[method-assign]
    client = DdsHalClient(LogService(emit_startup=False), transport=transport)
    try:
        result = asyncio.run(client.command(command, {}))
        assert result["response"]["ok"] is True
        assert client._control_transport_failed is False
        assert len(transport.emergency_requests) == 1
        assert transport.waits == [(transport.emergency_requests[0].request_id, expected_timeout_s)]
    finally:
        client.close()


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


@pytest.mark.parametrize("name", [
    "motion.manual_axis_move", "motion.enable_side", "motion.acknowledge_estop",
    "teleop.native.start", "gripper.command", "teleop.native.gripper_command",
])
def test_dds_lost_reply_does_not_reissue_mutating_command(name: str) -> None:
    transport = FakeDdsTransport()
    # 此用例模拟已发布后丢失应答，不能让线程调度的 1ms 抖动先触发发布前过期。
    client = DdsHalClient(LogService(emit_startup=False), transport=transport, reply_timeout_s=0.1)
    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(client.command(name, {"side": "left", "step": 5}))
    assert len(transport.requests) == 1


@pytest.mark.parametrize("stamp", [0, -1, None, True, "100000", float("nan"), 99_499, 100_101])
@pytest.mark.parametrize("topic,method", [
    (TOPIC_HAL_HEALTH, "health"),
    (TOPIC_HAL_MOTION_STATE, "motion_state"),
    (TOPIC_HAL_OMEGA_STATE, "omega_state"),
    (TOPIC_HAL_FORCE_STATE, "force_state"),
    (TOPIC_HAL_NATIVE_TELEOP_STATUS, "teleop.native.status"),
])
def test_dds_source_timestamp_cannot_be_refreshed_by_payload(stamp, topic, method) -> None:
    transport = FakeDdsTransport()
    transport.latest[topic] = JsonEnvelope(
        stamp_unix_ms=stamp, stamp_monotonic_ms=456, source="hal-cpp",
        payload_json='{"timestamp_ms":100000,"ltdmc_ok":true,"running":false}',
    )
    client = DdsHalClient(LogService(emit_startup=False), transport=transport)
    try:
        if method == "health":
            health = asyncio.run(client.health())
            assert not health.connected and not health.ltdmc_ok
            assert "source timestamp" in health.message
        else:
            with pytest.raises(RuntimeError, match="source timestamp"):
                asyncio.run(client.command(method) if method == "teleop.native.status" else getattr(client, method)())
        assert transport.requests == []
    finally:
        client.close()


def test_unchanged_dds_force_cache_expires_and_ws_frame_loses_health(monkeypatch) -> None:
    from backend.tests.test_telemetry_hub import FakeSettings, FakeHardware
    from backend.services.telemetry_hub import TelemetryHub

    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    transport = FakeDdsTransport()
    transport.latest[TOPIC_HAL_FORCE_STATE] = JsonEnvelope(
        stamp_unix_ms=100_000, stamp_monotonic_ms=456, source="hal-cpp",
        payload_json=json.dumps({
            "source": "hkvl_serial", "left": [1] * 6, "right": [2] * 6,
            "sides": {"left": {"healthy": True}, "right": {"healthy": True}},
            "calibration": {"phase": "ready"},
        }),
    )
    client = DdsHalClient(LogService(emit_startup=False), transport=transport)
    telemetry = TelemetryHub(FakeSettings(), FakeHardware())
    try:
        frame = telemetry.next_frame(force_state=asyncio.run(client.force_state()), hal_ok=True)
        assert frame.forceStatus["sides"]["left"]["healthy"] is True
        monkeypatch.setattr("backend.hal_client.dds_client.now_unix_ms", lambda: 100_501)
        with pytest.raises(RuntimeError, match="stale"):
            asyncio.run(client.force_state())
        frame = telemetry.next_frame(force_state=None, hal_ok=True)
        assert frame.forceLeft == [1] * 6
        assert frame.forceRight == [2] * 6
        assert not telemetry.force_ok
        assert all(side["healthy"] is False for side in frame.forceStatus["sides"].values())
        assert "calibration" not in frame.forceStatus
    finally:
        client.close()
        telemetry.shutdown()


def test_tare_wait_preserves_state_reads_and_has_its_own_nonretrying_budget() -> None:
    from threading import Event

    async def exercise() -> None:
        started, release = Event(), Event()
        transport = FakeDdsTransport()
        client = DdsHalClient(LogService(emit_startup=False), transport=transport)

        def wait(request_id, timeout):
            transport.waits.append((request_id, timeout))
            started.set()
            release.wait(2)
            return HalCommandReply(request_id=request_id, ok=True, result_json='{"ok":true}', error="")

        transport.wait_for_command_reply = wait
        pending = asyncio.create_task(client.command("force.tare", {"samples": 200, "unloadedConfirmed": True}))
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.005)
            assert started.is_set() and not pending.done()
            for phase in ("checking_stability", "validating"):
                transport.latest[TOPIC_HAL_FORCE_STATE] = JsonEnvelope(
                    stamp_unix_ms=100_000, stamp_monotonic_ms=456, source="hal-cpp",
                    payload_json=json.dumps({"calibration": {"phase": phase}}),
                )
                assert (await client.force_state())["calibration"]["phase"] == phase
                assert not pending.done()
            request = transport.requests[0]
            assert transport.waits == [(request.request_id, 6.0)]
            assert json.loads(request.payload_json)["commandExpiresAtUnixMs"] == 106_000
        finally:
            release.set()
            await pending
            await client.aclose()
        assert len(transport.requests) == 1

    asyncio.run(exercise())


def test_tare_lost_reply_never_retries_or_keeps_control_lease() -> None:
    transport = FakeDdsTransport()
    client = DdsHalClient(LogService(emit_startup=False), transport=transport)
    try:
        with pytest.raises(RuntimeError, match="timed out"):
            asyncio.run(client.command("force.tare", {"samples": 200}))
        assert len(transport.requests) == 1
        assert transport.waits == [(transport.requests[0].request_id, 6.0)]
        with pytest.raises(RuntimeError, match="quarantined"):
            asyncio.run(client.command("control.lease"))
    finally:
        client.close()
