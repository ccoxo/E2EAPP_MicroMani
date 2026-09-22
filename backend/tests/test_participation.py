from copy import deepcopy
from types import SimpleNamespace
import time
import pytest
from backend.core.participation import participation, action_mask, hardware_sides, scoped_config
from backend.core.defaults import default_config
from backend.services.dataset_recorder import DatasetRecorderService
from backend.services.teleop_mapping import TeleopMappingService

SINGLE = {"version": "appstation.participation.v1", "arms": ["left"], "grippers": []}

@pytest.mark.parametrize("value", [None, {}, {**SINGLE, "arms": []}, {**SINGLE, "arms": ["left", "left"]}, {**SINGLE, "grippers": ["right"]}, {**SINGLE, "version": "unknown"}])
def test_invalid_participation_is_rejected(value):
    with pytest.raises(ValueError):
        participation(value)


def test_mask_and_scope_use_operator_mapping_without_mutating_settings():
    config = default_config()
    original = deepcopy(config)
    scoped = scoped_config(config, SINGLE)
    assert config == original
    assert action_mask(SINGLE) == [True]*6 + [False]*8
    assert hardware_sides(SINGLE) == ["right"]
    assert scoped["teleop"]["leftEnabledAxes"] == [False]*6
    assert not scoped["teleop"]["rightConnected"]
    assert not scoped["teleop"]["rightGripperParticipating"]


def test_record_reset_uses_selected_hardware_side():
    recorder = object.__new__(DatasetRecorderService)
    recorder._participation = SINGLE
    recorder._enter_reset_pending_locked()
    assert recorder._reset_required_sides_locked() == {"right"}
    recorder.mark_reset_origin_returned("left")
    assert not recorder._reset_ready_locked()
    recorder.mark_reset_origin_returned("right")
    assert recorder._reset_ready_locked()


def test_record_gripper_missing_inactive_feedback_is_masked_but_active_is_rejected():
    recorder = object.__new__(DatasetRecorderService)
    recorder._participation = {**SINGLE, "grippers": ["left"]}
    native = {"grippers": {"right": {"positionMm": 12., "positionOk": True, "positionSampleTs": time.time()*1000}}}
    recorder.teleop = SimpleNamespace(status=lambda: {"nativeStatus": native})
    result = recorder._latest_native_gripper_sample({})
    assert result[0] == (0., 12.)
    native["grippers"]["right"]["positionOk"] = False
    with pytest.raises(RuntimeError, match="反馈无效"):
        recorder._latest_native_gripper_sample({})


def test_native_payload_masks_unselected_arm_and_grippers():
    config = default_config()
    config["teleop"].update(leftConnected=True, rightConnected=True)
    mapper = TeleopMappingService(None, None, None)
    mapper._arm_sources.add("recording")
    mapper.recording_participation = lambda: SINGLE
    payload = mapper._native_payload(config)
    assert payload["leftConnected"]
    assert not payload["rightConnected"]
    assert payload["leftEnabledAxes"] == [False]*6
    assert not payload["leftGripperParticipating"]
    assert not payload["rightGripperParticipating"]


def test_scope_respects_unswapped_master_mapping():
    config = default_config()
    config["teleop"].update(swapTeleopChannels=False, leftConnected=True, rightConnected=True)
    scoped = scoped_config(config, SINGLE)
    assert not scoped["teleop"]["leftConnected"]
    assert scoped["teleop"]["rightConnected"]


def test_record_route_preserves_selection_and_rejects_invalid_input(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from fastapi.testclient import TestClient
    from backend.app import create_app
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_DISABLE_CAMERA_PROBE", "1")
    app = create_app(tmp_path)
    recorder = app.state.recorder
    recorder.start_session = AsyncMock(return_value={"participation": SINGLE})
    app.state.services.teleop_mapper.stop = AsyncMock()
    client = TestClient(app)
    result = client.post("/api/record/session/create", json={"dataset_name": "single", "task": "test", "participation": SINGLE})
    assert result.status_code == 200, result.text
    recorder.start_session.assert_awaited_once_with("single", "test", SINGLE)
    result = client.post("/api/record/session/create", json={"participation": {**SINGLE, "arms": []}})
    assert result.status_code == 409
    assert recorder.start_session.await_count == 1


def test_selected_frame_rejects_missing_feedback_instead_of_filling_zeros():
    from backend.services.dataset_recorder import FrameAssembler, TimedSample
    recorder = object.__new__(DatasetRecorderService)
    recorder._participation = SINGLE
    recorder.telemetry = SimpleNamespace(motion_positions=[0.]*12, force_left=[0.]*6, force_right=[0.]*6)
    recorder._last_motion_pulses = [0.]*12
    recorder._real_hardware_mode = lambda config: True
    recorder._aligned_sample = lambda source, stamp: TimedSample(source, stamp, None, ok=False, stale=True)
    with pytest.raises(RuntimeError, match="拒绝写入伪造观测"):
        FrameAssembler(recorder).assemble({}, 1., 0)
