"""录制准备期动作锁定回归：仅内存替身，不连接设备。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.core.defaults import default_config
from backend.core.logging import LogService
from backend.services.teleop_mapping import TeleopMappingService
from backend.services.dataset_recorder import DatasetRecorderService


def mapper(monkeypatch, supported=True):
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    config = default_config()
    config["hal"]["mode"] = "real"
    hal = SimpleNamespace(
        health=AsyncMock(return_value=SimpleNamespace(capabilities=["record_start_hold_v1"] if supported else [])),
        command=AsyncMock(return_value={"ok": True}),
    )
    service = TeleopMappingService(SimpleNamespace(get_config=lambda: config), hal, LogService(emit_startup=False))
    return service


def test_prepare_checks_capability_before_sending_any_native_command(monkeypatch):
    service = mapper(monkeypatch, supported=False)
    with pytest.raises(RuntimeError, match="HAL"):
        asyncio.run(service.start("recording", pre_home=False, prepare_only=True))
    service.hal.command.assert_not_awaited()


def test_prepare_payload_holds_input_and_release_preserves_configuration(monkeypatch):
    service = mapper(monkeypatch)

    async def native(config, *args):
        service._arm_sources.add("recording")
        service._last_native_payload = service._native_payload(config)
        return {}

    service._start_native_locked = native

    async def exercise():
        await service.start("recording", pre_home=False, prepare_only=True)
        held = dict(service._last_native_payload)
        assert held["recordingPreparing"] is True
        await service.release_recording()
        service.hal.command.assert_awaited_once_with(
            "teleop.native.configure", {**held, "recordingPreparing": False})
        assert service._last_native_payload["recordingPreparing"] is False

    asyncio.run(exercise())


@pytest.mark.parametrize("stopped", [False, True])
def test_release_rejects_missing_preparation_or_safety_stop(monkeypatch, stopped):
    service = mapper(monkeypatch)
    if stopped:
        service._arm_sources.add("recording")
        service._last_native_payload = {"recordingPreparing": True}
        service.safety.interrupt(emergency=True)
    with pytest.raises(RuntimeError):
        asyncio.run(service.release_recording())
    service.hal.command.assert_not_awaited()


@pytest.mark.parametrize("outcome", ["ready", "failed", "cancelled"])
def test_recording_cannot_release_input_before_feedback_gate(tmp_path, monkeypatch, outcome):
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    config = default_config()
    config["hal"]["mode"] = "real"
    config["storage"]["datasetRoot"] = str(tmp_path / "datasets")
    teleop = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), release_recording=AsyncMock(), status=lambda: {})
    recorder = DatasetRecorderService(
        SimpleNamespace(get_config=lambda: config),
        SimpleNamespace(force_state=AsyncMock(return_value={})),
        SimpleNamespace(),
        SimpleNamespace(recording=False, episode_count=0, frame_count=0),
        LogService(emit_startup=False), teleop,
    )
    for name in ("_try_begin_native_dataset",):
        monkeypatch.setattr(recorder, name, AsyncMock(return_value=True))
    for name in ("_write_appstation_info", "_start_sampler_tasks_locked"):
        monkeypatch.setattr(recorder, name, lambda *args: None)
    monkeypatch.setattr(recorder, "_next_episode_index", lambda *args: 0)
    monkeypatch.setattr(recorder, "_native_writer_active", lambda: True)
    monkeypatch.setattr(recorder, "_refresh_gripper_cache", AsyncMock())
    monkeypatch.setattr(recorder, "_episode_clock_pair", AsyncMock(return_value=(10., 20.)))
    monkeypatch.setattr(recorder, "_wait_for_episode_warmup", AsyncMock())
    monkeypatch.setattr(recorder, "_record_loop", AsyncMock())
    monkeypatch.setattr(recorder, "_frame_assembler_loop", AsyncMock())

    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()

        async def gate(*args):
            entered.set()
            await release.wait()
            if outcome == "failed":
                raise RuntimeError("feedback unavailable")
            recorder._accepting_frame_jobs = True

        async def enable():
            assert recorder._accepting_frame_jobs
            assert not recorder.telemetry.recording
            assert recorder._loop_task is not None and recorder._assembler_task is not None

        teleop.release_recording.side_effect = enable
        monkeypatch.setattr(recorder, "_arm_episode_after_feedback", gate)
        task = asyncio.create_task(recorder.start_session("unit", "task"))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            teleop.start.assert_awaited_once_with("recording", pre_home=False, prepare_only=True)
            teleop.release_recording.assert_not_awaited()
            assert recorder.status()["recording"] is False
            if outcome == "cancelled":
                task.cancel()
            release.set()
            if outcome == "ready":
                await task
                teleop.release_recording.assert_awaited_once()
                assert recorder.telemetry.recording
            else:
                with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else RuntimeError):
                    await task
                teleop.release_recording.assert_not_awaited()
                assert not recorder.telemetry.recording
                assert not recorder._accepting_frame_jobs
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await recorder.finish_session()

    asyncio.run(exercise())
