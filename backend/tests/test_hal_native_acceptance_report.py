from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify-hal-native-teleop-report.ps1"
ACCEPT_SCRIPT = REPO_ROOT / "scripts" / "accept-hal-native-teleop.ps1"
DIRECTION_POLICY = "current-hal-native-cross-map-v1"


def _base_report() -> dict:
    axes = [
        f"{side}:{axis}"
        for side in ("left", "right")
        for axis in ("X", "Y", "Z", "Roll", "Pitch", "Yaw")
    ]
    expected_output_sign = {
        "X": -1,
        "Y": 1,
        "Z": 1,
        "Roll": 1,
        "Pitch": -1,
        "Yaw": -1,
    }
    return {
        "schema": "hal-native-teleop-acceptance-v1",
        "health": {"ltdmc_ok": True, "omega7_ok": True},
        "noMotionProbe": {"pass": True},
        "observation": {
            "sampleCount": 32,
            "maxActionHistoryCount": 24,
            "requireActions": True,
            "pass": True,
            "summary": {
                "directionPolicy": DIRECTION_POLICY,
                "observedSourceTargetPairs": ["left->right", "right->left"],
                "observedMovingSourceTargetPairs": ["left->right", "right->left"],
                "observedTargetAxes": axes,
                "maxAbsDelta": 12.0,
                "axisDiagnostics": {
                    axis: {
                        "sampleCount": 8,
                        "nonZeroSamples": 4,
                        "rawActiveSamples": 4,
                        "requestedPulseSamples": 4,
                        "emittedPulseSamples": 4,
                        "outputSamples": 4,
                        "outputDuty": 0.5,
                        "pulseEfficiency": 1.0,
                        "maxAbsRawDelta": 1.0,
                        "maxAbsFilteredDelta": 1.0,
                        "maxAbsRequestedPulse": 100.0,
                        "maxAbsEmittedPulse": 100.0,
                        "maxAbsOutputDelta": 1.0,
                        "expectedOutputSign": expected_output_sign[axis.split(":")[1]],
                        "directionMatchSamples": 4,
                        "directionMismatchSamples": 0,
                        "directionMatchRatio": 1.0,
                        "sourceSides": ["left" if axis.startswith("right:") else "right"],
                    }
                    for axis in axes
                },
                "gripperTargetRanges": {"leftMm": 3.0, "rightMm": 4.0},
            },
            "gates": {
                "requireLeftAction": True,
                "requireRightAction": True,
                "requireCrossMapping": True,
                "requireAllAxes": True,
                "requireGripperChange": True,
                "requireForceOutput": True,
                "requireGravityCompensation": True,
                "requireZeroStop": True,
                "zeroStopObserved": True,
                "forceOutputAllEnabled": True,
                "gravityCompensationAllEnabled": True,
                "logicalConnectedAllEnabled": True,
                "gripperCommandSpeed": 255,
                "gripperCommandSpeedOk": True,
                "pass": True,
                "gateFailures": [],
                "missingAxes": [],
            },
        },
    }


def _run_verifier(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(VERIFY_SCRIPT),
            "-ReportPath",
            str(path),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_hal_native_acceptance_report_verifier_accepts_full_pass(tmp_path: Path) -> None:
    report_path = tmp_path / "pass.json"
    report_path.write_text(json.dumps(_base_report()), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 0
    assert "acceptancePass" in result.stdout


def test_hal_native_acceptance_report_verifier_rejects_missing_axes(tmp_path: Path) -> None:
    report = _base_report()
    report["observation"]["gates"]["pass"] = False
    report["observation"]["gates"]["missingAxes"] = ["right:Yaw"]
    report["observation"]["gates"]["gateFailures"] = ["Not all semantic axes were captured: right:Yaw"]
    report_path = tmp_path / "fail.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 2
    assert "right:Yaw" in result.stderr


def test_hal_native_acceptance_report_verifier_explains_missing_observation(tmp_path: Path) -> None:
    report = _base_report()
    report["observation"]["sampleCount"] = 0
    report["observation"]["maxActionHistoryCount"] = 0
    report["observation"]["seconds"] = 0
    report_path = tmp_path / "no-observation.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 2
    assert "Observation mode was not run" in result.stderr
    assert "-Strict" in result.stderr
    assert "ObserveSeconds" in result.stderr


def test_hal_native_acceptance_report_verifier_rejects_missing_axis_diagnostics(tmp_path: Path) -> None:
    report = _base_report()
    del report["observation"]["summary"]["axisDiagnostics"]["right:Yaw"]
    report_path = tmp_path / "missing-axis-diagnostics.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 2
    assert "right:Yaw" in result.stderr
    assert "axis diagnostics" in result.stderr


def test_hal_native_acceptance_report_verifier_rejects_zero_output_axis_diagnostics(tmp_path: Path) -> None:
    report = _base_report()
    report["observation"]["summary"]["axisDiagnostics"]["left:Pitch"]["maxAbsOutputDelta"] = 0.0
    report["observation"]["summary"]["axisDiagnostics"]["left:Pitch"]["maxAbsEmittedPulse"] = 0.0
    report_path = tmp_path / "zero-axis-diagnostics.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 2
    assert "left:Pitch" in result.stderr
    assert "no non-zero output" in result.stderr


def test_hal_native_acceptance_report_verifier_classifies_no_raw_input(tmp_path: Path) -> None:
    report = _base_report()
    diag = report["observation"]["summary"]["axisDiagnostics"]["right:X"]
    diag["sampleCount"] = 8
    diag["nonZeroSamples"] = 0
    diag["maxAbsRawDelta"] = 0.0
    diag["maxAbsFilteredDelta"] = 0.0
    diag["maxAbsRequestedPulse"] = 0.0
    diag["maxAbsEmittedPulse"] = 0.0
    diag["maxAbsOutputDelta"] = 0.0
    report_path = tmp_path / "no-raw-input.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 2
    assert "right:X" in result.stderr
    assert "no raw input" in result.stderr
    assert "no raw input" in result.stdout
    assert "Axis diagnostics:" in result.stdout
    assert "right:X raw=0" in result.stdout
    assert "outputDuty=0" in result.stdout
    assert "pulseEfficiency=0" in result.stdout
    assert "cause=no raw input" in result.stdout


def test_hal_native_acceptance_report_verifier_classifies_filtered_out_axis(tmp_path: Path) -> None:
    report = _base_report()
    diag = report["observation"]["summary"]["axisDiagnostics"]["left:Yaw"]
    diag["maxAbsRawDelta"] = 0.2
    diag["maxAbsFilteredDelta"] = 0.0
    diag["maxAbsRequestedPulse"] = 0.0
    diag["maxAbsEmittedPulse"] = 0.0
    diag["maxAbsOutputDelta"] = 0.0
    report_path = tmp_path / "filtered-axis.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 2
    assert "left:Yaw" in result.stderr
    assert "filtered out" in result.stderr


def test_hal_native_acceptance_report_verifier_classifies_pulse_gated_axis(tmp_path: Path) -> None:
    report = _base_report()
    diag = report["observation"]["summary"]["axisDiagnostics"]["right:Pitch"]
    diag["maxAbsRawDelta"] = 0.2
    diag["maxAbsFilteredDelta"] = 0.2
    diag["maxAbsRequestedPulse"] = 1.5
    diag["maxAbsEmittedPulse"] = 0.0
    diag["maxAbsOutputDelta"] = 0.0
    report_path = tmp_path / "pulse-gated-axis.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 2
    assert "right:Pitch" in result.stderr
    assert "pulse gated" in result.stderr


def test_hal_native_acceptance_report_verifier_rejects_missing_zero_stop(tmp_path: Path) -> None:
    report = _base_report()
    report["observation"]["gates"]["pass"] = False
    report["observation"]["gates"]["zeroStopObserved"] = False
    report["observation"]["gates"]["gateFailures"] = ["No final zero-delta native teleop stop was observed"]
    report_path = tmp_path / "zero-stop-fail.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 2
    assert "zero-delta" in result.stderr


def test_hal_native_acceptance_report_verifier_rejects_disconnected_logical_hand(tmp_path: Path) -> None:
    report = _base_report()
    report["observation"]["gates"]["logicalConnectedAllEnabled"] = False
    report_path = tmp_path / "logical-hand-disconnected.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 2
    assert "logicalConnectedAllEnabled" in result.stderr


def test_hal_native_acceptance_report_verifier_rejects_low_gripper_speed(tmp_path: Path) -> None:
    report = _base_report()
    report["observation"]["gates"]["gripperCommandSpeed"] = 128
    report["observation"]["gates"]["gripperCommandSpeedOk"] = False
    report_path = tmp_path / "low-gripper-speed.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 2
    assert "gripperCommandSpeed" in result.stderr
    assert "128" in result.stderr


def test_hal_native_acceptance_report_verifier_rejects_axis_direction_mismatch(tmp_path: Path) -> None:
    report = _base_report()
    diag = report["observation"]["summary"]["axisDiagnostics"]["right:X"]
    diag["expectedOutputSign"] = -1
    diag["directionMatchSamples"] = 0
    diag["directionMismatchSamples"] = 4
    diag["directionMatchRatio"] = 0.0
    report_path = tmp_path / "axis-direction-mismatch.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 2
    assert "right:X" in result.stderr
    assert "direction" in result.stderr


def test_hal_native_acceptance_report_verifier_rejects_axis_without_direction_samples(tmp_path: Path) -> None:
    report = _base_report()
    diag = report["observation"]["summary"]["axisDiagnostics"]["left:Y"]
    diag["expectedOutputSign"] = 1
    diag["directionMatchSamples"] = 0
    diag["directionMismatchSamples"] = 0
    diag["directionMatchRatio"] = 0.0
    diag["outputSamples"] = 4
    diag["maxAbsOutputDelta"] = 1.0
    report_path = tmp_path / "axis-direction-unproven.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 2
    assert "left:Y" in result.stderr
    assert "direction" in result.stderr


def test_hal_native_acceptance_report_verifier_rejects_missing_direction_policy(tmp_path: Path) -> None:
    report = _base_report()
    del report["observation"]["summary"]["directionPolicy"]
    report_path = tmp_path / "missing-direction-policy.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 2
    assert "directionPolicy" in result.stderr


def test_hal_native_acceptance_report_verifier_rejects_zero_only_cross_mapping(tmp_path: Path) -> None:
    report = _base_report()
    report["observation"]["summary"]["observedMovingSourceTargetPairs"] = []
    report["observation"]["summary"]["maxAbsDelta"] = 0.0
    report_path = tmp_path / "zero-only-cross-map-fail.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = _run_verifier(report_path)

    assert result.returncode == 2
    assert "non-zero left->right" in result.stderr


def test_acceptance_report_axis_diagnostics_use_native_inputs_not_action_vector() -> None:
    source = ACCEPT_SCRIPT.read_text(encoding="utf-8")

    assert "status.inputs" in source
    assert "-OutputDelta $delta" not in source


def test_acceptance_report_axis_diagnostics_include_duty_metrics() -> None:
    source = ACCEPT_SCRIPT.read_text(encoding="utf-8")

    assert "rawActiveSamples" in source
    assert "outputDuty" in source
    assert "pulseEfficiency" in source
    assert "directionMismatchSamples" in source
    assert "expectedOutputSign" in source
    assert DIRECTION_POLICY in source


def test_acceptance_report_observation_mode_auto_runs_verifier_before_gate_exit() -> None:
    source = ACCEPT_SCRIPT.read_text(encoding="utf-8")

    assert "$autoVerifyReport" in source
    assert "$ObserveSeconds -gt 0" in source
    assert "if ($autoVerifyReport)" in source
    assert source.index("if ($VerifyReport)") < source.index("if (!$gatesPass)")


def test_acceptance_report_no_motion_probe_accepts_auto_idle_controller() -> None:
    source = ACCEPT_SCRIPT.read_text(encoding="utf-8")

    no_motion_block = source.split("$noMotionPass = (", 1)[1].split("$noMotion = [pscustomobject]", 1)[0]
    assert "[bool]$start.ok" in no_motion_block
    assert "!( [bool]$after.running )" in no_motion_block
    assert "[bool]$during.running" not in no_motion_block
