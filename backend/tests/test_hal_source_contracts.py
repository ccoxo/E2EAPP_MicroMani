from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_hal_home_all_requires_work_origin_payload() -> None:
    source = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    assert "jsonWorkOriginPulse(requestBody(request))" in normalized
    assert "home_all requires leftPulse[6] work origin payload" in source
    assert "home_all requires rightPulse[6] work origin payload" in source


def test_hal_teleop_target_update_uses_absolute_target_mode() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    normalized = " ".join(source.split())
    teleop_body = source.split("void LTDMCDriver::updateTeleopTargetUi(", 1)[1].split(
        "void LTDMCDriver::stopTeleopSide", 1
    )[0]

    assert "const auto updateTargetPulse = static_cast<long>(std::llround(targetPulse));" in normalized
    assert "updateTeleopTargetBestEffort(card, axisNo, updateTargetPulse)" in normalized
    assert "void updateTeleopTargetBestEffort" in source
    assert "const auto retUpdate = dmcUpdateTargetPosition(card, axisNo, targetPulse, 1);" in normalized
    assert "(void)retUpdate;" in normalized
    assert 'dmcFailureMessage("dmc_update_target_position"' not in source
    assert "dmcPMove" not in teleop_body
    assert "dmc_pmove" not in teleop_body
    assert "syncZeroDeltaTarget" in source
    assert "teleopTargetPulse_[index] = pulse_[index];" in normalized
    assert "axis busy before teleop target" not in source


def test_hal_teleop_soft_limit_allows_recovery_toward_valid_range() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    assert "bool teleopTargetAllowedByLimit(double baseUi, double targetUi, const AxisLimit& limit)" in normalized
    assert "return targetUi > baseUi && targetUi < limit.min;" in normalized
    assert "return targetUi < baseUi && targetUi > limit.max;" in normalized
    assert "!teleopTargetAllowedByLimit(baseUi, targetUi, limit)" in normalized
    assert "teleopDefaultLimit" not in source
    assert "+/-7.5" not in source


def test_hal_teleop_limits_and_step_caps_come_from_payload() -> None:
    server_source = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    driver_source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    header_source = (REPO_ROOT / "hal" / "include" / "LTDMCDriver.h").read_text(encoding="utf-8")
    normalized_driver = " ".join(driver_source.split())
    normalized_header = " ".join(header_source.split())

    assert "jsonTeleopSoftLimits(bodyText)" in server_source
    assert 'jsonNumberValue(bodyText, "translationStepLimitPulse", 0.0)' in server_source
    assert 'jsonNumberValue(bodyText, "rotationStepLimitPulse", 0.0)' in server_source
    assert 'jsonBoolValue(bodyText, "syncZeroDeltaTarget", false)' in server_source
    assert "jsonTeleopEnabledAxes(bodyText)" in server_source
    assert "const std::array<AxisLimit, 6>& limits" in normalized_header
    assert "const auto stepLimitPulse = rotation ? rotationStepPulse : translationStepPulse;" in normalized_driver
    assert "clampPulseStep(requestedDeltaPulse, stepLimitPulse)" in normalized_driver
    assert "const auto limit = limits[axisIndex];" in normalized_driver


def test_hal_omega7_assignment_supports_icf_swap_hands() -> None:
    header_source = (REPO_ROOT / "hal" / "include" / "Omega7Driver.h").read_text(encoding="utf-8")
    driver_source = (REPO_ROOT / "hal" / "src" / "Omega7Driver.cpp").read_text(encoding="utf-8")
    server_source = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    start_hal = (REPO_ROOT / "scripts" / "start-hal.ps1").read_text(encoding="utf-8")

    assert "bool initialize(int leftOpenId, int rightOpenId, bool swapHands)" in header_source
    assert "takeDeviceByHandedness(true)" in driver_source
    assert "takeDeviceByOpenId(leftOpenId)" in driver_source
    assert "std::swap(state_[0], state_[1])" in driver_source
    assert 'envBoolValue("APPSTATION_OMEGA7_SWAP_HANDS", true)' in server_source
    assert "APPSTATION_OMEGA7_SWAP_HANDS" in start_hal


def test_hal_launch_promotes_latest_built_binary() -> None:
    start_hal = (REPO_ROOT / "scripts" / "start-hal.ps1").read_text(encoding="utf-8")
    build_hal = (REPO_ROOT / "hal" / "build_hal.cmd").read_text(encoding="utf-8")

    assert "$halNextExe = Join-Path $repo \"hal\\build\\HalServer.next.exe\"" in start_hal
    assert "function Promote-HalCandidate" in start_hal
    assert "LastWriteTimeUtc" in start_hal
    assert "Copy-Item -LiteralPath $halNextExe -Destination $halExe -Force" in start_hal
    assert 'copy /Y "HalServer.next.exe" "HalServer.exe"' in build_hal
