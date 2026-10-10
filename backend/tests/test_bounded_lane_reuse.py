"""复用状态线程仍保持容量隔离、取消语义和关闭边界。"""
import asyncio
from threading import Event, Thread, current_thread
from unittest.mock import Mock

import pytest

from backend.hal_client import bounded_lane
from backend.hal_client.bounded_lane import BoundedLane, BlockingCallTimeout


def test_workers_reused_across_event_loops_and_exit_on_close():
    lane = BoundedLane("reused-read", 2, reuse_workers=True)
    threads = set()
    async def reads():
        for _ in range(80):
            threads.add(await lane.run(current_thread, 1))
    for _ in range(3):
        asyncio.run(reads())
    assert len(threads) == 1
    assert all(t.daemon for t in threads)
    lane.close()
    lane.close()
    for t in threads:
        t.join(1)
        assert not t.is_alive()
    async def closed():
        with pytest.raises(RuntimeError, match="closed"):
            await lane.run(lambda: None, 1)
    asyncio.run(closed())


def test_blocked_read_timeout_retains_slot_and_does_not_expand_pool():
    async def run():
        lane = BoundedLane("blocked-read", 1, reuse_workers=True)
        entered, release = Event(), Event()
        def block():
            entered.set()
            release.wait(3)
            return "late"
        try:
            with pytest.raises(BlockingCallTimeout):
                await lane.run(block, .04)
            assert entered.is_set() and lane.active == 1
            forbidden = Mock()
            for _ in range(3):
                with pytest.raises(RuntimeError, match="busy"):
                    await lane.run(forbidden, 1)
            forbidden.assert_not_called()
            assert len(lane._workers) == 1
            # 关闭不会等待卡住的原生线程，也不会释放其占用的容量。
            lane.close()
            assert lane.active == 1
        finally:
            release.set()
            lane.close()
            for worker in lane._workers:
                worker.join(1)
                assert not worker.is_alive()
        assert lane.active == 0
    asyncio.run(run())


def test_cancelled_read_stays_reserved_and_other_slot_remains_usable():
    async def run():
        lane = BoundedLane("cancelled-read", 2, reuse_workers=True)
        entered, release = Event(), Event()
        def block():
            entered.set()
            release.wait(3)
        task = asyncio.create_task(lane.run(block, 1))
        try:
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(.001)
            assert entered.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert lane.active == 1
            assert await lane.run(lambda: 42, 1) == 42
            assert len(lane._workers) == 2
        finally:
            release.set()
            lane.close()
            for worker in lane._workers:
                worker.join(1)
    asyncio.run(run())


def test_worker_exception_does_not_kill_reusable_thread():
    async def run():
        lane = BoundedLane("exception-read", 1, reuse_workers=True)
        def fail():
            raise ValueError("read failed")
        try:
            with pytest.raises(ValueError, match="read failed"):
                await lane.run(fail, 1)
            assert await lane.run(lambda: "fresh", 1) == "fresh"
            assert len(lane._workers) == 1
        finally:
            lane.close()
    asyncio.run(run())


def test_thread_start_failure_returns_capacity_and_can_retry(monkeypatch):
    async def run():
        lane = BoundedLane("startup-read", 1, reuse_workers=True)
        fake = Mock()
        fake.start.side_effect = RuntimeError("thread failed")
        monkeypatch.setattr(bounded_lane, "Thread", Mock(return_value=fake))
        with pytest.raises(RuntimeError, match="thread failed"):
            await lane.run(lambda: None, 1)
        assert lane.active == 0 and not lane._workers
        monkeypatch.setattr(bounded_lane, "Thread", Thread)
        try:
            assert await lane.run(lambda: 1, 1) == 1
        finally:
            lane.close()
    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["success", "error", "late", "cancel"])
def test_reused_workers_keep_completion_deadline_rules(monkeypatch, outcome):
    from backend.tests import test_bounded_lane_completion as cases
    lanes = []
    def reused(name, capacity):
        lane = BoundedLane(name, capacity, reuse_workers=True)
        lanes.append(lane)
        return lane
    monkeypatch.setattr(cases, "BoundedLane", reused)
    try:
        if outcome == "cancel":
            cases.test_cancellation_is_not_hidden_by_completed_worker()
        else:
            cases.test_worker_completion_deadline_survives_event_loop_stall(outcome)
    finally:
        for lane in lanes:
            lane.close()
            for worker in lane._workers:
                worker.join(1)
