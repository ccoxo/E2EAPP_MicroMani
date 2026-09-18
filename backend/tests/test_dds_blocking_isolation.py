from __future__ import annotations

import asyncio
from threading import Event
from unittest.mock import Mock

import pytest

from backend.core.logging import LogService
from backend.hal_client.dds_client import DdsHalClient
from backend.hal_client.dds_types import HalCommandReply
from backend.tests.test_hal_dds_client import FakeDdsTransport


@pytest.mark.parametrize("blocked_stage", ["publish", "reply"])
def test_blocked_command_capacity_does_not_queue_or_block_emergency_and_lease(blocked_stage) -> None:
    async def exercise() -> None:
        release = Event()
        transport = FakeDdsTransport()
        client = DdsHalClient(LogService(emit_startup=False), transport=transport, reply_timeout_s=0.05)
        client.on_control_transport_fault = Mock()

        def publish(request):
            transport.requests.append(request)
            if blocked_stage == "publish":
                release.wait(2)

        transport.publish_command_request = publish

        def wait(request_id, _timeout):
            if any(request.request_id == request_id for request in transport.emergency_requests):
                return HalCommandReply(
                    request_id=request_id, ok=True, result_json='{"ok":true,"leaseFresh":true}', error="",
                )
            if blocked_stage == "reply":
                release.wait(2)
            return None

        transport.wait_for_command_reply = wait
        pending = [asyncio.create_task(client.command("motion.manual_axis_move", {"step": 1})) for _ in range(4)]
        try:
            await asyncio.sleep(0.01)
            with pytest.raises(RuntimeError, match="not queued"):
                await client.command("motion.manual_axis_move", {"step": 2})
            assert len(transport.requests) == 4
            # DDS不使用default executor；普通任务堵满时这两条独立通道仍能完成。
            loop = asyncio.get_running_loop()
            original_executor = loop.run_in_executor
            loop.run_in_executor = Mock(side_effect=AssertionError("shared executor must not be used"))
            try:
                await client.command("motion.emergency_stop")
                await client.command("control.lease", {"sessionId": "test", "sequence": 1})
            finally:
                loop.run_in_executor = original_executor
            results = await asyncio.gather(*pending, return_exceptions=True)
            assert all(isinstance(result, RuntimeError) for result in results)
            assert client._command_lane.active == 4
            assert client.on_control_transport_fault.called
            with pytest.raises(RuntimeError, match="quarantined"):
                await client.command("motion.manual_axis_move", {"step": 3})
            with pytest.raises(RuntimeError, match="quarantined"):
                await client.command("control.lease", {})
            assert len(transport.requests) == 4
            await client.aclose()
            assert not transport.closed  # 阻塞调用仍持有原生句柄，不允许提前释放。
        finally:
            release.set()
            await asyncio.gather(*pending, return_exceptions=True)
            for _ in range(20):
                if client._command_lane.active == 0:
                    break
                await asyncio.sleep(0.005)
            await client.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["blocked", "missing_reply", "native_error", "rejected"])
def test_emergency_transport_failure_stops_lease_renewal(failure) -> None:
    async def exercise() -> None:
        release = Event()
        transport = FakeDdsTransport()
        client = DdsHalClient(LogService(emit_startup=False), transport=transport, reply_timeout_s=0.01)
        fault = Mock()
        client.on_control_transport_fault = fault

        def wait(request_id, _timeout):
            if failure == "blocked":
                release.wait(2)
            if failure == "native_error":
                raise RuntimeError("native transport failed")
            if failure == "rejected":
                return HalCommandReply(request_id=request_id, ok=False, result_json="", error="emergency failed")
            return None

        transport.wait_for_command_reply = wait
        try:
            with pytest.raises(RuntimeError):
                await client.command("motion.emergency_stop")
            assert fault.called
            with pytest.raises(RuntimeError, match="quarantined"):
                await client.command("control.lease", {})
            assert all(request.name == "motion.emergency_stop" for request in transport.emergency_requests)
        finally:
            release.set()
            for _ in range(20):
                if client._emergency_lane.active == 0:
                    break
                await asyncio.sleep(0.005)
            await client.aclose()

    asyncio.run(exercise())


def test_cancelled_call_not_started_by_os_never_executes_late(monkeypatch) -> None:
    from backend.hal_client.bounded_lane import BoundedLane

    targets = []

    class DelayedThread:
        def __init__(self, *, target, **_kwargs):
            targets.append(target)

        def start(self):
            pass

    monkeypatch.setattr("backend.hal_client.bounded_lane.Thread", DelayedThread)

    async def exercise() -> None:
        lane = BoundedLane("test", 1)
        operation = Mock()
        pending = asyncio.create_task(lane.run(operation, 1))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert lane.active == 1
        targets[0]()
        assert lane.active == 0
        operation.assert_not_called()

    asyncio.run(exercise())


def test_cached_native_read_block_does_not_free_timed_out_capacity_or_stop_event_loop() -> None:
    async def exercise() -> None:
        release = Event()
        transport = FakeDdsTransport()
        client = DdsHalClient(LogService(emit_startup=False), transport=transport, state_timeout_s=0.01)

        def get_latest(_topic):
            release.wait(2)
            return None

        transport.get_latest = get_latest
        try:
            first, second = await asyncio.gather(client.health(), client.health())
            assert not first.connected and not second.connected
            assert client._state_lane.active == 2
            third = await client.health()
            assert not third.connected and "not queued" in third.message
        finally:
            release.set()
            for _ in range(20):
                if client._state_lane.active == 0:
                    break
                await asyncio.sleep(0.005)
            await client.aclose()

    asyncio.run(exercise())


def test_remote_reply_timeout_quarantines_queued_motion_and_preserves_request_deadline() -> None:
    async def exercise() -> None:
        import json

        transport = FakeDdsTransport()
        client = DdsHalClient(LogService(emit_startup=False), transport=transport, reply_timeout_s=0.01)
        fault = Mock()
        client.on_control_transport_fault = fault
        with pytest.raises(RuntimeError, match="timed out"):
            await client.command("motion.manual_axis_move", {"step": 1, "commandExpiresAtUnixMs": 10**20})
        request = transport.requests[0]
        deadline = json.loads(request.payload_json)["commandExpiresAtUnixMs"]
        assert 0 < deadline - request.stamp_unix_ms <= 10
        fault.assert_called_once()
        with pytest.raises(RuntimeError, match="quarantined"):
            await client.command("motion.acknowledge_estop")
        assert len(transport.requests) == 1
        # 传输隔离仍允许尝试停止；普通通道是否可用由独立容量约束判定。
        transport.wait_for_command_reply = lambda request_id, _timeout: HalCommandReply(
            request_id=request_id, ok=True, result_json='{"ok":true}', error="",
        )
        await client.command("motion.disable_side", {"side": "left"})
        await client.command("motion.emergency_stop")
        assert transport.requests[-1].name == "motion.disable_side"
        assert transport.emergency_requests[-1].name == "motion.emergency_stop"
        await client.aclose()

    asyncio.run(exercise())
