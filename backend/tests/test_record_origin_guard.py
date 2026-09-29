from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app


@pytest.mark.parametrize("path", ["/api/motion/home_all", "/api/motion/left/return_origin", "/api/motion/right/return_origin"])
@pytest.mark.parametrize("phase", ["recording", "starting", "interrupted", "discarding", "saved", "discarded"])
def test_work_origin_return_waits_for_recording_to_be_processed(tmp_path, monkeypatch, path, phase):
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_DISABLE_CAMERA_PROBE", "1")
    app = create_app(tmp_path)
    recorder = app.state.recorder
    recorder._session_active = phase != "starting"
    recorder._session_starting = phase == "starting"
    recorder._recording = phase == "recording"
    recorder._reset_pending = phase in {"saved", "discarded", "discarding"}
    recorder._discard_in_progress = phase == "discarding"
    recorder._episode_frames = 180
    commands = app.state.services.commands
    commands.home_all = AsyncMock(return_value={"ok": True})
    commands.return_motion_origin_side = AsyncMock(return_value={"ok": True})
    recorder.save_episode = AsyncMock()
    recorder.discard_episode = AsyncMock()
    result = TestClient(app).post(path)
    if phase in {"saved", "discarded"}:
        assert result.status_code == 200, result.text
        assert commands.home_all.await_count + commands.return_motion_origin_side.await_count == 1
    else:
        assert result.status_code == 409, result.text
        assert result.json()["detail"]["code"] == "RECORDING_ACTIVE"
        assert "保存或丢弃" in result.json()["detail"]["message"]
        commands.home_all.assert_not_awaited()
        commands.return_motion_origin_side.assert_not_awaited()
    recorder.save_episode.assert_not_awaited()
    recorder.discard_episode.assert_not_awaited()
    assert recorder._episode_frames == 180
