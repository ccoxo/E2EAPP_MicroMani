from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_hal_motion_state_json_exposes_axis_moving_flags() -> None:
    source = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    body = source.split("std::string jsonMotionState", 1)[1].split(
        "void appendDoubleArray",
        1,
    )[0]

    assert 'out << "],\\"moving\\":[";' in body
    assert 'state.axes[i].moving ? "true" : "false"' in body


def test_hal_axis_diagnostics_exposes_read_only_ltdmc_io() -> None:
    driver_source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    header_source = (REPO_ROOT / "hal" / "include" / "LTDMCDriver.h").read_text(encoding="utf-8")
    server_source = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")

    assert "std::string axisDiagnosticsJson();" in header_source
    assert "std::string LTDMCDriver::axisDiagnosticsJson()" in driver_source
    body = driver_source.split("std::string LTDMCDriver::axisDiagnosticsJson()", 1)[1].split(
        "void LTDMCDriver::emergencyStop",
        1,
    )[0]

    assert '"GET /motion/axis_diagnostics "' in server_source
    assert "dmc_axis_io_status" in driver_source
    assert "dmc_read_rdy_pin" in driver_source
    assert "dmc_read_erc_pin" in driver_source
    assert "dmc_read_sevrst_pin" in driver_source
    assert "dmc_get_stop_reason" in driver_source
    assert "dmc_get_el_mode" in driver_source
    assert "dmcPMove" not in body
    assert "dmcHomeMove" not in body
    assert "dmcStop" not in body


def test_ltdmc_manual_jog_keeps_single_step_hard_limits() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    body = source.split("void LTDMCDriver::moveRelativeUi(", 1)[1].split(
        "void LTDMCDriver::homeOriginSide",
        1,
    )[0]

    assert "const auto maxStep = rotation ? 2.0 : 5000.0;" in body
    assert (
        'throw std::runtime_error(rotation ? "rotation jog exceeds 2 degree" : "translation jog exceeds 5000 um");'
        in body
    )


def test_hal_home_all_requires_work_origin_payload() -> None:
    source = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    assert "const auto enabledAxes = jsonHomeAllEnabledAxes(bodyText);" in normalized
    assert "motion.homeAll(jsonWorkOriginPulse(bodyText), enabledAxes)" in normalized
    assert "jsonBoolArray6(bodyText, \"enabledAxes\", kAllAxesEnabled)" in normalized
    assert "motion.enableSide(side, true, jsonBoolArray6(bodyText, \"enabledAxes\", kAllAxesEnabled))" in normalized
    assert "motion.homeSide(side, jsonBoolArray6(bodyText, \"enabledAxes\", kAllAxesEnabled))" in normalized
    assert "home_all requires leftPulse[6] work origin payload" in source
    assert "home_all requires rightPulse[6] work origin payload" in source
    assert "home_origin_side requires pulse[6] work origin payload" in source


def test_hal_teleop_target_update_uses_absolute_target_mode() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    normalized = " ".join(source.split())
    teleop_body = source.split("TeleopTargetUpdateResult LTDMCDriver::updateTeleopTargetUi(", 1)[1].split(
        "void LTDMCDriver::stopTeleopSide", 1
    )[0]

    assert "const auto updateTargetPulse = static_cast<long>(std::llround(targetPulse));" in normalized
    assert "updateTeleopTargetBestEffort(card, axisNo, updateTargetPulse)" in normalized
    assert "TeleopTargetUpdateResult LTDMCDriver::updateTeleopTargetUi(" in source
    assert "result.appliedDeltaUi[axisIndex]" in source
    assert "result.targetPulse[axisIndex]" in source
    assert "int updateTeleopTargetBestEffort" in source
    assert "const auto retUpdate = dmcUpdateTargetPosition(card, axisNo, targetPulse, 1);" in normalized
    assert "return retUpdate;" in normalized
    assert "result.updateReturn[axisIndex]" in source
    server_source = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    assert '\\"updateReturn\\"' in server_source
    assert '\\"currentPulse\\"' in server_source
    assert '\\"launchDeltaPulse\\"' in server_source
    assert '\\"stopReason\\"' in server_source
    assert '\\"axisIoStatus\\"' in server_source
    assert '\\"movingBefore\\"' in server_source
    assert '\\"moveStarted\\"' in server_source
    assert 'dmcFailureMessage("dmc_update_target_position"' not in source
    assert "dmcPMove(card, axisNo, updateTargetPulse, 1)" not in teleop_body
    assert "dmcTeleopFailureMessage(" not in teleop_body
    assert "updateTeleopTargetBestEffort(card, axisNo, updateTargetPulse)" in teleop_body
    assert "<< \" targetPulse=\" << targetPulse" in source
    assert "<< \" limit=[\" << limit.min << \",\" << limit.max << \"]\"" in source
    assert "syncZeroDeltaTarget" in source
    assert "teleopTargetPulse_[index] = pulse_[index];" in normalized
    assert "axis busy before teleop target" not in source


def test_hal_teleop_zero_delta_sync_only_refreshes_active_axes() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    normalized = " ".join(source.split())
    teleop_body = source.split("TeleopTargetUpdateResult LTDMCDriver::updateTeleopTargetUi(", 1)[1].split(
        "void LTDMCDriver::stopTeleopSide", 1
    )[0]
    zero_delta_branch = teleop_body.split("if (deltaPulse == 0) {", 1)[1].split(
        "if (!axisMotionEnabled(side, axis))",
        1,
    )[0]

    assert "const bool zeroDeltaWasActive = teleopTargetActive_[index];" in normalized
    assert "if (syncZeroDeltaTarget && zeroDeltaWasActive) {" in zero_delta_branch
    assert zero_delta_branch.index("zeroDeltaWasActive") < zero_delta_branch.index("updateTeleopTargetBestEffort")


def test_hal_teleop_stop_side_hard_stops_and_retargets_to_current_position() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    body = source.split("void LTDMCDriver::stopTeleopSide(Side side)", 1)[1].split(
        "void LTDMCDriver::configureStageAxes",
        1,
    )[0]

    assert "dmcStop(card, axisNo, 0)" in body
    assert "pulse_[index] = static_cast<double>(dmcGetPosition(card, axisNo));" in body
    assert "updateTeleopTargetBestEffort(card, axisNo, currentPulse);" in body
    assert "teleopTargetActive_[index] = false;" in body
    assert "teleopTargetPulse_[index] = pulse_[index];" in body


def test_hal_teleop_soft_limit_clips_to_payload_range() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    normalized = " ".join(source.split())
    teleop_body = source.split("TeleopTargetUpdateResult LTDMCDriver::updateTeleopTargetUi(", 1)[1].split(
        "void LTDMCDriver::stopTeleopSide", 1
    )[0]

    assert "double clipTeleopTargetToLimit(double baseUi, double targetUi, const AxisLimit& limit)" in normalized
    assert "if (baseUi < limit.min)" in normalized
    assert "if (baseUi > limit.max)" in normalized
    assert "return targetUi > baseUi ? (std::min)(targetUi, limit.max) : baseUi;" in normalized
    assert "return targetUi < baseUi ? (std::max)(targetUi, limit.min) : baseUi;" in normalized
    assert "return std::clamp(targetUi, limit.min, limit.max);" in normalized
    assert "clipTeleopTargetToLimit(baseUi, unclippedTargetUi, limit)" in normalized
    assert "teleop target exceeds soft limit" not in teleop_body
    assert "teleopDefaultLimit" not in source
    assert "+/-7.5" not in source


def test_hal_teleop_soft_limit_uses_actual_position_even_when_target_is_active() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    body = source.split("TeleopTargetUpdateResult LTDMCDriver::updateTeleopTargetUi(", 1)[1].split(
        "void LTDMCDriver::stopTeleopSide",
        1,
    )[0]

    assert "double actualPulse = teleopTargetActive_[index] ? teleopTargetPulse_[index] : pulse_[index];" in body
    assert "actualPulse = static_cast<double>(dmcGetPosition(card, axisNo));" in body
    assert "pulse_[index] = actualPulse;" in body
    assert "result.currentPulse[axisIndex] = actualPulse;" in body
    assert "const auto basePulse = actualPulse;" in body
    assert "const auto targetUi = clipTeleopTargetToLimit(baseUi, unclippedTargetUi, limit);" in body


def test_hal_teleop_stops_moving_axis_when_limit_holds_target_at_current_pulse() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    body = source.split("TeleopTargetUpdateResult LTDMCDriver::updateTeleopTargetUi(", 1)[1].split(
        "void LTDMCDriver::stopTeleopSide",
        1,
    )[0]
    zero_delta_branch = body.split("if (deltaPulse == 0) {", 1)[1].split(
        "if (!axisMotionEnabled(side, axis))",
        1,
    )[0]
    assert "if (targetHeldAtBase && moving) {" in body
    held_target_branch = body.split("if (targetHeldAtBase && moving) {", 1)[1].split(
        "const bool shouldLaunchMove = !moving;",
        1,
    )[0]

    assert "struct AxisHoldResult" in source
    assert "stopTeleopAxisAtCurrentBestEffort(card, axisNo)" in zero_delta_branch
    assert "const bool targetHeldAtBase = std::abs(appliedTargetPulse - basePulse) <= 0.5;" in body
    assert "stopTeleopAxisAtCurrentBestEffort(card, axisNo)" in held_target_branch
    assert body.index("if (targetHeldAtBase && moving) {") < body.index("const bool shouldLaunchMove = !moving;")


def test_hal_teleop_limits_and_step_caps_come_from_payload() -> None:
    server_source = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    driver_source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    header_source = (REPO_ROOT / "hal" / "include" / "LTDMCDriver.h").read_text(encoding="utf-8")
    normalized_driver = " ".join(driver_source.split())
    normalized_header = " ".join(header_source.split())

    assert "jsonTeleopSoftLimits(bodyText)" in server_source
    assert 'jsonNumberValue(bodyText, "translationStepLimitPulse", 0.0)' in server_source
    assert 'jsonNumberValue(bodyText, "rotationStepLimitPulse", 0.0)' in server_source
    assert 'jsonNumberValue(bodyText, "translationPulseDeadband", 0.0)' in server_source
    assert 'jsonNumberValue(bodyText, "rotationPulseDeadband", 0.0)' in server_source
    assert 'jsonBoolValue(bodyText, "syncZeroDeltaTarget", false)' in server_source
    assert "jsonTeleopEnabledAxes(bodyText)" in server_source
    assert "jsonTeleopTargetUpdateResult(side, result)" in server_source
    assert '\\"appliedDeltas\\"' in server_source
    assert "const std::array<AxisLimit, 6>& limits" in normalized_header
    assert "TeleopTargetUpdateResult updateTeleopTargetUi" in normalized_header
    assert "const auto stepLimitPulse = rotation ? rotationStepPulse : translationStepPulse;" in normalized_driver
    assert (
        "const auto pulseDeadband = rotation ? rotationPulseDeadband : translationPulseDeadband;"
        in normalized_driver
    )
    assert "clampPulseStep(deadbandedDeltaPulse, stepLimitPulse)" in normalized_driver
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
    assert 'envBoolValue("APPSTATION_OMEGA7_SWAP_HANDS", false)' in server_source
    assert "APPSTATION_OMEGA7_SWAP_HANDS" in start_hal


def test_hal_native_start_retries_omega_open_after_startup_occupancy() -> None:
    header_source = (REPO_ROOT / "hal" / "include" / "Omega7Driver.h").read_text(encoding="utf-8")
    driver_source = (REPO_ROOT / "hal" / "src" / "Omega7Driver.cpp").read_text(encoding="utf-8")
    server_source = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    health_branch = server_source.split('GET /health ', 1)[1].split('GET /motion/state ', 1)[0]
    native_start = server_source.split('POST /teleop/native/start ', 1)[1].split(
        'POST /teleop/native/stop ',
        1,
    )[0]

    assert "bool ensureReady()" in header_source
    assert "int leftOpenId_{0};" in header_source
    assert "int rightOpenId_{1};" in header_source
    assert "bool swapHands_{false};" in header_source
    assert "bool Omega7Driver::ensureReady()" in driver_source
    assert "leftOpenId_ = leftOpenId;" in driver_source
    assert "rightOpenId_ = rightOpenId;" in driver_source
    assert "swapHands_ = swapHands;" in driver_source
    assert "if (!dhdModule)" in driver_source
    assert "dhdModule = LoadLibraryA(\"dhd64.dll\")" in driver_source
    assert "omega.ensureReady();" in health_branch
    assert health_branch.find("omega.ensureReady();") < health_branch.find("jsonHealth(")
    assert "omega.ensureReady();" in native_start
    assert native_start.find("omega.ensureReady();") < native_start.find("nativeTeleop.start(")


def test_hal_server_binds_to_configured_port_from_environment() -> None:
    server_source = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")

    assert 'envIntValue("APPSTATION_HAL_PORT", 8091)' in server_source
    assert "address.sin_port = htons(static_cast<u_short>(halPort));" in server_source
    assert 'std::cerr << "Failed to bind HalServer on 127.0.0.1:" << halPort' in server_source
    assert 'std::cout << "HalServer listening on http://127.0.0.1:" << halPort' in server_source
    assert "htons(8091)" not in server_source


def test_hal_omega7_force_output_is_wired_to_sdk() -> None:
    header_source = (REPO_ROOT / "hal" / "include" / "Omega7Driver.h").read_text(encoding="utf-8")
    driver_source = (REPO_ROOT / "hal" / "src" / "Omega7Driver.cpp").read_text(encoding="utf-8")
    server_source = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    client_source = (REPO_ROOT / "backend" / "hal_client" / "client.py").read_text(encoding="utf-8")

    assert "void setGravityCompensation(bool leftEnabled, bool rightEnabled)" in header_source
    assert "DhdEnableForce" in driver_source
    assert "dhdEnableForce(enabled ? 1 : 0, deviceId)" in driver_source
    assert "dhdSetGravityCompensation(enabled ? 1 : 0, deviceId)" in driver_source
    assert "dhdSetForceAndTorqueAndGripperForce(" in driver_source
    assert "writeZeroForceUnlocked(item)" in driver_source
    assert 'POST /omega7/gravity_compensation ' in server_source
    assert 'POST /omega7/zero_force_feedback ' in server_source
    assert '"omega7.gravity_compensation": "/omega7/gravity_compensation"' in client_source
    assert '"omega7.zero_force_feedback": "/omega7/zero_force_feedback"' in client_source


def test_hal_launch_promotes_latest_built_binary() -> None:
    start_hal = (REPO_ROOT / "scripts" / "start-hal.ps1").read_text(encoding="utf-8")
    build_hal = (REPO_ROOT / "hal" / "build_hal.cmd").read_text(encoding="utf-8")

    assert "$halNextExe = Join-Path $repo \"hal\\build\\HalServer.next.exe\"" in start_hal
    assert "$workerExe = Join-Path $repo \"hal\\build\\JodellGripperWorker.exe\"" in start_hal
    assert "$workerNextExe = Join-Path $repo \"hal\\build\\JodellGripperWorker.next.exe\"" in start_hal
    assert "function Promote-HalCandidate" in start_hal
    assert "LastWriteTimeUtc" in start_hal
    assert "Promote-HalCandidate -CandidateExe $halNextExe -TargetExe $halExe" in start_hal
    assert "Promote-HalCandidate -CandidateExe $workerNextExe -TargetExe $workerExe" in start_hal
    assert "Copy-Item -LiteralPath $CandidateExe -Destination $TargetExe -Force" in start_hal
    assert 'Join-Path $halBuild "JodellGripperWorker.exe"' in start_hal
    assert 'copy /Y "HalServer.next.exe" "HalServer.exe"' in build_hal
    assert 'copy /Y "JodellGripperWorker.next.exe" "JodellGripperWorker.exe"' in build_hal


def test_hal_stage_axis_configuration_matches_icf_card_counts() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    assert "int stageAxisCount(appstation::hal::Side side)" in normalized
    assert "return side == appstation::hal::Side::Left ? 6 : 9;" in normalized
    assert "axisNoInt < stageAxisCount(side)" in normalized


def test_hal_enable_side_rejects_partial_servo_failures() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    assert "if (failed > 0) { throw std::runtime_error(failures.str()); }" in normalized
    assert "succeeded == 0 && failed > 0" not in normalized


def test_hal_home_origin_does_not_auto_enable_participating_axes() -> None:
    header = (REPO_ROOT / "hal" / "include" / "LTDMCDriver.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    server = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    home_all_branch = server.split('POST /motion/home_all ', 1)[1].split(
        'POST /motion/home_origin_side ',
        1,
    )[0]
    home_side_branch = server.split('POST /motion/home_origin_side ', 1)[1].split(
        'POST /motion/enable_side ',
        1,
    )[0]

    home_all_body = source.split("void LTDMCDriver::homeAll(", 1)[1].split(
        "void LTDMCDriver::homeOriginSide",
        1,
    )[0]
    home_side_body = source.split("void LTDMCDriver::homeOriginSide(", 1)[1].split(
        "void LTDMCDriver::moveAllUi",
        1,
    )[0]

    assert "enableHomeAxes" not in header
    assert "std::string LTDMCDriver::enableHomeAxes" not in source
    assert "motion.enableHomeAxes" not in home_all_branch
    assert "motion.enableHomeAxes" not in home_side_branch
    for body in (home_all_body, home_side_body):
        assert "axisMotionEnabled(side, axis)" in body
        assert "enable required axes before returning to work origin" in body


def test_hal_treats_card0_dmc5c10_sevon_feedback_as_unreadable() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    header = (REPO_ROOT / "hal" / "include" / "LTDMCDriver.h").read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    assert "bool usesSevonPin(appstation::hal::Side side, appstation::hal::SemanticAxis axis)" in normalized
    assert "return physicalAxis(side, axis) < stageAxisCount(side);" in normalized
    assert "bool hasReadableSevonFeedback(appstation::hal::Side side, appstation::hal::SemanticAxis axis)" in normalized
    assert "if (side == appstation::hal::Side::Right) { return false; }" in normalized
    assert "std::array<bool, 12> commandedEnabled_{};" in header
    assert "if (!hasReadableSevonFeedback(side, axis)) { enabled_[index] = commandedEnabled_[index]; }" in normalized
    assert (
        "else if (dmcReadSevonPin) { enabled_[index] = dmcReadSevonPin(card, axisNo) > 0; "
        "commandedEnabled_[index] = enabled_[index]; }"
    ) in normalized
    assert (
        "if (!usesSevonPin(side, axis)) { const auto index = stateIndex(side, axis); "
        "enabled_[index] = axisEnabled; commandedEnabled_[index] = axisEnabled; "
        "++succeeded; continue; }"
    ) in normalized


def test_hal_ignores_card0_unsupported_sevon_write_without_masking_other_failures() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    assert "bool ignoreUnsupportedSevonWriteFailure(" in normalized
    assert "side == appstation::hal::Side::Right" in normalized
    assert "ret == 2" in normalized
    assert "if (ignoreUnsupportedSevonWriteFailure(side, axis, ret))" in normalized
    assert "enabled_[index] = axisEnabled;" in normalized
    assert "commandedEnabled_[index] = axisEnabled;" in normalized
    assert "++succeeded;" in normalized
    assert "continue;" in normalized
    assert "failures << dmcAxisFailureMessage(\"dmc_write_sevon_pin\", ret, card, axisNo);" in normalized


def test_hal_stage_axis_and_direction_signs_match_icf_mapping() -> None:
    source = (REPO_ROOT / "hal" / "include" / "HalTypes.h").read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    assert "kLeftPhysicalAxis{0, 1, 3, 5, 4, 2}" in normalized
    assert "kRightPhysicalAxis{2, 0, 5, 8, 1, 7}" in normalized
    assert "-5000.0, 5000.0, -10000.0, 1666.666667, -2500.0, -3333.333" in normalized
    assert "-5000.0, -10000.0, -5000.0, 1666.666667, 2500.0, 333.3333" in normalized


def test_runtime_launch_disables_pagehide_auto_shutdown_and_stop_stack_kills_all_listener_trees() -> None:
    start_stack = (REPO_ROOT / "scripts" / "start-stack.ps1").read_text(encoding="utf-8")
    stop_stack = (REPO_ROOT / "scripts" / "stop-stack.ps1").read_text(encoding="utf-8")

    assert "VITE_AUTO_SHUTDOWN_ON_CLOSE" not in start_stack
    assert "[switch]$SkipStartupHome" in start_stack
    assert "APPSTATION_SKIP_STARTUP_HOME" in start_stack
    assert "Sort-Object -Unique" in stop_stack
    assert "Select-Object -ExpandProperty OwningProcess -First 1" not in stop_stack
    assert "$currentPid = $PID" in stop_stack
    assert "$RootPid -eq $currentPid" in stop_stack
    assert "Stop-ProcessTree -RootPid $rootPid" in stop_stack
    assert "$repo = (Resolve-Path (Join-Path $PSScriptRoot \"..\"))" in stop_stack
    assert "backend\\.app:create_app" in stop_stack
    assert "18080|18082" in stop_stack
    assert "Stop-BackendProcessTrees" in stop_stack


def test_hal_native_translation_soft_limits_are_relative_until_hal_reanchors_them() -> None:
    backend_source = (REPO_ROOT / "backend" / "services" / "teleop_mapping.py").read_text(encoding="utf-8")
    hal_source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    soft_limit_body = backend_source.split("def _soft_limit_arrays(", 1)[1].split(
        "def _effective_limit_arrays",
        1,
    )[0]
    effective_body = hal_source.split(
        "std::array<AxisLimit, 6> NativeTeleopController::effectiveSoftLimits",
        1,
    )[1].split(
        "std::array<double, 6> NativeTeleopController::velocityDeltasUi",
        1,
    )[0]

    assert "native_teleop_limit_arrays(config, side)" in soft_limit_body
    assert "pulseToUi(config_.homeReferencePulse[targetIndex][axisIndex], targetSide, axis)" in effective_body
    assert "limits[axisIndex].min += originUi" in effective_body
    assert "limits[axisIndex].max += originUi" in effective_body
    assert "std::max(limits[axisIndex].min, originUi + workLimit.min)" in effective_body
    assert "std::min(limits[axisIndex].max, originUi + workLimit.max)" in effective_body
    assert "axisIndex < 3" in effective_body


def test_hal_native_teleop_controller_is_wired_to_server_and_build() -> None:
    server = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    cmake = (REPO_ROOT / "hal" / "CMakeLists.txt").read_text(encoding="utf-8")
    build_cmd = (REPO_ROOT / "hal" / "build_hal.cmd").read_text(encoding="utf-8")

    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")

    assert 'POST /teleop/native/configure ' in server
    assert 'POST /teleop/native/start ' in server
    assert 'POST /teleop/native/stop ' in server
    assert 'GET /teleop/native/status ' in server
    assert "NativeTeleopController nativeTeleop" in server
    assert "nativeTeleop.stop()" in server

    assert "src/NativeTeleopController.cpp" in cmake
    assert "src/JodellGripperDriver.cpp" in cmake
    assert "NativeTeleopController.cpp" in build_cmd
    assert "JodellGripperDriver.cpp" in build_cmd

    assert "class NativeTeleopController" in header
    assert "velocity_admittance" in source
    assert "updateTeleopTargetUi" in source
    assert "stopTeleopSide" in source
    assert "setGravityCompensation" in source
    assert "forceOutputEnabled" in source


def test_hal_native_controller_defaults_match_site_corrected_output_and_xy_signs() -> None:
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    normalized = " ".join(header.split())

    assert "std::array<double, 2> translationScale{{1.25, 1.25}}" in normalized
    assert "std::array<double, 2> rotationScale{{1.20, 1.20}}" in normalized
    assert "{0.65, 0.45, 0.45, 0.60, 0.16, 0.20}" in normalized
    assert "{0.65, 0.45, 0.45, 0.55, 0.16, 0.25}" in normalized
    assert "double translationStartVelocityUmS{1500.0}" in normalized
    assert "double translationMaxVelocityUmS{20000.0}" in normalized
    assert "double rotationStartVelocityDegS{2.5}" in normalized
    assert "double rotationMaxVelocityDegS{30.0}" in normalized
    assert "double accTimeSec{0.03}" in normalized
    assert "double decTimeSec{0.03}" in normalized
    assert "{-5000000.0, -5000000.0, -10000000.0, 1667.0, 2500.0, -333.3333}" in normalized
    assert "{-5000000.0, 10000000.0, -5000000.0, 1667.0, -2500.0, 3333.333}" in normalized
    assert "bool gripperTeleopEnabled{false}" in normalized


def test_hal_native_home_stops_controller_and_waits_for_motion_done() -> None:
    server = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    motion = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    home_all_branch = server.split('POST /motion/home_all ', 1)[1].split(
        'POST /motion/home_origin_side ',
        1,
    )[0]
    home_side_branch = server.split('POST /motion/home_origin_side ', 1)[1].split(
        'POST /motion/enable_side ',
        1,
    )[0]

    assert "nativeTeleop.stop();" in home_all_branch
    assert "nativeTeleop.stop();" in home_side_branch
    assert "const auto enabledAxes = jsonHomeAllEnabledAxes(bodyText);" in home_all_branch
    assert "motion.enableHomeAxes" not in home_all_branch
    assert "const auto enabledAxes = jsonBoolArray6(bodyText, \"enabledAxes\", kAllAxesEnabled);" in home_side_branch
    assert "motion.enableHomeAxes" not in home_side_branch
    assert "waitForAxesDone(homeAxes, homeAxisCount, \"home_all pre-move\", 3000)" in motion
    assert "waitForAxesDone(homeAxes, homeAxisCount, \"home_all\", 60000)" in motion
    assert "waitForAxesDone(homeAxes, homeAxisCount, \"home_origin_side\", 60000)" in motion
    assert "teleopTargetActive_[index] = false;" in motion


def test_hal_native_home_origin_moves_to_absolute_work_origin_like_icf() -> None:
    motion = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    home_all_body = motion.split("void LTDMCDriver::homeAll(", 1)[1].split(
        "void LTDMCDriver::homeOriginSide",
        1,
    )[0]
    home_side_body = motion.split("void LTDMCDriver::homeOriginSide(", 1)[1].split(
        "void LTDMCDriver::moveAllUi",
        1,
    )[0]

    for body in (home_all_body, home_side_body):
        assert "const auto currentPulse = dmcGetPosition(card, axisNo);" in body
        assert "const auto deltaPulse = targetPulse - currentPulse;" in body
        assert "startWorkOriginMoveOrThrow(card, axisNo, targetPulse, deltaPulse, currentPulse)" in body

    assert "constexpr long kWorkOriginSettledPulseTolerance = 100;" in motion
    assert "std::abs(deltaPulse) <= kWorkOriginSettledPulseTolerance" in motion
    assert "const auto absoluteRet = dmcPMove(card, axisNo, targetPulse, 1);" in motion
    assert "const auto relativeRet = dmcPMove(card, axisNo, deltaPulse, 0);" in motion
    assert (
        'dmcAbsoluteFailureMessage("dmc_pmove", absoluteRet, card, axisNo, deltaPulse, targetPulse, currentPulse)'
        in motion
    )
    assert "relative fallback ret=" in motion


def test_hal_emergency_stop_preempts_long_motion_waits() -> None:
    header = (REPO_ROOT / "hal" / "include" / "LTDMCDriver.h").read_text(encoding="utf-8")
    motion = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    server = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    emergency_body = motion.split("void LTDMCDriver::emergencyStop() {", 1)[1].split(
        "std::string LTDMCDriver::enableSide",
        1,
    )[0]
    emergency_branch = server.split('POST /motion/emergency_stop ', 1)[1].split(
        'POST /motion/home_all ',
        1,
    )[0]

    assert "nativeTeleop.stop();" in emergency_branch
    assert 'GetProcAddress(ltdmcModule, "dmc_emg_stop")' in motion
    assert "dmcEmgStop(card);" in emergency_body
    assert "std::scoped_lock lock(mutex_)" not in emergency_body
    assert "stopAllAxesBestEffort();" in emergency_body
    assert "disableAllAxesBestEffort();" in emergency_body
    assert "dmcStop(card, static_cast<unsigned short>(axisNo), 1);" in emergency_body
    assert "dmcWriteSevonPin(card, static_cast<unsigned short>(axisNo), 0);" in emergency_body
    assert "std::unique_lock<std::mutex> stateLock(mutex_, std::try_to_lock);" in emergency_body
    assert "std::atomic_bool estopActive_{false};" in header
    assert "std::atomic_uint64_t estopSequence_{0};" in header
    assert "void disableAllAxesBestEffort() noexcept;" in header
    assert "void clearEstopIfUnchanged(std::uint64_t sequenceAtStart);" in header


def test_hal_status_reads_fall_back_to_cached_snapshot_when_motion_lock_is_busy() -> None:
    header = (REPO_ROOT / "hal" / "include" / "LTDMCDriver.h").read_text(encoding="utf-8")
    motion = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    health_body = motion.split("HalHealth LTDMCDriver::health(double uptimeS) const {", 1)[1].split(
        "void LTDMCDriver::ensureMotionReturnAllowed",
        1,
    )[0]
    read_state_body = motion.split("MotionState LTDMCDriver::readState() {", 1)[1].split(
        "std::string LTDMCDriver::axisDiagnosticsJson",
        1,
    )[0]

    assert "mutable std::mutex snapshotMutex_;" in header
    assert "MotionState cachedState_{};" in header
    assert "MotionState cachedStateSnapshot() const;" in header
    assert "void publishStateSnapshotLocked();" in header
    assert "HalHealth cachedHealth(double uptimeS) const;" in header
    assert "std::unique_lock<std::mutex> lock(mutex_, std::try_to_lock);" in health_body
    assert "if (!lock.owns_lock())" in health_body
    assert "return cachedHealth(uptimeS);" in health_body
    assert "std::unique_lock<std::mutex> lock(mutex_, std::try_to_lock);" in read_state_body
    assert "if (!lock.owns_lock())" in read_state_body
    assert "return cachedStateSnapshot();" in read_state_body
    assert "publishStateSnapshotLocked(state);" in read_state_body
    assert motion.count("publishStateSnapshotLocked();") >= 4


def test_hal_direct_work_origin_home_rejects_estop_before_enable_or_motion() -> None:
    header = (REPO_ROOT / "hal" / "include" / "LTDMCDriver.h").read_text(encoding="utf-8")
    motion = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    server = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    home_all_branch = server.split('POST /motion/home_all ', 1)[1].split(
        'POST /motion/home_origin_side ',
        1,
    )[0]
    home_side_branch = server.split('POST /motion/home_origin_side ', 1)[1].split(
        'POST /motion/enable_side ',
        1,
    )[0]
    home_all_body = motion.split(
        "void LTDMCDriver::homeAll(\n"
        "    const std::array<double, 12>& workOriginPulse,\n"
        "    const std::array<std::array<bool, 6>, 2>& enabledAxes) {",
        1,
    )[1].split("void LTDMCDriver::homeOriginSide", 1)[0]
    home_side_body = motion.split(
        "void LTDMCDriver::homeOriginSide(\n"
        "    Side side,\n"
        "    const std::array<double, 6>& workOriginPulse,\n"
        "    const std::array<bool, 6>& enabledAxes) {",
        1,
    )[1].split("void LTDMCDriver::moveAllUi", 1)[0]

    assert "void ensureMotionReturnAllowed() const;" in header
    assert "void LTDMCDriver::ensureMotionReturnAllowed() const {" in motion
    assert (
        'throw std::runtime_error("emergency stop active; acknowledge safety before returning to work origin");'
        in motion
    )
    for branch in (home_all_branch, home_side_branch):
        assert "motion.ensureMotionReturnAllowed();" in branch
        assert branch.index("motion.ensureMotionReturnAllowed();") < branch.index("nativeTeleop.stop();")
        assert branch.index("motion.ensureMotionReturnAllowed();") < branch.index("motion.home")
    for body in (home_all_body, home_side_body):
        assert "ensureMotionReturnAllowed();" in body


def test_hal_native_gripper_manual_endpoint_uses_native_controller_queue() -> None:
    server = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    client = (REPO_ROOT / "backend" / "hal_client" / "client.py").read_text(encoding="utf-8")
    command_service = (REPO_ROOT / "backend" / "services" / "command_service.py").read_text(encoding="utf-8")
    controller_header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    branch = server.split('POST /gripper/command ', 1)[1].split(
        'POST /motion/emergency_stop ',
        1,
    )[0]

    assert '"teleop.native.gripper_command": "/teleop/native/gripper_command"' in client
    assert 'await self.hal.command("teleop.native.gripper_command", payload)' in command_service
    assert "void configureGripper(const JodellGripperConfig& config);" in controller_header
    assert (
        "bool commandGripperTarget(Side side, double targetMm, int speed, int torque, "
        "std::string* message = nullptr);"
    ) in controller_header
    assert "nativeTeleop.configureGripper(config.gripper)" in branch
    assert "nativeTeleop.configure(config)" not in branch
    assert "nativeTeleop.commandGripperTarget(side, targetMm, speed, torque, &message)" in branch
    assert "gripper.commandTarget(side, targetMm, speed, torque, &message)" not in branch
    assert "jsonNativeTeleopConfig(bodyText)" in branch


def test_frontend_hal_native_gripper_commands_are_not_blocked_by_cached_enabled_state() -> None:
    store = (REPO_ROOT / "frontend" / "src" / "stores" / "telemetry.ts").read_text(encoding="utf-8")
    settings = (REPO_ROOT / "frontend" / "src" / "views" / "SettingsView.tsx").read_text(encoding="utf-8")

    assert "config.teleop.engine !== 'hal_native'" in store
    assert "const canCommandGripper = gripperEnabled || config.teleop.engine === 'hal_native'" in settings
    assert "disabled={!canCommandGripper}" in settings


def test_manual_axis_direction_corrections_are_backend_authoritative() -> None:
    store = (REPO_ROOT / "frontend" / "src" / "stores" / "telemetry.ts").read_text(encoding="utf-8")
    command_service = (REPO_ROOT / "backend" / "services" / "command_service.py").read_text(encoding="utf-8")
    normalized = " ".join(store.split())
    real_mode_body = store.split("issueManualAxisMove: (side, axis, direction) => {", 1)[1].split(
        "issueManualGripperMove:",
        1,
    )[0]

    assert "left: [1, 1, 1, 1, 1, 1]" in normalized
    assert "right: [1, 1, 1, 1, 1, 1]" in normalized
    assert '"left": [1, -1, 1, 1, 1, 1]' in command_service
    assert '"right": [-1, -1, -1, 1, 1, 1]' in command_service
    assert "const effectiveDirection = manualAxisEffectiveDirection(side, axisIndex, direction)" in real_mode_body
    assert "manualAxisMoveApi(side, axis, effectiveDirection, step, speedMode)" in real_mode_body
    assert "manualAxisMoveApi(side, axis, direction, step, speedMode)" not in real_mode_body


def test_hal_native_controller_uses_icf_omega_semantic_pose_mapping() -> None:
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    server = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    assert "omegaPoseToSemantic" in source
    assert 'std::string mappingMode{"direct"}' in header
    assert 'config.mappingMode = jsonStringValueOr(body, "mappingMode", config.mappingMode);' in server
    assert 'normalized.mappingMode != "legacy"' in source
    assert "return {raw[1], raw[0], raw[2], raw[3], raw[5], raw[4]};" in normalized
    assert "return {raw[0], raw[1], raw[2], raw[3], raw[4], raw[5]};" in normalized
    assert "const auto rawSemanticPose = omegaPoseToSemantic(hand.pose, config_.mappingMode);" in source
    assert "const auto semanticPose = kalmanPoseForSide(sourceIndex, rawSemanticPose, dtSec);" in source
    assert "referencePose_[sourceIndex] = semanticPose;" in source
    assert "incrementalDeltasUi(sourceIndex, targetSide, semanticPose)" in source
    assert "velocityDeltasUi(sourceIndex, targetSide, semanticPose, dtSec)" in source
    assert "const std::array<double, 6>& pose" in header


def test_hal_native_default_cross_mapping_matches_stage_one_and_two_targets() -> None:
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    tick_body = source.split("void NativeTeleopController::tickSide(", 1)[1].split(
        "void NativeTeleopController::syncIncrementalZeroDeltaUnlocked",
        1,
    )[0]
    normalized = " ".join(tick_body.split())

    assert "bool swapTeleopChannels{true};" in header
    assert "const Side sourceSide = sideFromIndex(sourceIndex);" in tick_body
    assert (
        "const Side targetSide = config_.swapTeleopChannels ? sideFromIndex(1 - sourceIndex) : sourceSide;"
        in tick_body
    )
    assert "const int targetIndex = sideIndex(targetSide);" in tick_body
    assert "lastDiagnosticTargetSide_[sourceIndex] = targetSide;" in tick_body
    assert "sourceIndex) : sourceSide; const int targetIndex = sideIndex(targetSide);" in normalized


def test_hal_native_incremental_zero_delta_stops_active_target_like_icf_incremental_follow() -> None:
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    body = source.split("void NativeTeleopController::tickSide(", 1)[1].split(
        "std::array<double, 6> NativeTeleopController::velocityDeltasUi",
        1,
    )[0]
    zero_branch = body.split("if (!hasMotion(deltas)) {", 1)[1].split("\n  }\n\n", 1)[0]

    assert 'std::string controlMode{"incremental_position"}' in header
    assert "void syncIncrementalZeroDeltaUnlocked(" in header
    assert "config_.controlMode == kIncrementalPositionMode" in zero_branch
    assert (
        "syncIncrementalZeroDeltaUnlocked(sourceSide, targetSide, sourceIndex, targetIndex, semanticPose"
        in zero_branch
    )
    sync_body = source.split("void NativeTeleopController::syncIncrementalZeroDeltaUnlocked(", 1)[1].split(
        "std::array<double, 6> NativeTeleopController::velocityDeltasUi",
        1,
    )[0]
    assert "motion_.stopTeleopSide(targetSide);" in sync_body
    assert "targetActive_[targetIndex] = false;" in sync_body
    assert "recordZeroStopActionUnlocked(sourceSide, targetSide);" in sync_body
    assert "referencePose_[sourceIndex] = semanticPose" in sync_body
    assert "motion_.updateTeleopTargetUi(" not in sync_body


def test_hal_native_incremental_motion_updates_target_then_advances_reference() -> None:
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    body = source.split("void NativeTeleopController::tickSide(", 1)[1].split(
        "void NativeTeleopController::syncIncrementalZeroDeltaUnlocked(",
        1,
    )[0]
    active_branch = body.split("const auto result = motion_.updateTeleopTargetUi(", 1)[1].split(
        "}\n\nvoid NativeTeleopController::syncIncrementalZeroDeltaUnlocked(",
        1,
    )[0]
    normalized = " ".join(active_branch.split())

    assert "targetSide," in active_branch
    assert "deltas," in active_branch
    assert "config_.translationStepLimitPulse," in active_branch
    assert "config_.rotationStepLimitPulse," in active_branch
    assert "config_.translationPulseDeadband," in active_branch
    assert "config_.rotationPulseDeadband," in active_branch
    assert "config_.enabledAxes[targetIndex]," in active_branch
    assert "true," in active_branch
    assert "const auto limits = effectiveSoftLimits(targetSide, targetIndex);" in body
    assert "limits," in active_branch
    assert "targetActive_[targetIndex] = true;" in active_branch
    assert "recordActionUnlocked(sourceSide, targetSide, result);" in active_branch
    assert (
        "if (config_.controlMode == kIncrementalPositionMode) { referencePose_[sourceIndex] = semanticPose; }"
        in normalized
    )


def test_hal_native_teleop_soft_limits_are_relative_to_home_reference() -> None:
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    server = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    backend = (REPO_ROOT / "backend" / "services" / "teleop_mapping.py").read_text(encoding="utf-8")
    normalized_source = " ".join(source.split())

    assert "std::array<std::array<double, 6>, 2> workOriginPulse" in header
    assert "std::array<bool, 2> workOriginValid" in header
    assert "std::array<std::array<double, 6>, 2> homeReferencePulse" in header
    assert "std::array<bool, 2> homeReferenceValid" in header
    assert "bool rotationWorkLimitEnabled" in header
    assert "std::array<std::array<AxisLimit, 6>, 2> rotationWorkLimits" in header
    assert "std::array<AxisLimit, 6> effectiveSoftLimits(Side targetSide, int targetIndex) const" in header
    assert "pulseToUi(config_.homeReferencePulse[targetIndex][axisIndex], targetSide, axis)" in source
    assert "limits[axisIndex].min += originUi" in source
    assert "limits[axisIndex].max += originUi" in source
    assert "std::max(limits[axisIndex].min, originUi + workLimit.min)" in source
    assert "std::min(limits[axisIndex].max, originUi + workLimit.max)" in source
    assert "const auto limits = effectiveSoftLimits(targetSide, targetIndex);" in normalized_source
    assert 'jsonBoolValue(body, "leftWorkOriginValid", config.workOriginValid[0])' in server
    assert 'jsonNumberArray6(body, "leftWorkOriginPulse", config.workOriginPulse[0])' in server
    assert 'jsonBoolValue(body, "leftHomeReferenceValid", config.homeReferenceValid[0])' in server
    assert 'jsonNumberArray6(body, "leftHomeReferencePulse", config.homeReferencePulse[0])' in server
    assert 'jsonBoolValue(body, "rotationWorkLimitEnabled", config.rotationWorkLimitEnabled)' in server
    assert 'jsonAxisLimits(body, "leftRotationWorkLimitMin", "leftRotationWorkLimitMax"' in server
    assert '"leftWorkOriginPulse": self._work_origin_pulse("left", config)' in backend
    assert '"leftHomeReferencePulse": self._home_reference_pulse("left", config)' in backend
    assert '"rightWorkOriginValid": self._work_origin_valid("right", config)' in backend
    assert '"leftRotationWorkLimitMin": self._rotation_work_limit_arrays("left", config)[0]' in backend


def test_hal_native_incremental_continuous_mode_filters_small_rotation_noise() -> None:
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    incremental_body = source.split("std::array<double, 6> NativeTeleopController::incrementalDeltasUi(", 1)[1].split(
        "long NativeTeleopController::applyContinuousPulseGate",
        1,
    )[0]
    continuous_branch = incremental_body.split("if (config_.continuousIncrementMode) {", 1)[1].split(
        "} else if (rotation) {",
        1,
    )[0]
    normalized = " ".join(continuous_branch.split())

    assert "const bool aboveInputThreshold = std::abs(rawDelta) >= inputThreshold;" in incremental_body
    assert "filteredDelta = aboveInputThreshold ? rawDelta : 0.0;" in normalized
    assert "std::abs(rawDelta) > 1e-12 ? rawDelta : 0.0" not in continuous_branch


def test_hal_native_gripper_follow_uses_physical_hands_without_arm_logical_gate() -> None:
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    body = source.split("void NativeTeleopController::tickGrippers(", 1)[1].split(
        "void NativeTeleopController::enqueueGripperCommand",
        1,
    )[0]

    assert "const int sourceIndex = gripperSourceIndex(targetIndex);" in body
    assert "if (!logicalConnected_[sourceIndex])" not in body


def test_hal_native_incremental_below_threshold_input_stops_active_target_like_icf() -> None:
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    body = source.split("void NativeTeleopController::tickSide(", 1)[1].split(
        "void NativeTeleopController::syncIncrementalZeroDeltaUnlocked(",
        1,
    )[0]
    no_motion_branch = body.split("if (!hasMotion(deltas)) {", 1)[1].split(
        "\n  }\n\n  const auto result = motion_.updateTeleopTargetUi(",
        1,
    )[0]

    assert '"incremental input below output threshold stopped"' in no_motion_branch
    assert 'const auto stopMessage = incrementalInputActive_[sourceIndex]' in no_motion_branch
    assert (
        'syncIncrementalZeroDeltaUnlocked(sourceSide, targetSide, sourceIndex, targetIndex, semanticPose, stopMessage)'
        in no_motion_branch
    )
    assert "return;" in no_motion_branch


def test_hal_native_incremental_gated_input_advances_reference_and_syncs_target() -> None:
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    tick_body = source.split("void NativeTeleopController::tickSide(", 1)[1].split(
        "std::array<double, 6> NativeTeleopController::velocityDeltasUi",
        1,
    )[0]
    incremental_body = source.split("std::array<double, 6> NativeTeleopController::incrementalDeltasUi(", 1)[1].split(
        "long NativeTeleopController::applyContinuousPulseGate",
        1,
    )[0]
    normalized_tick = " ".join(tick_body.split())
    normalized_incremental = " ".join(incremental_body.split())

    assert "std::array<bool, 2> incrementalInputActive_" in header
    assert "incrementalInputActive_[sourceIndex] = false;" in normalized_incremental
    assert "incrementalInputActive_[sourceIndex] = true;" in normalized_incremental
    assert "continuousPulseCarry_" in header
    assert "auto& pulseCarry = continuousPulseCarry_[sourceIndex][axisIndex];" in normalized_incremental
    assert "pulseCarry += requestedPulseFloat;" in normalized_incremental
    assert "pulseCarry -= requestedPulse;" in normalized_incremental
    assert '"incremental input below output threshold stopped"' in normalized_tick
    assert (
        "if (incrementalInputActive_[sourceIndex]) { referencePose_[sourceIndex] = semanticPose;"
        not in normalized_tick
    )
    assert (
        "setBlockerUnlocked(sourceIndex, \"active\", \"incremental input below output threshold\"); return; }"
        not in normalized_tick
    )
    assert (
        "syncIncrementalZeroDeltaUnlocked(sourceSide, targetSide, sourceIndex, targetIndex, semanticPose, stopMessage);"
        in normalized_tick
    )
    zero_branch = normalized_tick.split("if (!hasMotion(deltas)) {", 1)[1].split(
        "setBlockerUnlocked(sourceIndex, \"active\", \"inside native velocity deadzone\");",
        1,
    )[0]
    assert (
        "syncIncrementalZeroDeltaUnlocked(sourceSide, targetSide, sourceIndex, targetIndex, semanticPose"
        in zero_branch
    )


def test_hal_native_continuous_increment_preserves_subthreshold_pulse_carry() -> None:
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    incremental_body = source.split("std::array<double, 6> NativeTeleopController::incrementalDeltasUi(", 1)[1].split(
        "long NativeTeleopController::applyContinuousPulseGate",
        1,
    )[0]
    continuous_branch = incremental_body.split("if (config_.continuousIncrementMode) {", 1)[1].split(
        "} else if (rotation) {",
        1,
    )[0]
    normalized = " ".join(continuous_branch.split())
    pulse_gate_body = incremental_body.split("auto& pulseCarry = continuousPulseCarry_[sourceIndex][axisIndex];", 1)[1]

    assert "filteredDelta = aboveInputThreshold ? rawDelta : 0.0;" in normalized
    assert "pulseCarry += requestedPulseFloat;" in pulse_gate_body
    assert "pulseCarry -= requestedPulse;" in pulse_gate_body


def test_hal_native_records_zero_delta_action_after_incremental_stop() -> None:
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    body = source.split("void NativeTeleopController::recordZeroStopActionUnlocked(", 1)[1].split(
        "void NativeTeleopController::recordActionUnlocked",
        1,
    )[0]

    assert "recordZeroStopActionUnlocked" in header
    assert "NativeTeleopAction action;" in body
    assert "action.side = targetSide;" in body
    assert "action.sourceSide = sourceSide;" in body
    assert "lastAction_ = action;" in body
    assert "actionHistory_.push_back(action);" in body


def test_hal_native_action_history_exposes_pulse_and_limit_diagnostics() -> None:
    hal_types = (REPO_ROOT / "hal" / "include" / "HalTypes.h").read_text(encoding="utf-8")
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    record_body = source.split("void NativeTeleopController::recordActionUnlocked(", 1)[1].split(
        "void NativeTeleopController::recordZeroStopActionUnlocked",
        1,
    )[0]
    append_body = source.split("void appendAction(", 1)[1].split("bool hasMotion", 1)[0]

    assert "std::array<double, 6> requestedDeltaPulse{};" in header
    assert "std::array<double, 6> appliedDeltaPulse{};" in header
    assert "std::array<double, 6> targetPulse{};" in header
    assert "std::array<double, 6> currentPulse{};" in header
    assert "std::array<double, 6> launchDeltaPulse{};" in header
    assert "std::array<double, 6> updateReturn{};" in header
    assert "std::array<double, 6> stopReason{};" in hal_types
    assert "std::array<double, 6> axisIoStatus{};" in hal_types
    assert "std::array<double, 6> stopReason{};" in header
    assert "std::array<double, 6> axisIoStatus{};" in header
    assert "std::array<bool, 6> movingBefore{};" in header
    assert "std::array<bool, 6> moveStarted{};" in header
    assert "std::array<bool, 6> clipped{};" in header
    assert "action.requestedDeltaPulse = result.requestedDeltaPulse;" in record_body
    assert "action.appliedDeltaPulse = result.appliedDeltaPulse;" in record_body
    assert "action.targetPulse = result.targetPulse;" in record_body
    assert "action.currentPulse = result.currentPulse;" in record_body
    assert "action.launchDeltaPulse = result.launchDeltaPulse;" in record_body
    assert "action.updateReturn = result.updateReturn;" in record_body
    assert "action.stopReason = result.stopReason;" in record_body
    assert "action.axisIoStatus = result.axisIoStatus;" in record_body
    assert "action.movingBefore = result.movingBefore;" in record_body
    assert "action.moveStarted = result.moveStarted;" in record_body
    assert "action.clipped = result.clipped;" in record_body
    assert '\\"requestedDeltaPulse\\":' in append_body
    assert '\\"appliedDeltaPulse\\":' in append_body
    assert '\\"targetPulse\\":' in append_body
    assert '\\"currentPulse\\":' in append_body
    assert '\\"launchDeltaPulse\\":' in append_body
    assert '\\"updateReturn\\":' in append_body
    assert '\\"stopReason\\":' in append_body
    assert '\\"axisIoStatus\\":' in append_body
    assert '\\"movingBefore\\":' in append_body
    assert '\\"moveStarted\\":' in append_body
    assert '\\"clipped\\":' in append_body


def test_ltdmc_teleop_reports_axis_launch_and_tracking_state() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    body = source.split("TeleopTargetUpdateResult LTDMCDriver::updateTeleopTargetUi(", 1)[1].split(
        "void LTDMCDriver::stopTeleopSide",
        1,
    )[0]

    assert "result.currentPulse[axisIndex] = actualPulse;" in body
    assert "result.movingBefore[axisIndex] = moving;" in body
    assert "result.axisIoStatus[axisIndex] = static_cast<double>(dmcAxisIoStatus(card, axisNo));" in body
    assert "dmcGetStopReason(card, axisNo, &stopReason)" in body
    assert "result.stopReason[axisIndex] = static_cast<double>(stopReason);" in body
    assert "result.moveStarted[axisIndex] = shouldLaunchMove;" in body
    assert (
        "result.launchDeltaPulse[axisIndex] = shouldLaunchMove ? static_cast<double>(launchDeltaPulse) : 0.0;"
        in body
    )
    assert (
        "applyMotionProfile(card, axisNo, startVelocityPulse, maxVelocityPulse, tacc, tdec, launchDeltaPulse)"
        in body
    )


def test_hal_native_status_exposes_per_hand_input_gate_diagnostics() -> None:
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    status_body = source.split("std::string NativeTeleopController::statusJson() const", 1)[1].split(
        "void NativeTeleopController::loop",
        1,
    )[0]
    tick_body = source.split("void NativeTeleopController::tickSide(", 1)[1].split(
        "std::array<double, 6> NativeTeleopController::velocityDeltasUi",
        1,
    )[0]
    incremental_body = source.split("std::array<double, 6> NativeTeleopController::incrementalDeltasUi(", 1)[1].split(
        "long NativeTeleopController::applyContinuousPulseGate",
        1,
    )[0]

    assert "lastSemanticPose_" in header
    assert "lastRawDelta_" in header
    assert "lastFilteredDelta_" in header
    assert "lastRequestedPulse_" in header
    assert "lastEmittedPulse_" in header
    assert "lastOutputDeltaUi_" in header
    assert "lastDiagnosticTargetSide_" in header
    assert "lastSemanticPose_[sourceIndex] = semanticPose;" in tick_body
    assert "lastDiagnosticTargetSide_[sourceIndex] = targetSide;" in tick_body
    assert "lastRawDelta_[sourceIndex][axisIndex] = rawDelta;" in incremental_body
    assert "lastFilteredDelta_[sourceIndex][axisIndex] = filteredDelta;" in incremental_body
    assert "lastRequestedPulse_[sourceIndex][axisIndex] = requestedPulseFloat;" in incremental_body
    assert "lastEmittedPulse_[sourceIndex][axisIndex] = requestedPulse;" in incremental_body
    assert "lastOutputDeltaUi_[sourceIndex][axisIndex] = deltas[axisIndex];" in incremental_body
    assert '\\"inputs\\":{' in status_body
    assert 'appendInputDiagnostic(' in status_body
    assert '\\"semanticPose\\":' in source
    assert '\\"referencePose\\":' in source
    assert '\\"rawDelta\\":' in source
    assert '\\"filteredDelta\\":' in source
    assert '\\"requestedPulse\\":' in source
    assert '\\"emittedPulse\\":' in source
    assert '\\"outputDeltaUi\\":' in source
    assert '\\"targetSide\\":' in source


def test_hal_native_incremental_rotation_spike_recaptures_reference_before_motion_update() -> None:
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    tick_body = source.split("void NativeTeleopController::tickSide(", 1)[1].split(
        "void NativeTeleopController::syncIncrementalZeroDeltaUnlocked",
        1,
    )[0]
    guard_body = source.split("bool NativeTeleopController::suppressIncrementalRotationSpikeUnlocked(", 1)[1].split(
        "std::array<AxisLimit, 6> NativeTeleopController::effectiveSoftLimits",
        1,
    )[0]

    assert "bool suppressIncrementalRotationSpikeUnlocked(" in header
    assert "constexpr double kIncrementalRotationSpikeGuardDeg = 5.0;" in source
    assert "suppressIncrementalRotationSpikeUnlocked(" in tick_body
    assert tick_body.index("suppressIncrementalRotationSpikeUnlocked(") < tick_body.index(
        "motion_.updateTeleopTargetUi("
    )
    assert "axisIndex = 3" in guard_body
    assert "std::abs(rawDelta) <= kIncrementalRotationSpikeGuardDeg" in guard_body
    assert "referencePose_[sourceIndex] = semanticPose;" in guard_body
    assert "motion_.stopTeleopSide(targetSide);" in guard_body
    assert "targetActive_[targetIndex] = false;" in guard_body
    assert (
        'setBlockerUnlocked(sourceIndex, "blocked", '
        '"incremental rotation input spike suppressed; reference recaptured");'
    ) in guard_body


def test_hal_native_throttles_repeated_zero_delta_actions() -> None:
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    body = source.split("void NativeTeleopController::recordZeroStopActionUnlocked(", 1)[1].split(
        "int NativeTeleopController::gripperSourceIndex",
        1,
    )[0]

    assert "if (hasLastAction_ && !hasMotion(lastAction_.deltas))" in body
    assert "return;" in body


def test_hal_native_tick_isolates_one_hand_errors_from_other_hand_and_grippers() -> None:
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    tick_body = source.split("void NativeTeleopController::tick(double dtSec)", 1)[1].split(
        "void NativeTeleopController::tickSideBestEffort",
        1,
    )[0]
    helper_body = source.split("void NativeTeleopController::tickSideBestEffort", 1)[1].split(
        "void NativeTeleopController::tickSide(",
        1,
    )[0]

    assert "void tickSideBestEffort(int sourceIndex, const Omega7State& hand, double dtSec);" in header
    assert "tickSideBestEffort(0, hands[0], dtSec);" in tick_body
    assert "tickSideBestEffort(1, hands[1], dtSec);" in tick_body
    assert tick_body.index("tickSideBestEffort(0, hands[0], dtSec);") < tick_body.index(
        "tickSideBestEffort(1, hands[1], dtSec);"
    )
    assert "tickGrippers(hands);" in tick_body
    assert tick_body.index("tickSideBestEffort(1, hands[1], dtSec);") < tick_body.index("tickGrippers(hands);")
    assert "try {" in helper_body
    assert "tickSide(sourceIndex, hand, dtSec);" in helper_body
    assert "catch (const std::exception& exc)" in helper_body
    assert "lastError_ = exc.what();" in helper_body
    assert 'setBlockerUnlocked(sourceIndex, "blocked", exc.what());' in helper_body


def test_hal_native_zero_increment_hard_stops_active_target() -> None:
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    body = source.split("void NativeTeleopController::syncIncrementalZeroDeltaUnlocked(", 1)[1].split(
        "std::array<AxisLimit, 6> NativeTeleopController::effectiveSoftLimits",
        1,
    )[0]

    assert "if (targetActive_[targetIndex]) {" in body
    assert "motion_.stopTeleopSide(targetSide);" in body
    assert "targetActive_[targetIndex] = false;" in body
    assert "recordZeroStopActionUnlocked(sourceSide, targetSide);" in body
    assert "motion_.updateTeleopTargetUi(" not in body


def test_ltdmc_native_teleop_reattaches_moving_axis_without_reprofiling_busy_axis() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    body = source.split("TeleopTargetUpdateResult LTDMCDriver::updateTeleopTargetUi(", 1)[1].split(
        "void LTDMCDriver::stopTeleopSide",
        1,
    )[0]

    assert "const bool reattachMovingTarget = moving && !teleopTargetActive_[index];" in body
    assert "if (reattachMovingTarget) {" in body
    reattach_body = body.split("if (reattachMovingTarget) {", 1)[1].split("}", 1)[0]
    assert "teleopTargetPulse_[index] = actualPulse;" in reattach_body
    assert "teleopTargetActive_[index] = true;" in reattach_body
    assert "const bool shouldLaunchMove = !moving;" in body
    assert body.index("const bool reattachMovingTarget") < body.index("const auto basePulse")
    assert body.index("const bool shouldLaunchMove") < body.index("applyMotionProfile(")


def test_omega7_gripper_gap_uses_direct_encoder_and_angle_fallbacks() -> None:
    source = (REPO_ROOT / "hal" / "src" / "Omega7Driver.cpp").read_text(encoding="utf-8")
    read_body = source.split("std::array<Omega7State, 2> Omega7Driver::readState()", 1)[1].split(
        "void Omega7Driver::setGravityCompensation",
        1,
    )[0]

    assert "using DhdGetGripperAngleDeg" in source
    assert "using DhdGetGripperEncoder" in source
    assert "using DhdGripperEncoderToGap" in source
    assert 'GetProcAddress(dhdModule, "dhdGetGripperAngleDeg")' in source
    assert 'GetProcAddress(dhdModule, "dhdGetGripperEncoder")' in source
    assert 'GetProcAddress(dhdModule, "dhdGripperEncoderToGap")' in source
    assert "constexpr double kOmega7GripperOpenDeg = 30.0;" in source
    assert "constexpr double kOmega7GripperGapMinReliableMm = 0.001;" in source
    assert "dhdGetGripperGap" in read_body
    assert "dhdGripperEncoderToGap" in read_body
    assert "dhdGetGripperAngleDeg" in read_body
    assert "item.gripperGap = selectedGapMm / 1000.0;" in read_body
    assert "item.gripperGapAvailable = haveGap;" in read_body


def test_settings_gripper_teleop_toggle_logs_start_stop_and_errors() -> None:
    source = (REPO_ROOT / "frontend" / "src" / "views" / "SettingsView.tsx").read_text(encoding="utf-8")
    body = source.split("const handleTeleopToggle = async () => {", 1)[1].split(
        "const requestGripperTarget =",
        1,
    )[0]

    assert "commandLog(injectLog, '[GRIPPER]'" in body
    assert "startGripperTeleop()" in body
    assert "stopGripperTeleop()" in body
    assert "commandErrorMessage(error)" in body
    assert "catch (error)" in body
    assert "/* ignore */" not in body


def test_hal_native_incremental_honors_continuous_increment_settings() -> None:
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    server = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    payload = (REPO_ROOT / "backend" / "services" / "teleop_mapping.py").read_text(encoding="utf-8")
    incremental_body = source.split("std::array<double, 6> NativeTeleopController::incrementalDeltasUi(", 1)[1].split(
        "void NativeTeleopController::tickGrippers",
        1,
    )[0]

    assert "bool continuousIncrementMode{true}" in header
    assert "double translationInputEpsilonM{0.000005}" in header
    assert "double rotationInputEpsilonDeg{0.08}" in header
    assert "double translationMinActivePulse{3.0}" in header
    assert "double rotationMinActivePulse{3.0}" in header
    assert "int continuousMicroConfirmTicks{2}" in header
    assert "continuousDirection_" in header
    assert "continuousStreak_" in header
    assert "applyContinuousPulseGate" in header
    assert 'jsonBoolValue(body, "continuousIncrementMode"' in server
    assert 'jsonNumberValue(body, "translationInputEpsilon"' in server
    assert '"continuousIncrementMode": self._continuous_increment_mode(config)' in payload
    assert '"translationInputEpsilon": self._translation_input_epsilon_m(config)' in payload
    assert "config_.continuousIncrementMode" in incremental_body
    assert "config_.translationInputEpsilonM" in incremental_body
    assert "config_.rotationInputEpsilonDeg" in incremental_body
    assert "applyContinuousPulseGate(" in incremental_body
    assert "constexpr double kRotationInputEpsilonFloorDeg = 0.08;" in source
    assert "constexpr double kTranslationMicroConfirmUpperM = 0.00002;" in source
    assert "constexpr double kRotationMicroConfirmUpperDeg = 0.18;" in source
    assert "constexpr int kContinuousMicroConfirmTicksFloor = 2;" in source
    assert "std::max(kRotationInputEpsilonFloorDeg, normalized.rotationInputEpsilonDeg)" in source
    assert "std::max(kContinuousMicroConfirmTicksFloor, normalized.continuousMicroConfirmTicks)" in source
    assert "if (config_.continuousMicroConfirmTicks <= 0)" in source


def test_hal_native_incremental_uses_target_stage_impulse_and_units() -> None:
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    tick_body = source.split("void NativeTeleopController::tickSide(", 1)[1].split(
        "std::array<double, 6> NativeTeleopController::velocityDeltasUi",
        1,
    )[0]
    incremental_body = source.split("std::array<double, 6> NativeTeleopController::incrementalDeltasUi(", 1)[1].split(
        "void NativeTeleopController::tickGrippers",
        1,
    )[0]
    normalized = " ".join(incremental_body.split())

    assert "config_.swapTeleopChannels ? sideFromIndex(1 - sourceIndex) : sourceSide" in tick_body
    assert "const int targetIndex = sideIndex(targetSide);" in incremental_body
    assert "config_.impulseCoeff[targetIndex][axisIndex]" in incremental_body
    assert "config_.impulseCoeff[sourceIndex][axisIndex]" not in incremental_body
    assert "config_.axisOutputScale[targetIndex][axisIndex]" in incremental_body
    assert "pulsePerUnit(targetSide, axis)" in incremental_body
    assert (
        "const double physical = requestedPulse / unitPulse; "
        "deltas[axisIndex] = rotation ? physical : physical * 1000.0;"
    ) in normalized
    mapped_direction_body = source.split("double NativeTeleopController::mappedDirection", 1)[1].split(
        "void NativeTeleopController::setBlockerUnlocked",
        1,
    )[0]
    assert "const int targetIndex = sideIndex(targetSide);" in mapped_direction_body
    assert "config_.impulseCoeff[targetIndex][axisIndex]" in mapped_direction_body
    assert "config_.impulseCoeff[sourceIndex][axisIndex]" not in mapped_direction_body


def test_hal_native_kalman_filter_keeps_status_schema_and_gates_pose_source() -> None:
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    server = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    payload = (REPO_ROOT / "backend" / "services" / "teleop_mapping.py").read_text(encoding="utf-8")
    status_body = source.split("std::string NativeTeleopController::statusJson() const", 1)[1].split(
        "void NativeTeleopController::loop",
        1,
    )[0]
    tick_body = source.split("void NativeTeleopController::tickSide(", 1)[1].split(
        "void NativeTeleopController::syncIncrementalZeroDeltaUnlocked",
        1,
    )[0]

    assert "bool kalmanFilterEnabled{false}" in header
    assert "double kalmanBeta{0.05}" in header
    assert "double kalmanDtMaxSec{0.05}" in header
    assert "double kalmanTranslationMeasurementVariance{1e-8}" in header
    assert "double kalmanRotationMeasurementVariance{0.04}" in header
    assert "double kalmanTranslationIntentVelocityThreshold{0.0005}" in header
    assert "double kalmanRotationIntentVelocityThreshold{0.5}" in header
    assert "struct KalmanAxisState" in header
    assert "kalmanPoseForSide" in header
    assert "kalmanIntentWeight" in header
    assert "applyKalmanIntentWeights" in header
    assert "lastIntentWeight_" in header
    assert "kalmanStates_" in header
    assert 'jsonBoolValue(body, "kalmanFilterEnabled", config.kalmanFilterEnabled)' in server
    assert 'jsonNumberValue(body, "kalmanBeta", config.kalmanBeta)' in server
    assert (
        'jsonNumberValue(body, "kalmanRotationMeasurementVariance", '
        "config.kalmanRotationMeasurementVariance)"
    ) in server
    assert 'jsonNumberValue(body, "kalmanTranslationIntentVelocityThreshold"' in server
    assert '"kalmanFilterEnabled": bool(teleop.get("kalmanFilterEnabled", False))' in payload
    assert '"kalmanBeta": float(teleop.get("kalmanBeta", ICF_TELEOP_DEFAULTS["kalmanBeta"]))' in payload
    assert '"kalmanTranslationIntentVelocityThreshold": float(' in payload
    assert "const auto rawSemanticPose = omegaPoseToSemantic(hand.pose, config_.mappingMode);" in tick_body
    assert "const auto semanticPose = kalmanPoseForSide(sourceIndex, rawSemanticPose, dtSec);" in tick_body
    assert "lastSemanticPose_[sourceIndex] = semanticPose;" in tick_body
    assert "config_.kalmanBeta" in source
    assert "config_.kalmanRotationMeasurementVariance" in source
    assert "double wrapKalmanRotationResidualDeg(double residualDeg)" in source
    assert "gamma = wrapKalmanRotationResidualDeg(gamma);" in source
    assert "const double controlInput = 0.0;" in source
    assert "double gamma = measurement - predictedMeasurement;" in source
    assert "const double hPredictedPHt = predictedP00;" in source
    assert "const double pCrossSymmetric = 0.5 * (state.p01 + state.p10);" in source
    assert "const double qCrossSymmetric = 0.5 * (state.q01 + state.q10);" in source
    assert "const double q00Adaptive = gainPosition * gamma * gamma * gainPosition;" in source
    assert "const double rAdaptive = gamma * gamma - hPredictedPHt;" in source
    assert "const double velocityConfidence =" in source
    assert "if (speed >= threshold)" in source
    assert "deltas = applyKalmanIntentWeights(sourceIndex, deltas);" in source
    assert "kalman" not in status_body.lower()
    assert "lastSemanticPose_" in status_body


def test_hal_native_jodell_driver_binds_required_vendor_symbols() -> None:
    header = (REPO_ROOT / "hal" / "include" / "JodellGripperDriver.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "JodellGripperDriver.cpp").read_text(encoding="utf-8")

    assert "class JodellGripperDriver" in header
    assert "serialOperation" in source
    assert "clawEnable" in source
    assert "runWithParam" in source
    assert "getClawCurrentLocation" in source
    assert "COM8" in source
    assert "COM9" in source


def test_hal_native_gripper_follows_physical_hand_even_without_arm_logical_activation() -> None:
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    body = source.split("void NativeTeleopController::tickGrippers(", 1)[1].split(
        "double NativeTeleopController::mappedDirection",
        1,
    )[0]

    assert "logicalConnected_[sourceIndex]" not in body
    assert "const auto& hand = hands[sourceIndex];" in body
    assert body.index("const auto& hand = hands[sourceIndex];") < body.index("enqueueGripperCommand")


def test_hal_native_gripper_surfaces_command_status_and_retries_port_open() -> None:
    controller_header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    controller_source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    gripper_source = (REPO_ROOT / "hal" / "src" / "JodellGripperDriver.cpp").read_text(encoding="utf-8")
    gripper_header = (REPO_ROOT / "hal" / "include" / "JodellGripperDriver.h").read_text(encoding="utf-8")
    normalized_controller = " ".join(controller_source.split())
    normalized_gripper = " ".join(gripper_source.split())

    assert "std::array<bool, 2> gripperLastCommandOk_" in controller_header
    assert "std::array<std::string, 2> gripperLastMessage_" in controller_header
    assert "std::array<double, 2> gripperPositionsMm_" in controller_header
    assert '\\"grippers\\":{\\"left\\"' in controller_source
    assert (
        "enqueueGripperCommand(targetIndex, targetSide, targetMm, config_.gripper.speed, config_.gripper.torque)"
        in normalized_controller
    )
    assert "void NativeTeleopController::gripperLoop()" in controller_source
    assert "const bool ok = gripper_.commandTarget" in normalized_controller
    assert "command.torque, &message, false);" in normalized_controller
    assert "gripperLastCommandOk_[command.targetIndex] = ok;" in normalized_controller
    assert "gripperLastMessage_[command.targetIndex] = message;" in normalized_controller
    assert "gripperPositionsMm_ = gripperPositions;" in normalized_controller
    assert '",\\"positionMm\\":' in controller_source
    assert "positionMmSnapshot" in gripper_header
    assert "const auto gripperPositions = gripper_.positionMmSnapshot(gripperPositionsMm_);" in controller_source
    assert "const auto gripperPositions = gripper_.positionMm();" not in controller_source
    assert "std::array<double, 2> positionMm_" in gripper_header
    assert "getClawCurrentLocation_(slave)" in gripper_source
    assert "lastError_ = std::string(\"native gripper \")" not in normalized_controller
    assert "constexpr auto kPortSwitchSettleMs = std::chrono::milliseconds(50);" in gripper_source
    assert "for (int attempt = 0; attempt < 5; ++attempt)" in normalized_gripper
    assert "std::this_thread::sleep_for(kPortSwitchSettleMs);" in normalized_gripper
    assert "(void)serialOperation_(port, config_.baudrate, 0);" in normalized_gripper


def test_hal_native_gripper_io_is_decoupled_from_motion_loop() -> None:
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    tick_body = source.split("void NativeTeleopController::tickGrippers(", 1)[1].split(
        "void NativeTeleopController::enqueueGripperCommand",
        1,
    )[0]
    assert "void NativeTeleopController::gripperLoop()" in source
    loop_body = source.split("void NativeTeleopController::gripperLoop()", 1)[1].split(
        "double NativeTeleopController::mappedDirection",
        1,
    )[0]

    assert "#include <condition_variable>" in header
    assert "std::condition_variable gripperCv_" in header
    assert "std::thread gripperWorker_" in header
    assert "std::array<PendingGripperCommand, 2> pendingGripperCommands_" in header
    assert "enqueueGripperCommand(" in tick_body
    assert "gripper_.commandTarget" not in tick_body
    assert "gripper_.commandTarget" in loop_body


def test_hal_native_gripper_worker_samples_positions_without_commands() -> None:
    gripper_header = (REPO_ROOT / "hal" / "include" / "JodellGripperDriver.h").read_text(encoding="utf-8")
    gripper_source = (REPO_ROOT / "hal" / "src" / "JodellGripperDriver.cpp").read_text(encoding="utf-8")

    assert "bool readPositionMm(Side side, std::string* message = nullptr);" in gripper_header
    assert "bool JodellGripperDriver::readPositionMm(" in gripper_source


def test_hal_native_gripper_worker_starts_only_when_gripper_teleop_is_enabled() -> None:
    controller_source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    start_body = controller_source.split(
        "void NativeTeleopController::start(bool leftConnected, bool rightConnected)",
        1,
    )[1].split("void NativeTeleopController::stop()", 1)[0]
    normalized = " ".join(start_body.split())

    assert "gripperTeleopEnabled = config_.gripperTeleopEnabled;" in normalized
    assert "if (gripperTeleopEnabled) { startGripperWorker(); }" in normalized


def test_hal_native_gripper_uses_isolated_jodell_worker_processes() -> None:
    gripper_header = (REPO_ROOT / "hal" / "include" / "JodellGripperDriver.h").read_text(encoding="utf-8")
    gripper_source = (REPO_ROOT / "hal" / "src" / "JodellGripperDriver.cpp").read_text(encoding="utf-8")
    worker_source = (REPO_ROOT / "hal" / "src" / "JodellGripperWorker.cpp").read_text(encoding="utf-8")
    controller_header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    controller_source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    server_source = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    cmake = (REPO_ROOT / "hal" / "CMakeLists.txt").read_text(encoding="utf-8")
    build_cmd = (REPO_ROOT / "hal" / "build_hal.cmd").read_text(encoding="utf-8")
    command_body = gripper_source.split("bool JodellGripperDriver::commandTarget(", 1)[1].split(
        "bool JodellGripperDriver::readPositionMm",
        1,
    )[0]
    read_body = gripper_source.split("bool JodellGripperDriver::readPositionMm", 1)[1].split(
        "std::array<double, 2> JodellGripperDriver::targetMm",
        1,
    )[0]
    loop_body = controller_source.split("void NativeTeleopController::gripperLoop()", 1)[1].split(
        "void NativeTeleopController::sampleGripperPosition",
        1,
    )[0]
    sample_body = (
        controller_source.split("void NativeTeleopController::sampleGripperPosition", 1)[1].split(
            "double NativeTeleopController::mappedDirection",
            1,
        )[0]
        if "void NativeTeleopController::sampleGripperPosition" in controller_source
        else ""
    )
    normalized_loop = " ".join(loop_body.split())
    normalized_sample = " ".join(sample_body.split())

    assert "bool processWorkersEnabled{true};" in gripper_header
    assert "std::array<ProcessWorkerHandle, 2> workerProcesses_" in gripper_header
    assert "ensureProcessWorkerUnlocked" in gripper_source
    assert "CreateProcessA" in gripper_source
    assert "JodellGripperWorker.exe" in gripper_source
    assert "commandProcessWorkerUnlocked" in gripper_source
    assert "closeProcessWorkersUnlocked" in gripper_source
    assert "processWorkersEnabled = false" in worker_source
    assert "std::getline(std::cin, line)" in worker_source
    assert "driver.commandTarget" in worker_source
    assert "driver.readPositionMm" in worker_source
    assert command_body.index("if (config_.processWorkersEnabled)") < command_body.index("ensureLoadedUnlocked")
    assert read_body.index("if (config_.processWorkersEnabled)") < read_body.index("ensureLoadedUnlocked")
    assert '\\"workerMode\\":\\"' in controller_source
    assert "processWorkersEnabled" in controller_source
    assert "gripperProcessWorkersEnabled" in server_source
    assert "jodellWorkerExePath" in server_source
    assert "gripperWorkerCommandTimeoutMs" in server_source
    assert "add_executable(JodellGripperWorker src/JodellGripperWorker.cpp)" in cmake
    assert "JodellGripperWorker.next.exe" in build_cmd
    assert "getClawCurrentLocation_(slave)" in gripper_source
    assert "void sampleGripperPosition(Side side);" in controller_header
    assert "nextGripperSampleIndex_" not in controller_header
    assert "constexpr auto kGripperPositionSampleInterval = std::chrono::microseconds(33333);" in controller_source
    assert "gripperCv_.wait_until(lock, nextSampleAt, [&]" in normalized_loop
    assert "sampleGripperPosition(Side::Left);" in normalized_loop
    assert "sampleGripperPosition(Side::Right);" in normalized_loop
    assert "sampleGripperPosition(sideFromIndex(sampleIndex));" not in normalized_loop
    assert "const bool ok = gripper_.readPositionMm(side, &message);" in normalized_sample
    assert "gripperPositionsMm_ = gripper_.positionMmSnapshot(gripperPositionsMm_);" in normalized_sample
    assert "gripperLastCommandOk_[index] = ok;" in normalized_sample
    assert "gripperLastMessage_[index] = message;" in normalized_sample


def test_hal_native_gripper_errors_do_not_pollute_arm_teleop_last_error() -> None:
    controller_source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    normalized = " ".join(controller_source.split())
    command_loop_body = controller_source.split("void NativeTeleopController::gripperLoop()", 1)[1].split(
        "void NativeTeleopController::sampleGripperPosition",
        1,
    )[0]
    sample_body = controller_source.split("void NativeTeleopController::sampleGripperPosition", 1)[1].split(
        "bool NativeTeleopController::running() const",
        1,
    )[0]
    direct_command_body = controller_source.split("bool NativeTeleopController::commandGripperTarget(", 1)[1].split(
        "std::string NativeTeleopController::statusJson() const",
        1,
    )[0]

    assert "lastError_ = std::string(\"native gripper \")" not in normalized
    assert "gripperLastCommandOk_[command.targetIndex] = ok;" in command_loop_body
    assert "gripperLastMessage_[command.targetIndex] = message;" in command_loop_body
    assert "gripperLastCommandOk_[index] = ok;" in sample_body
    assert "gripperLastMessage_[index] = message;" in sample_body
    assert "gripperLastCommandOk_[index] = ok;" in direct_command_body
    assert "gripperLastMessage_[index] = driverMessage;" in direct_command_body


def test_hal_native_jodell_driver_closes_other_active_port_before_switching() -> None:
    gripper_header = (REPO_ROOT / "hal" / "include" / "JodellGripperDriver.h").read_text(encoding="utf-8")
    gripper_source = (REPO_ROOT / "hal" / "src" / "JodellGripperDriver.cpp").read_text(encoding="utf-8")
    normalized = " ".join(gripper_source.split())

    assert "bool ensurePortOpenUnlocked(int index, int port, std::string* message);" in gripper_header
    assert "std::array<int, 2> activePorts_" in gripper_header
    assert "selectPortUnlocked" not in gripper_header
    assert "selectPortUnlocked" not in gripper_source
    assert "for (int& activePort : activePorts_)" in normalized
    assert "activePort > 0 && activePort != port" in normalized
    assert "(void)serialOperation_(activePort, config_.baudrate, 0);" in normalized
    assert "if (activePorts_[index] == port)" in normalized
    assert "activePorts_[index] = port;" in normalized


def test_hal_native_stop_clears_logical_connection_state() -> None:
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    stop_body = source.split("void NativeTeleopController::stop() {", 1)[1].split(
        "void NativeTeleopController::startGripperWorker()",
        1,
    )[0]
    normalized = " ".join(stop_body.split())

    assert "logicalConnected_ = {false, false};" in normalized


def test_hal_native_gripper_teleop_throttles_background_jodell_commands() -> None:
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    tick_body = source.split("void NativeTeleopController::tickGrippers(", 1)[1].split(
        "void NativeTeleopController::enqueueGripperCommand",
        1,
    )[0]
    normalized_tick = " ".join(tick_body.split())

    assert "constexpr int kGripperTeleopDeadbandFloorCounts = 1;" in source
    assert "constexpr double kGripperTeleopMinCommandIntervalFloorMs = 10.0;" in source
    assert "normalized.gripperDeadbandCounts = std::max(" in source
    assert "normalized.gripperMinCommandIntervalMs = std::max(" in source
    assert "int gripperDeadbandCounts{1};" in header
    assert "double gripperMinCommandIntervalMs{20.0};" in header
    assert (
        "if (gripperLastRaw_[targetIndex] >= 0 && elapsedMs < config_.gripperMinCommandIntervalMs)"
    ) in normalized_tick
    assert (
        "if (gripperLastRaw_[targetIndex] >= 0 && std::abs(raw - gripperLastRaw_[targetIndex]) "
        "< config_.gripperDeadbandCounts)"
    ) in normalized_tick


def test_hal_native_gripper_icf_min_gap_contract() -> None:
    header = (REPO_ROOT / "hal" / "include" / "NativeTeleopController.h").read_text(encoding="utf-8")
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    server = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    tick_body = source.split("void NativeTeleopController::tickGrippers(", 1)[1].split(
        "void NativeTeleopController::enqueueGripperCommand",
        1,
    )[0]
    command_body = source.split("bool NativeTeleopController::commandGripperTarget(", 1)[1].split(
        "std::string NativeTeleopController::statusJson() const",
        1,
    )[0]

    assert "bool gripperIcfTargetProtectionEnabled{true};" in header
    assert "double gripperIcfTargetMinGapMm{1.02};" in header
    assert "effectiveGripperTargetMm" in header
    assert "effectiveGripperTargetMm(targetMm)" in command_body
    assert "effectiveGripperTargetMm(targetMm)" in tick_body
    assert 'jsonBoolValue(body, "icfTargetProtectionEnabled"' in server
    assert 'jsonNumberValue(body, "icfTargetMinGapMm"' in server


def test_hal_native_status_reports_logical_hand_connections() -> None:
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    status_body = source.split("std::string NativeTeleopController::statusJson() const", 1)[1].split(
        "void NativeTeleopController::loop",
        1,
    )[0]

    assert '\\"logicalConnected\\":[' in status_body
    assert "logicalConnected_[0]" in status_body
    assert "logicalConnected_[1]" in status_body


def test_hal_native_status_reports_gripper_command_speed_for_acceptance() -> None:
    source = (REPO_ROOT / "hal" / "src" / "NativeTeleopController.cpp").read_text(encoding="utf-8")
    status_body = source.split("std::string NativeTeleopController::statusJson() const", 1)[1].split(
        "void NativeTeleopController::loop",
        1,
    )[0]

    assert '\\"gripperCommand\\":{\\"speed\\":' in status_body
    assert "config_.gripper.speed" in status_body
    assert "config_.gripper.torque" in status_body


def test_hal_native_acceptance_script_is_no_motion_by_default() -> None:
    source = (REPO_ROOT / "scripts" / "accept-hal-native-teleop.ps1").read_text(encoding="utf-8-sig")

    assert "/teleop/native/start" in source
    assert "leftConnected = $false" in source
    assert "rightConnected = $false" in source
    assert "gripperTeleopEnabled = $false" in source
    assert 'controlMode = "incremental_position"' in source
    assert 'controlMode = "velocity_admittance"' not in source
    assert "/motion/" not in source
    assert "/gripper/" not in source
    assert "Observation mode only reads /teleop/native/status" in source
    assert "[switch]$RequireActions" in source
    assert "[switch]$RequireLeftAction" in source
    assert "[switch]$RequireRightAction" in source
    assert "[switch]$RequireCrossMapping" in source
    assert "[switch]$RequireAllAxes" in source
    assert "[switch]$RequireGripperChange" in source
    assert "[switch]$RequireForceOutput" in source
    assert "[switch]$RequireGravityCompensation" in source
    assert "[switch]$RequireZeroStop" in source
    assert "[switch]$VerifyReport" in source
    assert "[switch]$Strict" in source
    assert "$Strict" in source
    assert "$RequireZeroStop = $true" in source
    assert "$RequireCrossMapping = $true" in source
    assert "$RequireAllAxes = $true" in source
    assert "$VerifyReport = $true" in source
    assert "Strict HAL-native teleop checklist" in source
    assert "Move left Omega.7 to drive the right arm" in source
    assert "Move right Omega.7 to drive the left arm" in source
    assert "Exercise X/Y/Z/Roll/Pitch/Yaw on both target arms" in source
    assert "Open and close both grippers" in source
    assert "Return both Omega.7 hands to center and confirm motion stops" in source
    assert "verify-hal-native-teleop-report.ps1" in source
    assert "observedSourceSides" in source
    assert "logicalConnectedAllEnabled" in source
    assert "gripperCommandSpeedOk" in source
    assert "observedTargetSides" in source
    assert "observedSourceTargetPairs" in source
    assert "observedMovingSourceTargetPairs" in source
    assert "observedAxes" in source
    assert "observedTargetAxes" in source
    assert "missingAxes" in source
    assert "gripperTargetRanges" in source
    assert "gateFailures" in source
    assert "zeroStopObserved" in source
    assert "No left->right native teleop action was captured" in source
    assert "No non-zero left->right native teleop action was captured" in source
    assert "Not all semantic axes were captured" in source
    assert "No final zero-delta native teleop stop was observed" in source
    assert "No non-zero native teleop actions were captured" in source
    assert "exit 2" in source
