"""采集时标、起点和参与侧回归；仅替身与内存数据，不连接设备。"""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import backend.services.dataset_recorder as module
from backend.core.motion_safety import MotionSafetyGate
from backend.services.dataset_recorder import DatasetRecorderService, TimedRingBuffer, TimedSample


CONFIG = {"hal": {"mode": "real"}, "gripper": {"strokeMm": 26.0}, "storage": {}}
SELECTED = {"version": "appstation.participation.v1", "arms": ["right"], "grippers": ["right"]}


def recorder_stub(monkeypatch):
    monkeypatch.delenv("APPSTATION_HAL_MODE", raising=False)
    r = object.__new__(DatasetRecorderService)
    r._participation = dict(SELECTED)
    r._recording_config_snapshot = CONFIG
    r._gripper_target_buffer = TimedRingBuffer()
    r._sample_buffers = {"gripper": TimedRingBuffer(), "hal": TimedRingBuffer()}
    r._sample_buffers["hal"].append(TimedSample(
        "hal", 10., {"positions": [0.] * 12, "pulses": [0.] * 12},
        valid_until_unix_ms=time.time()*1000 + 1000))
    r._latest_action_delta_vector = lambda _t: [0.0] * 14
    r.teleop = SimpleNamespace(status=lambda *_args: {"nativeStatus": {"gripperTargets": [1.02, 0.0]}})
    return r


def test_gripper_label_uses_previous_target_not_future_close(monkeypatch):
    r = recorder_stub(monkeypatch)
    r._gripper_target_buffer.append(TimedSample("gripper_target", 9.98, (26.0, 0.0)))
    r._gripper_target_buffer.append(TimedSample("gripper_target", 10.1, (1.02, 0.0)))
    assert r._latest_action_vector([0.0] * 14, CONFIG, 10.0)[13] == 26.0
    assert r._latest_action_vector([0.0] * 14, CONFIG, 10.1)[13] == 1.02


@pytest.mark.parametrize("stamp", [None, 8.8, 10.1])
def test_missing_stale_or_future_only_target_cannot_be_fabricated(monkeypatch, stamp):
    r = recorder_stub(monkeypatch)
    if stamp is not None:
        r._gripper_target_buffer.append(TimedSample("gripper_target", stamp, (26.0, 0.0)))
    with pytest.raises(RuntimeError, match="目标历史"):
        r._latest_action_vector([0.0] * 14, CONFIG, 10.0)


def test_sampler_records_target_timestamp_separately_from_position_read(monkeypatch):
    r = recorder_stub(monkeypatch)
    native = {"dds_stamp_monotonic_ms": 10100.0, "gripperTargets": [1.02, None], "grippers": {}}
    r.hal = SimpleNamespace(command=AsyncMock(return_value={"response": native}))
    r._gripper_source_sync = lambda *args, **kwargs: TimedSample("gripper", 9.97, [26.0, 0.0])
    sample = r._sample_source_once_sync("gripper", CONFIG, 10.0)
    assert sample.monotonic_s == 9.97
    assert r._gripper_target_buffer.at_or_before(10.0, 1.0) is None
    assert r._gripper_target_buffer.at_or_before(10.1, 0.05).value == (1.02, 0.0)


def test_inflight_sampler_does_not_write_targets_into_next_episode(monkeypatch):
    r = recorder_stub(monkeypatch)
    old = r._gripper_target_buffer

    async def command(*args):
        r._gripper_target_buffer = TimedRingBuffer()
        return {"response": {"monotonic_s": 10.0, "gripperTargets": [26.0, 0.0]}}

    r.hal = SimpleNamespace(command=command)
    r._gripper_source_sync = lambda *args, **kwargs: None
    r._sample_source_once_sync("gripper", CONFIG, 10.0)
    assert len(old) == 1
    assert len(r._gripper_target_buffer) == 0


@pytest.mark.parametrize("target,actual,valid", [(26., 26., True), (8.1, 8.1, False),
                                                (26., 8.1, False), (8.1, 26., False)])
def test_start_checks_both_target_and_measurement(monkeypatch, target, actual, valid):
    r = recorder_stub(monkeypatch)
    r._gripper_target_buffer.append(TimedSample("gripper_target", 10., (target, 0.)))
    r._sample_buffers["gripper"].append(TimedSample(
        "gripper", 10., [actual, 0.], valid_until_unix_ms=time.time()*1000 + 1000))
    assert (r._record_start_feedback_blocker(CONFIG, 10.) == "") is valid


def test_start_rejects_old_feedback_and_supports_frozen_task_target(monkeypatch):
    r = recorder_stub(monkeypatch)
    r._gripper_target_buffer.append(TimedSample("gripper_target", 10., (12., 0.)))
    sample = TimedSample("gripper", 10., [12., 0.], valid_until_unix_ms=time.time()*1000 - 1)
    r._sample_buffers["gripper"].append(sample)
    config = {**CONFIG, "storage": {"recordStartGripperMm": 12.}}
    assert r._record_start_feedback_blocker(config, 10.)
    r._sample_buffers["gripper"].clear()
    r._sample_buffers["gripper"].append(module.replace(sample, valid_until_unix_ms=time.time()*1000 + 1000))
    assert r._record_start_feedback_blocker(config, 10.) == ""


def test_stability_window_restarts_after_bad_sample(monkeypatch):
    r = recorder_stub(monkeypatch)
    r.safety = MotionSafetyGate()
    r._sampler_start_monotonic_s = r._sampler_schedule_start_s = time.monotonic()
    r.validate_start_origin = AsyncMock()
    calls = []

    def blocker(*args):
        calls.append(time.monotonic())
        return "开口未到位" if len(calls) == 3 else ""

    r._record_start_feedback_blocker = blocker
    monkeypatch.setattr(module, "RECORD_START_STABLE_S", .05)
    r.hal = SimpleNamespace(command=AsyncMock(side_effect=AssertionError("不得驱动设备")))
    asyncio.run(r._wait_for_record_start_ready(CONFIG, r.safety.capture()))
    assert calls[-1] - calls[3] >= .05
    assert r.validate_start_origin.await_count == len(calls)
    r.hal.command.assert_not_awaited()


def test_start_gate_timeout_and_missing_origin_validator_fail_closed(monkeypatch):
    r = recorder_stub(monkeypatch)
    r.safety = MotionSafetyGate()
    with pytest.raises(RuntimeError, match="未装配"):
        asyncio.run(r._wait_for_record_start_ready(CONFIG, r.safety.capture()))
    r._sampler_start_monotonic_s = r._sampler_schedule_start_s = time.monotonic()
    r.validate_start_origin = AsyncMock()
    r._record_start_feedback_blocker = lambda *args: "开口未到位"
    monkeypatch.setattr(module, "RECORD_START_TIMEOUT_S", .05)
    with pytest.raises(RuntimeError, match="开口未到位"):
        asyncio.run(r._wait_for_record_start_ready(CONFIG, r.safety.capture()))


def test_arm_does_not_admit_frames_until_warmup_and_gate_complete(monkeypatch):
    r = recorder_stub(monkeypatch)
    r.safety = MotionSafetyGate()
    r._lock = asyncio.Lock()
    r._participation = None  # 本测试只隔离启用顺序，真实门槛由上面用例验证。
    r._accepting_frame_jobs = False
    r._sampler_start_monotonic_s = 9.
    r.telemetry = SimpleNamespace(recording=False)
    r._wait_for_fresh_native_gripper_feedback = AsyncMock()
    calls = []

    async def stage(name):
        assert not r._accepting_frame_jobs
        assert not r.telemetry.recording
        calls.append(name)

    r._wait_for_episode_warmup = lambda: stage("warmup")
    r._wait_for_record_start_ready = lambda *args: stage("gate")
    r._episode_clock_pair = AsyncMock(return_value=(10., 20.))
    asyncio.run(r._arm_episode_after_feedback(CONFIG, r.safety.capture()))
    assert calls == ["warmup", "gate"]
    assert r._episode_start_monotonic_s == 10.
    assert r._record_loop_start_monotonic_s == 20.
    assert r._accepting_frame_jobs and r.telemetry.recording


@pytest.mark.parametrize("grippers", [[], ["right"]])
def test_quality_uses_participation_for_stationary_and_rotation_only_episodes(monkeypatch, grippers):
    r = recorder_stub(monkeypatch)
    r._participation = {**SELECTED, "grippers": grippers}
    r._episode_frames = 0
    r._reset_training_quality_tracking()
    for roll in [0., 100.]:
        state = [0.] * 14
        action = [0.] * 14
        state[13] = action[13] = 8.1
        action[10] = roll
        r._track_training_quality_frame({"observation.state": state, "action": action})
        r._episode_frames += 1
    summary = r._training_quality_summary()
    assert summary["activeDatasetSides"] == ["right"]
    report = r._quality_assessment({"frames": 2, "trainingQuality": summary})
    codes = {x["code"] for x in report["reasons"]}
    assert "active_side_unknown" not in codes
    assert ("start_gripper_action" in codes) is bool(grippers)


@pytest.mark.parametrize("side,index", [("left", 6), ("right", 13)])
def test_target_history_preserves_operator_side_contract(monkeypatch, side, index):
    r = recorder_stub(monkeypatch)
    r._participation = {**SELECTED, "arms": [side], "grippers": [side]}
    r._gripper_target_buffer.append(TimedSample("gripper_target", 10., (12., 24.)))
    action = r._latest_action_vector([0.] * 14, CONFIG, 10.)
    assert action[index] == (24. if side == "left" else 12.)


def test_delayed_target_is_recorded_with_alignment_warning(monkeypatch):
    r = recorder_stub(monkeypatch)
    r._gripper_target_buffer.append(TimedSample("gripper_target", 9.94, (26., 0.)))
    r.telemetry = SimpleNamespace(motion_positions=[0.] * 12, gripper_positions=[26., 0.])
    r._last_motion_pulses = [0.] * 12
    r._record_fps_hz = 30
    r._episode_index = 0
    r._recording_motion_positions = lambda _c, p, _pulse: p
    r._camera_placeholder_value = lambda _key: b"offline"

    def aligned(source, stamp):
        value = {"positions": [0.] * 12, "pulses": [0.] * 12} if source == "hal" else [26., 0.]
        return TimedSample(source, stamp, value, valid_until_unix_ms=time.time()*1000 + 1000)

    r._aligned_sample = aligned
    frame = module.FrameAssembler(r).assemble(CONFIG, 10., 0)
    assert frame["action"][13] == 26.
    assert frame["alignmentExceptions"][0]["source"] == "gripper_target"
    assert frame["alignmentExceptions"][0]["skewMs"] == -60.
    assert r._quality_assessment({"frames": 1, "alignmentExceptions": frame["alignmentExceptions"]})["recommendation"] == "review"


def test_start_checks_fresh_motion_even_without_gripper(monkeypatch):
    r = recorder_stub(monkeypatch)
    r._participation = {**SELECTED, "grippers": []}
    assert r._record_start_feedback_blocker(CONFIG, 10.) == ""
    assert "机械臂" in r._record_start_feedback_blocker(CONFIG, 10.2)


def test_stop_during_start_gate_never_admits_frames(monkeypatch):
    r = recorder_stub(monkeypatch)
    r.safety = MotionSafetyGate()
    r._accepting_frame_jobs = False
    r.telemetry = SimpleNamespace(recording=False)
    r._sampler_start_monotonic_s = r._sampler_schedule_start_s = time.monotonic()

    async def stop_while_checking():
        r.safety.interrupt(emergency=True)

    r.validate_start_origin = stop_while_checking
    with pytest.raises(RuntimeError, match="emergency stop"):
        asyncio.run(r._wait_for_record_start_ready(CONFIG, r.safety.capture()))
    assert not r._accepting_frame_jobs and not r.telemetry.recording
