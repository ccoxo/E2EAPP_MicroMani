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


