"""Offline regression tests: no vendor SDK, real DDS, cameras or robot commands."""
import asyncio
from threading import Event

import pytest

from backend.core.logging import LogService
from backend.services.dataset_recorder import DatasetRecorderService


def recorder_stub():
    recorder = object.__new__(DatasetRecorderService)
    recorder.logs = LogService(emit_startup=False)
    recorder._startup_native_preflight_error = None
    return recorder


@pytest.mark.parametrize("dependency_error", ["", "missing dependency"])
def test_startup_preflight_is_awaited_off_loop_and_cached(monkeypatch, dependency_error):
    recorder = recorder_stub()
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    monkeypatch.setenv("APPSTATION_LEROBOT_NATIVE", "1")
    release = Event()
    calls = []

    def preflight():
        calls.append(release.wait(1))
        return dependency_error

    monkeypatch.setattr(recorder, "_native_preflight", preflight)

    async def exercise():
        asyncio.get_running_loop().call_later(.02, release.set)
        await recorder.prepare_native_runtime({})
        await recorder.prepare_native_runtime({})

    asyncio.run(exercise())
    assert calls == [True]
    assert recorder._startup_native_preflight_error == dependency_error
    monkeypatch.delattr(recorder, "_native_preflight")
    monkeypatch.setattr(recorder, "_native_imports", lambda: pytest.fail("reimported native dependencies"))
    assert recorder._native_preflight() == dependency_error
    messages = [entry.msg for entry in recorder.logs.list_entries()]
    assert len(messages) == 2
    assert f"phase={'failed' if dependency_error else 'ready'}" in messages[-1]
    assert "elapsed_ms=" in messages[-1]


@pytest.mark.parametrize("mode,native", [("test", "1"), ("real", "0")])
def test_test_mode_or_disabled_native_does_not_import(monkeypatch, mode, native):
    recorder = recorder_stub()
    monkeypatch.setenv("APPSTATION_HAL_MODE", mode)
    monkeypatch.setenv("APPSTATION_LEROBOT_NATIVE", native)
    monkeypatch.setattr(recorder, "_native_preflight", lambda: pytest.fail("unexpected import"))
    asyncio.run(recorder.prepare_native_runtime({}))
    assert recorder._startup_native_preflight_error is None


def test_prepare_failure_is_cached_and_recording_rejected_without_retry(monkeypatch):
    recorder = recorder_stub()
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    monkeypatch.setenv("APPSTATION_LEROBOT_NATIVE", "1")

    def broken():
        raise OSError("broken native library")

    monkeypatch.setattr(recorder, "_native_preflight", broken)
    asyncio.run(recorder.prepare_native_runtime({}))
    monkeypatch.delattr(recorder, "_native_preflight")
    monkeypatch.setattr(recorder, "_native_imports", lambda: pytest.fail("cold retry"))
    assert not asyncio.run(recorder._try_begin_native_dataset({}))
    assert "broken native library" in recorder._native_error


def test_cancelled_startup_does_not_mark_runtime_ready(monkeypatch):
    recorder = recorder_stub()
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    monkeypatch.setenv("APPSTATION_LEROBOT_NATIVE", "1")
    entered, release = Event(), Event()

    def blocked():
        entered.set()
        release.wait(2)
        return ""

    monkeypatch.setattr(recorder, "_native_preflight", blocked)

    async def exercise():
        task = asyncio.create_task(recorder.prepare_native_runtime({}))
        try:
            while not entered.is_set():
                await asyncio.sleep(.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert recorder._startup_native_preflight_error is None
        finally:
            release.set()

    asyncio.run(exercise())


def test_asgi_startup_waits_for_recorder_prepare(tmp_path, monkeypatch):
    from backend.app import create_app

    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    app = create_app(tmp_path)
    app.state.telemetry.hardware = None

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def prepare(config):
            entered.set()
            await release.wait()

        monkeypatch.setattr(app.state.recorder, "prepare_native_runtime", prepare)
        ready, finish = asyncio.Event(), asyncio.Event()

        async def lifespan():
            async with app.router.lifespan_context(app):
                ready.set()
                await finish.wait()

        startup = asyncio.create_task(lifespan())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert not ready.is_set()
            assert not app.state.control_watchdog.clients
            release.set()
            await asyncio.wait_for(ready.wait(), 2)
        finally:
            release.set()
            finish.set()
            await asyncio.wait_for(startup, 2)

    asyncio.run(exercise())


def test_start_stage_logs_failure_and_preserves_exception():
    recorder = recorder_stub()
    with pytest.raises(RuntimeError, match="original failure"):
        with recorder._record_start_stage("native_open"):
            raise RuntimeError("original failure")
    messages = [entry.msg for entry in recorder.logs.list_entries()]
    assert "stage=native_open" in messages[0] and "phase=begin" in messages[0]
    assert "phase=failed" in messages[-1] and "elapsed_ms=" in messages[-1]
