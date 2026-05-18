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

    assert teleop["strategyVersion"] == "e2e_omega7_icf_v19_manual_gripper_response_force_20260518"
    assert teleop["swapHands"] is False
    assert teleop["swapTeleopChannels"] is True
    assert teleop["stabilityMode"] == "off"
    assert teleop["leftTranslationScale"] == 0.30
    assert teleop["rightTranslationScale"] == 0.30
    assert teleop["leftRotationScale"] == 0.10
    assert teleop["rightRotationScale"] == 0.10
    assert teleop["leftForceFeedback"] is True
    assert teleop["rightForceFeedback"] is True
    assert teleop["leftAxisOutputScale"] == [0.60, 0.30, 0.30, 1.50, 0.30, 1.00]
    assert teleop["rightAxisOutputScale"] == [0.60, 0.30, 0.30, 1.50, 0.30, 0.30]
    assert teleop["translationStepLimitPulse"] == 4000
    assert teleop["rotationStepLimitPulse"] == 1250
    assert teleop["translationPulseDeadband"] == 2
    assert teleop["rotationPulseDeadband"] == 2
    assert teleop["translationStartVelocityUmS"] == 600.0
    assert teleop["translationMaxVelocityUmS"] == 8000.0
    assert teleop["rotationStartVelocityDegS"] == 1.0
    assert teleop["rotationMaxVelocityDegS"] == 12.0
    assert teleop["diagLog"] is False
    assert teleop["leftEnabledAxes"] == [True] * 6
    assert teleop["rightEnabledAxes"] == [True] * 6
    assert teleop["softLimitUnitSpec"] == ["um", "um", "um", "deg", "deg", "deg"]
    assert teleop["leftSoftLimitMin"] == [-25000.0, -37500.0, -37500.0, -90.0, -90.0, -7.0]
    assert teleop["leftSoftLimitMax"] == [25000.0, 37500.0, 37500.0, 90.0, 90.0, 7.0]
    assert teleop["rightSoftLimitMin"] == [-25000.0, -37500.0, -37500.0, -90.0, -90.0, -7.0]
    assert teleop["rightSoftLimitMax"] == [25000.0, 37500.0, 37500.0, 90.0, 90.0, 7.0]


def test_motion_kinematics_defaults_match_icf_mapping() -> None:
    config = default_config()
    kinematics = config["motion"]["kinematics"]

    assert kinematics["axisUnitSpec"] == ["mm", "mm", "mm", "deg", "deg", "deg"]
    assert kinematics["leftAxisMap"] == [0, 1, 3, 5, 4, 2]
    assert kinematics["rightAxisMap"] == [8, 6, 11, 14, 7, 13]
    assert kinematics["leftPhysicalAxis"] == [0, 1, 3, 5, 4, 2]
    assert kinematics["rightPhysicalAxis"] == [2, 0, 5, 8, 1, 7]
    assert kinematics["leftSignedPulsePerUnit"] == [-5000.0, 10000.0, -10000.0, 1666.666667, -2500.0, -3333.333333]
    assert kinematics["rightSignedPulsePerUnit"] == [
        -5000.0,
        -10000.0,
        -10000.0,
        1666.666667,
        2500.0,
        666.0,
    ]


def test_work_origin_defaults_match_icf_reference_position() -> None:
    config = default_config()
    origin = config["motion"]["origin"]

    assert config["motion"]["workOriginStrategyVersion"] == "icf_work_origin_20260513"
    assert origin["valid"] is True
    assert origin["leftValid"] is True
    assert origin["rightValid"] is True
    assert origin["leftPulse"] == [258510.0, -200013.0, 274821.0, 49833.0, 84839.0, 381102.0]
    assert origin["rightPulse"] == [99769.0, 382483.0, 881210.0, -35473.0, -215115.0, -5006.0]


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
    assert gripper_teleop["positionDeadbandCounts"] == 2
    assert gripper_teleop["minCommandIntervalMs"] == 50
    assert gripper_teleop["leftSourceHand"] == "PhysicalRight"
    assert gripper_teleop["rightSourceHand"] == "PhysicalLeft"
    assert gripper_teleop["leftGapInvert"] is False
    assert gripper_teleop["rightGapInvert"] is True
    assert gripper_teleop["autoGapCalibration"] is True
    assert gripper_teleop["buttonFallback"] is True
