from copy import deepcopy
import json
from types import SimpleNamespace
import time
import pytest
from backend.core.participation import participation, action_mask, hardware_sides, scoped_config
from backend.core.defaults import default_config
from backend.services.dataset_recorder import DatasetRecorderService
from backend.services.teleop_mapping import TeleopMappingService

SINGLE = {"version": "appstation.participation.v1", "arms": ["left"], "grippers": []}

@pytest.mark.parametrize("valid_skew", [-.019333, .019333, -.020333, None])
def test_hal_alignment_does_not_prefer_timeout_placeholder_to_valid_measurement(valid_skew):
    from backend.services.dataset_recorder import TimedRingBuffer, TimedSample
    recorder = object.__new__(DatasetRecorderService)
    recorder._participation = SINGLE
    recorder._sample_buffers = {"hal": TimedRingBuffer()}
    recorder._record_source_quality = lambda *args: None
    recorder._fallback_sample = lambda source, target, message: TimedSample(
        source, target, {"positions": [999.]}, ok=False, stale=True)
    target = 46918.48633333334
    if valid_skew is not None:
        recorder._sample_buffers["hal"].append(TimedSample(
            "hal", target + valid_skew, {"positions": [42.]}, valid_until_unix_ms=time.time()*1000 + 500))
    recorder._sample_buffers["hal"].append(TimedSample(
        "hal", target - .005333, None, ok=False, stale=True, timed_out=True, message="hal timeout"))
    result = recorder._aligned_sample("hal", target)
    if valid_skew is not None:
        assert result.ok
        assert result.alignment_exceeded == (abs(valid_skew) > .020)
        assert result.value == {"positions": [42.]}
        assert result.monotonic_s == target + valid_skew
    else:
        assert not result.ok and result.stale and result.timed_out

def test_failure_timeline_keeps_bracketing_and_latest_samples_without_values():
    from backend.services.dataset_recorder import TimedRingBuffer, TimedSample
    buffer = TimedRingBuffer()
    for stamp in [9.8, 9.9, 9.949667, 10.06, 10.1, 10.2, 10.3]:
        buffer.append(TimedSample("gripper", stamp, [123.], poll_diagnostic={
            "startedMonotonicS": stamp + .01, "readMs": 3., "wakeLatenessMs": 4.}))
    details = buffer.failure_timeline(10.)
    assert details["count"] == 7
    assert details["latest"]["sampleMonotonicS"] == 10.3
    assert [s["sampleMonotonicS"] for s in details["nearby"]] == [9.8, 9.9, 9.949667, 10.06, 10.1, 10.2]
    assert details["nearby"][2]["skewMs"] == pytest.approx(-50.333)
    assert details["nearby"][2]["poll"]["readMs"] == 3.
    assert "value" not in details["latest"]
    assert TimedRingBuffer().failure_timeline(10.) == {"count": 0, "nearby": [], "latest": None}

@pytest.mark.parametrize("read_ok", [True, False])
def test_record_gripper_samples_dds_without_waiting_for_ui_cache(monkeypatch, read_ok):
    from unittest.mock import AsyncMock
    from backend.services.dataset_recorder import TimedRingBuffer
    monkeypatch.delenv("APPSTATION_HAL_MODE", raising=False)
    recorder = object.__new__(DatasetRecorderService)
    recorder._participation = {**SINGLE, "grippers": ["left"]}
    target = 44380.551666666666
    cached = {"monotonic_s": target - .067667, "grippers": {
        "right": {"positionMm": 12., "positionOk": True, "positionSampleTs": time.time()*1000}}}
    fresh = deepcopy(cached)
    fresh["monotonic_s"] = target - .002
    fresh["grippers"]["right"].update(positionMm=13., positionOk=read_ok)
    recorder.teleop = SimpleNamespace(status=lambda: {"nativeStatus": cached})
    recorder.hal = SimpleNamespace(command=AsyncMock(return_value={"response": fresh}))
    config = {"hal": {"mode": "real"}, "teleop": {"engine": "hal_native"}}
    if not read_ok:
        with pytest.raises(RuntimeError, match="反馈无效"):
            recorder._sample_source_once_sync("gripper", config, target)
    else:
        sample = recorder._sample_source_once_sync("gripper", config, target)
        assert sample.value == [0., 13.]
        recorder._sample_buffers = {"gripper": TimedRingBuffer()}
        recorder._sample_buffers["gripper"].append(sample)
        recorder._record_source_quality = lambda *args: None
        aligned = recorder._aligned_sample("gripper", target)
        assert aligned.ok and not aligned.stale
        assert aligned.monotonic_s == fresh["monotonic_s"]
    recorder.hal.command.assert_awaited_once_with("teleop.native.status", {})


@pytest.mark.parametrize("fault", ["missing", "expired", "timeout", "alignment"])
def test_direct_gripper_sampling_keeps_missing_and_stale_guards(monkeypatch, fault):
    from unittest.mock import AsyncMock
    from backend.services.dataset_recorder import TimedRingBuffer
    monkeypatch.delenv("APPSTATION_HAL_MODE", raising=False)
    recorder = object.__new__(DatasetRecorderService)
    recorder._participation = {**SINGLE, "grippers": ["left"]}
    native = {"monotonic_s": 10., "grippers": {"right": {
        "positionMm": 12., "positionOk": True, "positionSampleTs": time.time()*1000}}}
    recorder.teleop = SimpleNamespace(status=lambda: {"nativeStatus": native})
    fresh = deepcopy(native)
    if fault == "missing":
        fresh = {}
    elif fault == "expired":
        fresh["grippers"]["right"]["positionSampleTs"] -= 2000
    elif fault == "alignment":
        fresh["monotonic_s"] -= .067667
    recorder.hal = SimpleNamespace(command=AsyncMock(return_value={"response": fresh}))
    if fault == "timeout":
        recorder.hal.command.side_effect = RuntimeError("DDS timeout")
    config = {"hal": {"mode": "real"}, "teleop": {"engine": "hal_native"}}
    if fault != "alignment":
        with pytest.raises(RuntimeError):
            recorder._sample_source_once_sync("gripper", config, 10.)
    else:
        sample = recorder._sample_source_once_sync("gripper", config, 10.)
        recorder._sample_buffers = {"gripper": TimedRingBuffer()}
        recorder._sample_buffers["gripper"].append(sample)
        recorder._record_source_quality = lambda *args: None
        assert recorder._aligned_sample("gripper", 10.).stale

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


@pytest.mark.parametrize("failed_source", ["hal", "gripper"])
def test_feedback_failure_identifies_source_before_teleop_stop(failed_source):
    from backend.services.dataset_recorder import FrameAssembler, TimedSample
    recorder = object.__new__(DatasetRecorderService)
    recorder._participation = {**SINGLE, "grippers": ["left"]}
    recorder.telemetry = SimpleNamespace(motion_positions=[0.]*12, force_left=[0.]*6, force_right=[0.]*6)
    recorder._last_motion_pulses = [0.]*12
    recorder._real_hardware_mode = lambda config: True
    native = {"grippers": {"right": {"positionOk": False, "positionSampleTs": 1,
                                    "lastReadDurationMs": 123, "lastReadMessage": "READ timeout"}}}
    recorder.teleop = SimpleNamespace(status=lambda: {"nativeStatus": native})
    recorder._aligned_sample = lambda source, stamp: TimedSample(
        source, stamp - .06 if source == failed_source else stamp,
        {"positions": [0.]*12, "pulses": [0.]*12} if source == "hal" else [0., 0.],
        ok=True, stale=source == failed_source, elapsed_ms=7, message="invalid sample",
        valid_until_unix_ms=time.time()*1000 + 500)
    with pytest.raises(RuntimeError, match="拒绝写入伪造观测") as error:
        FrameAssembler(recorder).assemble({}, 10., 42)
    details = json.loads(str(error.value).split("diagnostic=", 1)[1])
    assert details["frameIndex"] == 42
    assert details["failedSources"] == [failed_source]
    sample = next(item for item in details["samples"] if item["source"] == failed_source)
    assert sample["skewMs"] == pytest.approx(-60)
    assert sample["ok"] is True and sample["stale"] is True
    assert details["nativeGrippers"]["right"]["lastReadMessage"] == "READ timeout"
