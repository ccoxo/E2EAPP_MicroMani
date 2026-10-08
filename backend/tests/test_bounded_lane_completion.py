from __future__ import annotations

import asyncio
import time
from threading import Event

import pytest

from backend.hal_client.bounded_lane import BoundedLane, BlockingCallTimeout


@pytest.mark.parametrize("outcome", ["success", "error", "late"])
def test_worker_completion_deadline_survives_event_loop_stall(outcome):
    async def exercise():
        lane = BoundedLane("completion-test", 1)
        entered, release, finished = Event(), Event(), Event()

        def operation():
            entered.set()
            release.wait(2)
            try:
                if outcome == "error":
                    raise ValueError("worker failure")
                return "confirmed"
            finally:
                finished.set()

        task = asyncio.create_task(lane.run(operation, 0.2))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        # 故意阻塞事件循环；工作线程仍独立执行。
        if outcome == "late":
            time.sleep(0.25)
        release.set()
        assert finished.wait(1)
        time.sleep(0.3)
        if outcome == "late":
            with pytest.raises(BlockingCallTimeout):
                await task
        elif outcome == "error":
            with pytest.raises(ValueError, match="worker failure"):
                await task
        else:
            assert await task == "confirmed"
        assert lane.active == 0

    asyncio.run(exercise())


def test_cancellation_is_not_hidden_by_completed_worker():
    async def exercise():
        lane = BoundedLane("cancel-test", 1)
        entered, release, finished = Event(), Event(), Event()

        def operation():
            entered.set()
            release.wait(2)
            finished.set()
            return "confirmed"

        task = asyncio.create_task(lane.run(operation, 1))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        release.set()
        assert finished.wait(1)
        time.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert lane.active == 0

    asyncio.run(exercise())


def test_timely_emergency_reply_does_not_quarantine_after_loop_stall():
    from unittest.mock import Mock
    from backend.hal_client.dds_client import DdsHalClient
    from backend.hal_client.dds_types import HalCommandReply
    from backend.tests.test_hal_dds_client import FakeDdsTransport

    async def exercise():
        entered, release, finished = Event(), Event(), Event()
        transport = FakeDdsTransport()
        client = DdsHalClient(Mock(), transport=transport, reply_timeout_s=0.2)
        fault = Mock()
        client.on_control_transport_fault = fault

        def reply(request_id, _timeout):
            entered.set()
            release.wait(2)
            finished.set()
            return HalCommandReply(request_id=request_id, ok=True,
                                   result_json='{"ok":true}', error="")

        transport.wait_for_command_reply = reply
        task = asyncio.create_task(client.command("motion.emergency_stop"))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        release.set()
        assert finished.wait(1)
        time.sleep(0.35)
        try:
            assert (await task)["response"]["ok"] is True
            fault.assert_not_called()
            assert client._control_transport_failed is False
        finally:
            release.set()
            await client.aclose()

    asyncio.run(exercise())
