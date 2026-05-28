from __future__ import annotations

from backend.core.defaults import default_config


def test_hal_defaults_use_backend_hal_boundary() -> None:
    config = default_config()

    assert config["hal"]["mode"] == "real"
    assert config["hal"]["baseUrl"] == "http://localhost:8091"
    assert config["hal"]["wsUrl"] == "ws://localhost:8091/ws/telemetry"
    assert config["hal"]["ltdmcDllPath"].endswith("LTDMC.dll")
    assert config["hal"]["dhdDllPath"].endswith("dhd64.dll")


def test_motion_translation_profile_uses_um_units() -> None:
    config = default_config()

    translation = config["motion"]["leftProfile"]["translation"]
    assert translation["startSpeed"] == 300
    assert translation["maxSpeed"] == 4000
    assert translation["accTimeSec"] == 0.05
    assert translation["decTimeSec"] == 0.05
    assert "acceleration" not in translation
    assert "maxAcceleration" not in translation


def test_omega7_teleop_defaults_match_icf_strategy() -> None:
    config = default_config()
    teleop = config["teleop"]

    assert teleop["engine"] == "hal_native"
    assert teleop["controlMode"] == "incremental_position"
    assert teleop["nativeLoopHz"] == 100
    assert teleop["nativeTranslationDeadzoneM"] == 0.002
    assert teleop["nativeTranslationFullScaleM"] == 0.04
    assert teleop["nativeRotationDeadzoneDeg"] == 2.0
    assert teleop["nativeRotationFullScaleDeg"] == 30.0
    assert teleop["nativeVelocitySmoothingMs"] == 40.0
    assert teleop["kalmanFilterEnabled"] is False
    assert teleop["kalmanBeta"] == 0.05
    assert teleop["kalmanMinVariance"] == 1e-12
    assert teleop["kalmanMaxVariance"] == 100.0
    assert teleop["kalmanDtMinSec"] == 0.001
    assert teleop["kalmanDtMaxSec"] == 0.05
    assert teleop["kalmanTranslationPositionVariance"] == 1e-8
    assert teleop["kalmanTranslationVelocityVariance"] == 1e-4
    assert teleop["kalmanTranslationMeasurementVariance"] == 1e-8
    assert teleop["kalmanTranslationProcessPositionVariance"] == 1e-10
    assert teleop["kalmanTranslationProcessVelocityVariance"] == 1e-8
    assert teleop["kalmanRotationPositionVariance"] == 0.25
    assert teleop["kalmanRotationVelocityVariance"] == 4.0
    assert teleop["kalmanRotationMeasurementVariance"] == 0.04
    assert teleop["kalmanRotationProcessPositionVariance"] == 1e-4
    assert teleop["kalmanRotationProcessVelocityVariance"] == 1e-3
    assert teleop["kalmanTranslationIntentVelocityThreshold"] == 0.0005
    assert teleop["kalmanRotationIntentVelocityThreshold"] == 0.5
    assert teleop["strategyVersion"] == "e2e_omega7_native_v27_right_yaw333_com9_20260520"
    assert teleop["mappingMode"] == "direct"
    assert teleop["swapHands"] is False
    assert teleop["swapTeleopChannels"] is True
    assert teleop["stabilityMode"] == "off"
    assert teleop["leftTranslationScale"] == 1.0
    assert teleop["rightTranslationScale"] == 1.0
    assert teleop["leftRotationScale"] == 1.0
    assert teleop["rightRotationScale"] == 1.0
    assert teleop["leftForceFeedback"] is True
    assert teleop["rightForceFeedback"] is True
    assert teleop["leftAxisOutputScale"] == [0.40, 0.25, 0.25, 0.40, 0.20, 0.20]
    assert teleop["rightAxisOutputScale"] == [0.40, 0.25, 0.25, 0.40, 0.20, 0.20]
    assert teleop["translationStepLimitPulse"] == 4000
    assert teleop["rotationStepLimitPulse"] == 1250
    assert teleop["translationPulseDeadband"] == 2
    assert teleop["rotationPulseDeadband"] == 2
    assert teleop["translationStartVelocityUmS"] == 600.0
    assert teleop["translationMaxVelocityUmS"] == 8000.0
    assert teleop["rotationStartVelocityDegS"] == 1.0
    assert teleop["rotationMaxVelocityDegS"] == 12.0
    assert teleop["continuousIncrementMode"] is True
    assert teleop["translationInputEpsilon"] == 0.00002
    assert teleop["rotationInputEpsilon"] == 0.03
    assert teleop["translationMinActivePulse"] == 3
    assert teleop["rotationMinActivePulse"] == 3
    assert teleop["continuousMicroConfirmTicks"] == 0
    assert teleop["diagLog"] is False
    assert teleop["leftEnabledAxes"] == [True] * 6
    assert teleop["rightEnabledAxes"] == [True] * 6
    assert teleop["softLimitUnitSpec"] == ["um", "um", "um", "deg", "deg", "deg"]
    assert teleop["leftSoftLimitMin"] == [-25000.0, -37500.0, -37500.0, -100.0, -100.0, -7.0]
    assert teleop["leftSoftLimitMax"] == [25000.0, 37500.0, 37500.0, 100.0, 100.0, 7.0]
    assert teleop["rightSoftLimitMin"] == [-25000.0, -37500.0, -37500.0, -100.0, -100.0, -7.0]
    assert teleop["rightSoftLimitMax"] == [25000.0, 37500.0, 37500.0, 0.0, 100.0, 7.0]
    assert teleop["leftImpulseCoeff"] == [-5000000, 5000000, -10000000, 1667, -2500, -333.3333]
    assert teleop["rightImpulseCoeff"] == [-5000000, -10000000, -5000000, 1667, 2500, 3333.333]
    assert teleop["leftDirectionSign"] == [1, -1, -1, 1, -1, -1]
    assert teleop["rightDirectionSign"] == [1, 1, -1, 1, 1, 1]
    assert teleop["gripperTeleop"]["leftSourceHand"] == "PhysicalRight"
    assert teleop["gripperTeleop"]["rightSourceHand"] == "PhysicalLeft"
    assert teleop["gripperTeleop"]["leftGapInvert"] is False
    assert teleop["gripperTeleop"]["rightGapInvert"] is False


def test_motion_kinematics_defaults_match_icf_mapping() -> None:
    config = default_config()
    kinematics = config["motion"]["kinematics"]

    assert kinematics["axisUnitSpec"] == ["mm", "mm", "mm", "deg", "deg", "deg"]
    assert kinematics["leftAxisMap"] == [0, 1, 3, 5, 4, 2]
    assert kinematics["rightAxisMap"] == [8, 6, 11, 14, 7, 13]
    assert kinematics["leftPhysicalAxis"] == [0, 1, 3, 5, 4, 2]
    assert kinematics["rightPhysicalAxis"] == [2, 0, 5, 8, 1, 7]
    assert kinematics["leftSignedPulsePerUnit"] == [-5000.0, 5000.0, -10000.0, 1666.666667, -2500.0, -3333.333]
    assert kinematics["rightSignedPulsePerUnit"] == [
        -5000.0,
        -10000.0,
        -5000.0,
        1666.666667,
        2500.0,
        333.3333,
    ]


def test_work_origin_defaults_match_icf_reference_position() -> None:
    config = default_config()
    origin = config["motion"]["origin"]

    assert config["motion"]["workOriginStrategyVersion"] == "icf_work_origin_20260521_rotation_limit_v2"
    assert origin["valid"] is True
    assert origin["leftValid"] is True
    assert origin["rightValid"] is True
    assert origin["leftPulse"] == [258494.0, -200013.0, 274821.0, 49833.0, 84839.0, 381102.0]
    assert origin["rightPulse"] == [99772.0, 382486.0, 881207.0, 19527.0, -175127.0, -9668.0]


def test_force_defaults_match_nidaq_reference_project() -> None:
    config = default_config()

    assert config["force"]["leftIp"] == "Dev5/ai0:5"
    assert config["force"]["rightIp"] == "Dev3/ai0:5"
    assert config["force"]["sampleHz"] == 200
    assert config["force"]["inputMode"] == "DIFF"
    assert config["force"]["leftCalibrationPath"].endswith("FT32918.cal")
    assert config["force"]["rightCalibrationPath"].endswith("FT38799.cal")


def test_safety_defaults_are_stored_in_backend_units() -> None:
    config = default_config()

    assert config["safety"]["fxyStopN"] == 4
    assert config["safety"]["fzStopN"] == 5
    assert config["safety"]["momentStopNm"] == 0.04
    assert "fxyStopMn" not in config["safety"]
    assert "momentStopMNm" not in config["safety"]


def test_pico_script_defaults_point_to_reference_tools() -> None:
    config = default_config()

    assert config["picoVision"]["ip"] == "10.90.132.174"
    assert config["picoVision"]["videoPort"] == 12345
    assert config["picoVision"]["commandPort"] == 13579
    assert config["picoVision"]["scriptsDir"].endswith("PicoWirelessTools")


def test_storage_defaults_separate_recording_fps_from_camera_preview() -> None:
    config = default_config()

    assert config["cameras"]["global"] == "AR0234 / index 1"
    assert config["cameras"]["wristLeft"] == "IMX258 / index 2"
    assert config["cameras"]["wristRight"] == "IMX258 / index 0"
    assert config["cameras"]["previewResolution"] == "640x480"
    assert config["cameras"]["fps"] == 30
    assert config["storage"]["recordFps"] == 30


def test_gripper_teleop_defaults_match_omega7_gap_range() -> None:
    config = default_config()
    gripper_teleop = config["teleop"]["gripperTeleop"]

    assert gripper_teleop["leftGapMaxMm"] == 25.0
    assert gripper_teleop["rightGapMaxMm"] == 25.0
    assert gripper_teleop["positionDeadbandCounts"] == 1
    assert gripper_teleop["minCommandIntervalMs"] == 20
    assert gripper_teleop["leftSourceHand"] == "PhysicalRight"
    assert gripper_teleop["rightSourceHand"] == "PhysicalLeft"
    assert gripper_teleop["leftGapInvert"] is False
    assert gripper_teleop["rightGapInvert"] is False
    assert gripper_teleop["autoGapCalibration"] is True
    assert gripper_teleop["buttonFallback"] is True


def test_native_teleop_xy_signs_match_site_verified_stage_direction() -> None:
    teleop = default_config()["teleop"]

    assert teleop["swapTeleopChannels"] is True
    assert teleop["leftImpulseCoeff"][:2] == [-5000000, 5000000]
    assert teleop["rightImpulseCoeff"][:2] == [-5000000, -10000000]


def test_native_teleop_axis_scales_use_icf_effective_output_scale() -> None:
    teleop = default_config()["teleop"]

    assert teleop["leftTranslationScale"] == 1.0
    assert teleop["rightTranslationScale"] == 1.0
    assert teleop["leftRotationScale"] == 1.0
    assert teleop["rightRotationScale"] == 1.0
    assert teleop["leftAxisOutputScale"] == [0.40, 0.25, 0.25, 0.40, 0.20, 0.20]
    assert teleop["rightAxisOutputScale"] == [0.40, 0.25, 0.25, 0.40, 0.20, 0.20]
