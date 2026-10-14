import asyncio
import queue
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core.defaults import default_config
from backend.services.dataset_recorder import DatasetRecorderService, DatasetSaveError, LeRobotWriterThread, WriterCommand


def make_recorder():
    recorder = DatasetRecorderService(
        settings=SimpleNamespace(get_config=default_config),
        hardware=SimpleNamespace(), hal=SimpleNamespace(),
        telemetry=SimpleNamespace(recording=False),
        logs=SimpleNamespace(warning=Mock(), error=Mock(), info=Mock()),
        teleop=SimpleNamespace(stop=AsyncMock(), status=lambda: {}),
    )
    recorder._session_active = True
    recorder._recording = False
    recorder._safety_interrupted = True
    recorder._episode_frames = 127
    recorder._native_writer_active = lambda: True
    recorder._drain_recording_queues = AsyncMock()
    recorder._native_writer_command = AsyncMock()
    recorder._stop_assembler_task = AsyncMock()
    recorder._stop_sampler_tasks = AsyncMock()
    recorder._finalize_native_dataset = AsyncMock()
    recorder._stop_writer_task = AsyncMock()
    return recorder


def test_discard_interrupted_episode_clears_native_buffer():
    async def run():
        recorder = make_recorder()
        await recorder.discard_episode()
        recorder._drain_recording_queues.assert_awaited_once()
        recorder._native_writer_command.assert_awaited_once_with("clear_episode")
        assert recorder.status()["resetPending"]
        assert not recorder.status()["recording"]
    asyncio.run(run())


def test_failed_clear_does_not_report_discard_success_and_can_retry():
    async def run():
        recorder = make_recorder()
        recorder._native_writer_command.side_effect = RuntimeError("encoder busy")
        with pytest.raises(DatasetSaveError, match="clear_episode"):
            await recorder.discard_episode()
        assert not recorder.status()["resetPending"]
        recorder._native_writer_command.side_effect = None
        await recorder.discard_episode()
        assert recorder._native_writer_command.await_count == 2
        assert recorder.status()["resetPending"]
    asyncio.run(run())


def test_saved_discard_marks_metadata_without_clearing_a_new_buffer():
    async def run():
        recorder = make_recorder()
        episode = {"id": "episode_000022"}
        recorder._last_saved_episode = episode
        recorder._mark_saved_episode_deleted_locked = Mock()
        await recorder.discard_episode()
        recorder._mark_saved_episode_deleted_locked.assert_called_once_with(episode)
        recorder._native_writer_command.assert_not_awaited()
    asyncio.run(run())


def test_finish_after_interruption_clears_unsaved_buffer_before_finalize():
    async def run():
        recorder = make_recorder()
        async def finalize():
            recorder._native_writer_command.assert_awaited_once_with("clear_episode")
        recorder._finalize_native_dataset = finalize
        await recorder.finish_session()
        assert not recorder.status()["active"]
    asyncio.run(run())


@pytest.mark.parametrize("native_size, expected_size, accepted", [(763, 636, False), (636, 636, True)])
def test_writer_does_not_save_mixed_attempts(native_size, expected_size, accepted):
    recorder = SimpleNamespace(_episode_frames=expected_size)
    writer = LeRobotWriterThread(recorder, queue.Queue())
    saved = Mock()
    writer._dataset = SimpleNamespace(
        writer=SimpleNamespace(episode_buffer={"size": native_size}),
        save_episode=saved, meta=SimpleNamespace(total_frames=15554),
    )
    result = Future()
    writer._handle_command(WriterCommand("save_episode", result))
    if accepted:
        assert result.result() == 15554
        saved.assert_called_once_with(parallel_encoding=True)
    else:
        with pytest.raises(RuntimeError, match="frame count mismatch"):
            result.result()
        saved.assert_not_called()
