"""复用现场偏差，离线验证数据质量告警与反馈失效的边界。"""
from types import SimpleNamespace
import asyncio
import pytest
from backend.services import dataset_recorder as recording


def make_recorder(monkeypatch):
    monkeypatch.setattr(recording, "now_ms", lambda: 1_000_000)
    recorder = object.__new__(recording.DatasetRecorderService)
    recorder._participation = {"version": "appstation.participation.v1", "arms": ["right"], "grippers": ["right"]}
    recorder.telemetry = SimpleNamespace(motion_positions=[999.] * 12, force_left=[0.] * 6,
                                        force_right=[0.] * 6, gripper_positions=[999., 999.])
    recorder._last_motion_pulses = [999.] * 12
    recorder._real_hardware_mode = lambda c: True
    recorder._record_source_quality = lambda *args: None
    recorder._recording_motion_positions = lambda c, p, pulses: p
    recorder._force_values_from_sample = lambda v: None
    recorder._compose_observation_state = lambda p, g: p + g
    recorder._camera_placeholder_value = lambda c: b"offline"
    recorder._latest_action_vector = lambda state, config, stamp: state
    recorder._record_fps_hz = 30
    recorder._episode_index = 0
    recorder._sample_buffers = {source: recording.TimedRingBuffer() for source in recording.SOURCE_KEYS}
    # 非本测试目标的输入也提供有效替身，避免触发相机或 SDK。
    recorder._fallback_sample = lambda source, stamp, message: recording.TimedSample(source, stamp, None, ok=False, stale=True)
    return recorder


def sample(source, stamp, *, expired=False):
    value = {"positions": [1.] * 12, "pulses": [123.] * 12} if source == "hal" else [12., 0.]
    return recording.TimedSample(source, stamp, value, valid_until_unix_ms=999_999 if expired else 1_000_200,
                                 measurement_unix_ms={"source": 999_700})


@pytest.mark.parametrize("source,skew", [("gripper", -.067667), ("gripper", -.050333),
                                        ("hal", -.019333), ("hal", -.023667)])
def test_field_traces_keep_real_values_and_record_alignment(monkeypatch, source, skew):
    recorder = make_recorder(monkeypatch)
    for key in ("hal", "gripper"):
        recorder._sample_buffers[key].append(sample(key, 10. + (skew if key == source else 0.)))
    if skew == -.019333:
        recorder._sample_buffers["hal"].append(recording.TimedSample(
            "hal", 9.994667, None, ok=False, stale=True, timed_out=True))
    frame = recording.FrameAssembler(recorder).assemble({}, 10., 176)
    assert frame["frame_index"] == 176 and frame["timestamp"] == 176 / 30
    assert frame["observation.pulses"] == [123.] * 12
    assert 999. not in frame["observation.state"]
    events = frame["alignmentExceptions"]
    if abs(skew) > recording.SOURCE_MAX_SKEW_S[source]:
        assert len(events) == 1
        assert events[0]["source"] == source and events[0]["frameIndex"] == 176
        assert events[0]["skewMs"] == pytest.approx(skew * 1000)
        assert events[0]["measurementUnixMs"] == {"source": 999_700}
    else:
        assert events == []


@pytest.mark.parametrize("fault", ["expired", "missing", "timeout", "nan", "infinite", "no_timestamp"])
@pytest.mark.parametrize("source", ["hal", "gripper"])
def test_invalid_feedback_cannot_be_downgraded_to_alignment_warning(monkeypatch, source, fault):
    recorder = make_recorder(monkeypatch)
    for key in ("hal", "gripper"):
        item = sample(key, 10., expired=key == source and fault == "expired")
        if key == source:
            if fault == "missing":
                continue
            if fault == "timeout":
                item = recording.TimedSample(key, 10., None, ok=False, stale=True, timed_out=True)
            elif fault in ("nan", "infinite"):
                value = float("nan" if fault == "nan" else "inf")
                (item.value["pulses"] if key == "hal" else item.value)[0] = value
            elif fault == "no_timestamp":
                item = recording.TimedSample(key, 10., item.value)
        recorder._sample_buffers[key].append(item)
    with pytest.raises(RuntimeError, match="反馈无效"):
        recording.FrameAssembler(recorder).assemble({}, 10., 0)


def test_alignment_events_persist_only_after_frame_write_and_require_review(monkeypatch, tmp_path):
    recorder = make_recorder(monkeypatch)
    recorder._reset_training_quality_tracking()
    recorder._track_training_quality_frame = lambda frame: None
    recorder._episode_frames = 0
    recorder._max_force_left = recorder._max_force_right = 0.
    for key in ("hal", "gripper"):
        recorder._sample_buffers[key].append(sample(key, 9.93 if key == "gripper" else 10.))
    frame = recording.FrameAssembler(recorder).assemble({}, 10., 4)
    assert recorder._alignment_exceptions == []
    recorder._mark_frame_written(frame)
    episode = {"id": "episode_000000", "frames": 1, "alignmentExceptions": recorder._alignment_exceptions}
    episode["qualityAssessment"] = recorder._quality_assessment(episode)
    (tmp_path / "meta").mkdir()
    recorder._write_episodes(tmp_path, [episode])
    restored = recorder._read_episodes(tmp_path)[0]
    assert restored["alignmentExceptions"] == frame["alignmentExceptions"]
    assert restored["qualityAssessment"]["recommendation"] == "review"
    assert any(r["code"] == "alignment_exceeded" for r in restored["qualityAssessment"]["reasons"])


def test_new_gripper_status_packets_do_not_renew_old_measurement(monkeypatch):
    recorder = make_recorder(monkeypatch)
    monkeypatch.setattr(recording.time, "time", lambda: 1000.)
    native = {"dds_stamp_unix_ms": 999_950, "monotonic_s": 9.95, "grippers": {
        "left": {"positionMm": 12., "positionOk": True, "positionSampleTs": 999_300}}}
    first = recorder._gripper_source_sync({}, 10., record_quality=False, native_status=native)
    native.update(dds_stamp_unix_ms=1_000_000, monotonic_s=10.)
    second = recorder._gripper_source_sync({}, 10., record_quality=False, native_status=native)
    assert first.monotonic_s == pytest.approx(9.3)
    assert second.monotonic_s == pytest.approx(first.monotonic_s)
    assert first.valid_until_unix_ms == second.valid_until_unix_ms == 1_000_300
    assert recorder._feedback_is_valid(second)
    monkeypatch.setattr(recording, "now_ms", lambda: 1_000_301)
    assert not recorder._feedback_is_valid(second)


def test_hal_validity_uses_original_dds_timestamp(monkeypatch):
    recorder = make_recorder(monkeypatch)
    async def read():
        return {"positions": [1.]*12, "pulses": [123.]*12,
                "dds_stamp_unix_ms": 999_800, "monotonic_s": 9.8}
    observed = asyncio.run(recorder._timed_source("hal", read(), 10., record_quality=False))
    assert observed.valid_until_unix_ms == 1_000_300
    assert recorder._feedback_is_valid(observed)
    monkeypatch.setattr(recording, "now_ms", lambda: 1_000_301)
    assert not recorder._feedback_is_valid(observed)
