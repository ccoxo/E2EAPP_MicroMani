import asyncio
import time

from backend.core.loop_watchdog import LoopStallWatchdog


def blocking_section_for_watchdog_test() -> None:
    time.sleep(0.4)


def test_watchdog_records_loop_stack_during_stall(tmp_path):
    log = tmp_path / "loop_stalls.log"

    async def scenario() -> None:
        watchdog = LoopStallWatchdog(log, threshold_s=0.1, poll_s=0.02)
        watchdog.start(asyncio.get_running_loop())
        await asyncio.sleep(0.1)
        blocking_section_for_watchdog_test()
        await asyncio.sleep(0.15)
        await watchdog.stop()

    asyncio.run(scenario())
    text = log.read_text(encoding="utf-8")
    assert "loop stalled" in text
    assert "--- LOOP thread" in text
    assert "blocking_section_for_watchdog_test" in text
    assert "stall end" in text


def test_watchdog_stays_quiet_without_stall(tmp_path):
    log = tmp_path / "loop_stalls.log"

    async def scenario() -> None:
        watchdog = LoopStallWatchdog(log, threshold_s=0.2, poll_s=0.02)
        watchdog.start(asyncio.get_running_loop())
        for _ in range(10):
            await asyncio.sleep(0.02)
        await watchdog.stop()

    asyncio.run(scenario())
    assert not log.exists()
