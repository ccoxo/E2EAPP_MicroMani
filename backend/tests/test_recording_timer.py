"""录制定时精度的资源配对与会话异常清理，不连接真实设备。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.services import recording_timer, dataset_recorder
from backend.core.config import default_config


def fake_winmm(monkeypatch, result=0):
    dll = SimpleNamespace(timeBeginPeriod=Mock(return_value=result), timeEndPeriod=Mock(return_value=0))
    monkeypatch.setattr(recording_timer, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(recording_timer, "ctypes", SimpleNamespace(WinDLL=Mock(return_value=dll), c_uint=int))
    return dll


def test_timer_balances_repeated_start_close_and_next_session(monkeypatch):
    dll = fake_winmm(monkeypatch)
    scope = recording_timer.RecordingTimerScope()
    scope.start()
    scope.start()
    dll.timeBeginPeriod.assert_called_once_with(1)
    scope.close()
    scope.close()
    dll.timeEndPeriod.assert_called_once_with(1)
    scope.start()
    scope.close()
    assert dll.timeBeginPeriod.call_count == dll.timeEndPeriod.call_count == 2


def test_failed_timer_request_has_no_unmatched_release(monkeypatch):
    dll = fake_winmm(monkeypatch, result=97)
    scope = recording_timer.RecordingTimerScope()
    with pytest.raises(RuntimeError, match="97"):
        scope.start()
    scope.close()
    dll.timeEndPeriod.assert_not_called()


def test_non_windows_does_not_load_winmm(monkeypatch):
    fake_winmm(monkeypatch)
    monkeypatch.setattr(recording_timer, "sys", SimpleNamespace(platform="linux"))
    scope = recording_timer.RecordingTimerScope()
    scope.start()
    scope.close()
    recording_timer.ctypes.WinDLL.assert_not_called()


@pytest.mark.parametrize("failure", [None, RuntimeError("cleanup"), asyncio.CancelledError()])
def test_session_finish_releases_timer_on_success_error_and_cancellation(monkeypatch, failure):
    dll = fake_winmm(monkeypatch)
    recorder = object.__new__(dataset_recorder.DatasetRecorderService)
    recorder._recording_timer_scope = recording_timer.RecordingTimerScope()
    recorder._recording_timer_scope.start()

    async def finish():
        if failure is not None:
            raise failure
        return {"active": False}

    recorder._finish_session = finish
    if failure is None:
        assert asyncio.run(recorder.finish_session()) == {"active": False}
    else:
        with pytest.raises(type(failure)):
            asyncio.run(recorder.finish_session())
    assert recorder._recording_timer_scope is None
    dll.timeEndPeriod.assert_called_once_with(1)


@pytest.mark.parametrize("failure", [RuntimeError("startup"), asyncio.CancelledError()])
def test_start_failure_after_timer_acquisition_releases_request(monkeypatch, tmp_path, failure):
    dll = fake_winmm(monkeypatch)
    config = default_config()
    config["hal"]["mode"] = "mock"
    config["storage"]["datasetRoot"] = str(tmp_path / "datasets")
    async def noop(*args, **kwargs):
        return True
    recorder = dataset_recorder.DatasetRecorderService(
        SimpleNamespace(get_config=lambda: config), SimpleNamespace(), SimpleNamespace(),
        SimpleNamespace(recording=False, episode_count=0, frame_count=0),
        SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None),
        SimpleNamespace(stop=noop, status=lambda: {}),
    )
    monkeypatch.setattr(recorder, "_try_begin_native_dataset", noop)
    monkeypatch.setattr(recorder, "_write_appstation_info", lambda *a: None)
    monkeypatch.setattr(recorder, "_next_episode_index", lambda *a: 0)
    async def clock_failure(*args):
        dll.timeBeginPeriod.assert_called_once_with(1)
        raise failure
    monkeypatch.setattr(recorder, "_episode_clock_pair", clock_failure)
    async def run():
        with pytest.raises(type(failure)):
            await recorder.start_session("unit", "task")
        assert recorder._recording_timer_scope is None
        assert recorder._writer_thread is None
        dll.timeEndPeriod.assert_called_once_with(1)
    asyncio.run(run())
