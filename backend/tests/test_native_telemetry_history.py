"""用离线 C++ 输出验证 Python 录制消费者；不接触 SDK/DDS/设备。"""
import json
import subprocess
from pathlib import Path

import pytest

from backend.services.dataset_recorder import DatasetRecorderService

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def payloads():
    executable = ROOT / "hal/build/motion-tests/NativeTelemetryTests.exe"
    if not executable.exists():
        pytest.skip("先运行 hal/tests/run_motion_tests.cmd 构建离线契约生成器")
    result = subprocess.run([str(executable), "--json"], capture_output=True,
                            text=True, encoding="utf-8", check=True, timeout=10)
    return json.loads(result.stdout)


def test_realtime_dds_uses_compact_history_not_full_diagnostics():
    source = (ROOT / "hal/src/HalDdsControlServer.cpp").read_text(encoding="utf-8")
    assert "publishJson(nativeTeleopWriter_, nativeTeleop_.telemetryJson());" in source
    assert "publishJson(nativeTeleopWriter_, nativeTeleop_.statusJson());" not in source
    dispatcher = (ROOT / "hal/src/HalCommandDispatcher.cpp").read_text(encoding="utf-8")
    assert "return nativeTeleop_.statusJson();" in dispatcher


def test_all_history_actions_and_measurement_timestamps_are_preserved(payloads):
    full, compact = payloads["full"], payloads["compact"]
    assert full["actionHistoryFormat"] == "diagnostic_v1"
    assert compact["actionHistoryFormat"] == "recording_v1"
    assert len(full["actionHistory"]) == len(compact["actionHistory"]) == 1000
    for original, live in zip(full["actionHistory"], compact["actionHistory"], strict=True):
        assert live == {key: original[key] for key in (
            "ts", "monotonicMs", "monotonic_s", "side", "sourceSide", "deltaVector")}
    for key in full.keys() - {"actionHistory", "actionHistoryFormat"}:
        assert compact[key] == full[key], key
    assert compact["grippers"]["left"]["positionSampleTs"] == 12345
    assert compact["grippers"]["left"]["positionSampleMonotonicMs"] == 23456
    assert compact["grippers"]["right"]["positionOk"] is False
    assert "requestedDeltaPulse" in compact["lastAction"]


@pytest.mark.parametrize("target", [99.9, 100.0, 100.017, 102.5, 104.5, 104.995, 105.9, 106.5])
def test_compact_history_produces_identical_recording_action_labels(payloads, target):
    def actions(payload):
        recorder = object.__new__(DatasetRecorderService)
        recorder._final_action_status = payload
        return recorder._latest_action_delta_vector(target)
    assert actions(payloads["full"]) == actions(payloads["compact"])


def test_live_payload_is_substantially_smaller_without_truncation(payloads):
    assert len(json.dumps(payloads["compact"])) < .4 * len(json.dumps(payloads["full"]))
