from __future__ import annotations

import configparser

from backend.core.defaults import (
    ICF_LEFT_MOTION_MECHANICAL_LIMITS,
    ICF_RIGHT_MOTION_MECHANICAL_LIMITS,
    default_config,
)
from backend.core.motion_limits import effective_limits_ui
from scripts.import_icf_teleop_config import apply_gripper_config, apply_icf_config


def _minimal_icf_ini() -> configparser.ConfigParser:
    ini = configparser.ConfigParser()
    ini.optionxform = str  # type: ignore[method-assign, assignment]
    ini.read_dict(
        {
            "Teleop": {
                "LocalSwapTeleopChannels": "true",
            },
            "Kinematics": {
                "LeftPulsePerUnit": "5000,10000,10000,1666.666667,2500,3333.333333",
                "RightPulsePerUnit": "5000,10000,10000,1666.666667,2500,3333.333333",
            },
            "WorkOrigin": {
                "Valid": "true",
                "LeftOriginPulse": "258510,-200013,274821,49833,84839,381102",
                "RightOriginPulse": "99769,382483,881210,-35473,-215115,-5006",
            },
            "Safety": {
                "TeleopLeftEnabledAxes": "1,1,1,1,1,1",
                "TeleopRightEnabledAxes": "1,1,1,1,1,1",
                "TeleopLeftSoftLimitMin": "-200000000,-200000000,-200000000,-100167,-140161,357769",
                "TeleopLeftSoftLimitMax": "200000000,200000000,200000000,199833,309839,404435",
                "TeleopRightSoftLimitMin": "-200000000,-200000000,-200000000,-185473,-440115,-9668",
                "TeleopRightSoftLimitMax": "200000000,200000000,200000000,114527,9885,-344",
            },
        }
    )
    return ini


def test_import_icf_gripper_sources_are_normalized_to_operator_view() -> None:
    ini = configparser.ConfigParser()
    ini.optionxform = str  # type: ignore[method-assign, assignment]
    ini.read_dict(
        {
            "JodellGripper": {
                "Enable": "1",
                "Port": "8",
                "SlaveId": "10",
                "SourceHand": "PhysicalRight",
            },
            "JodellGripperLeft": {
                "Enable": "1",
                "Port": "9",
                "SlaveId": "9",
                "SourceHand": "PhysicalLeft",
            },
        }
    )
    config = default_config()

    apply_gripper_config(config, ini)

    assert config["gripper"]["leftPort"] == "COM8"
    assert config["gripper"]["rightPort"] == "COM9"
    assert config["teleop"]["gripperTeleop"]["leftSourceHand"] == "PhysicalLeft"
    assert config["teleop"]["gripperTeleop"]["rightSourceHand"] == "PhysicalRight"


def test_import_icf_config_preserves_card0_yaw_safety_policy() -> None:
    config = default_config()

    apply_icf_config(config, _minimal_icf_ini(), source_path=__file__)

    assert config["teleop"]["leftEnabledAxes"] == [True, True, True, True, True, True]
    assert config["teleop"]["rightEnabledAxes"] == [True, True, True, True, True, False]


def test_import_icf_config_decouples_legacy_rotation_window_from_mechanical_limits() -> None:
    config = default_config()

    apply_icf_config(config, _minimal_icf_ini(), source_path=__file__)

    assert config["motion"]["homeReference"]["leftPulse"] == config["motion"]["origin"]["leftPulse"]
    assert config["motion"]["homeReference"]["rightPulse"] == config["motion"]["origin"]["rightPulse"]
    assert config["motion"]["leftSoftLimits"] == ICF_LEFT_MOTION_MECHANICAL_LIMITS
    assert config["motion"]["rightSoftLimits"] == ICF_RIGHT_MOTION_MECHANICAL_LIMITS
    assert config["motion"]["rotationWorkLimits"]["enabled"] is True
    assert config["motion"]["rotationWorkLimits"]["left"]["yaw"] == {"min": -7.0, "max": 7.0}
    assert config["motion"]["rotationWorkLimits"]["right"]["yaw"] == {"min": -7.0, "max": 7.0}

    for side in ("left", "right"):
        limits = effective_limits_ui(config, side)
        for limit in limits[3:]:
            assert limit.min <= limit.max
