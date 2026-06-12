from __future__ import annotations

from copy import deepcopy
from typing import Any

DEFAULT_MOTION_PROFILE: dict[str, Any] = {
    "translation": {
        "startSpeed": 300,
        "maxSpeed": 4000,
        "accTimeSec": 0.05,
        "decTimeSec": 0.05,
    },
    "rotation": {
        "startSpeed": 0.5,
        "maxSpeed": 6,
        "accTimeSec": 0.05,
        "decTimeSec": 0.05,
    },
}

ICF_TELEOP_STRATEGY_VERSION = "e2e_omega7_native_v29_stable_feel_lead_20260612"
ICF_WORK_ORIGIN_VERSION = "icf_work_origin_20260521_rotation_limit_v2"
ICF_HOME_REFERENCE_VERSION = "icf_home_reference_20260602_v1"

ICF_CAMERA_DEFAULTS: dict[str, Any] = {
    "global": "IMX335 / index 1",
    "globalIdentity": "USB\\VID_0ABD&PID_8050&MI_00\\7&1396F44D&0&0000",
    "wristLeft": "IMX335 / index 0",
    "wristLeftIdentity": "USB\\VID_0ABD&PID_8050&MI_00\\7&398F0A3&0&0000",
    "wristRight": "IMX335 / index 2",
    "wristRightIdentity": "USB\\VID_0ABD&PID_8050&MI_00\\8&3724732E&0&0000",
    "previewResolution": "640x480",
    "globalResolution": "640x480",
    "wristLeftResolution": "640x480",
    "wristRightResolution": "640x480",
    "fps": 30,
    "tuning": {
        "global": {
            "autoExposure": False,
            "exposure": -5.5,
            "gain": 0.0,
            "autoWhiteBalance": False,
        },
        "wrist_left": {
            "autoExposure": False,
            "exposure": -6.0,
            "gain": 0.0,
            "autoWhiteBalance": False,
        },
        "wrist_right": {
            "autoExposure": False,
            "exposure": -6.0,
            "gain": 0.0,
            "autoWhiteBalance": False,
        },
    },
}

DEFAULT_SOFT_LIMITS: dict[str, Any] = {
    "x": {"min": -25000, "max": 25000},
    "y": {"min": -37500, "max": 37500},
    "z": {"min": -37500, "max": 37500},
    "roll": {"min": -90000, "max": 90000},
    "pitch": {"min": -100000, "max": 100000},
    "yaw": {"min": -7000, "max": 7000},
}

ICF_LEFT_MOTION_SOFT_LIMITS: dict[str, Any] = {
    "x": {"min": -25000, "max": 25000},
    "y": {"min": -37500, "max": 37500},
    "z": {"min": -37500, "max": 37500},
    "roll": {"min": -5000, "max": 95000},
    "pitch": {"min": -30000, "max": 30000},
    "yaw": {"min": -7000, "max": 7000},
}

ICF_RIGHT_MOTION_SOFT_LIMITS: dict[str, Any] = {
    "x": {"min": -25000, "max": 25000},
    "y": {"min": -37500, "max": 37500},
    "z": {"min": -37500, "max": 37500},
    "roll": {"min": -95000, "max": 5000},
    "pitch": {"min": -30000, "max": 30000},
    "yaw": {"min": -7000, "max": 7000},
}

ICF_KINEMATICS_DEFAULTS: dict[str, Any] = {
    "source": "QSerialTest/config.ini",
    "axisOrder": ["x", "y", "z", "roll", "pitch", "yaw"],
    "leftAxisMap": [0, 1, 3, 5, 4, 2],
    "rightAxisMap": [8, 6, 11, 14, 7, 13],
    "leftPhysicalAxis": [0, 1, 3, 5, 4, 2],
    "rightPhysicalAxis": [2, 0, 5, 8, 1, 7],
    "axisUnitSpec": ["mm", "mm", "mm", "deg", "deg", "deg"],
    "leftPulsePerUnit": [5000.0, 5000.0, 10000.0, 1666.666667, 2500.0, 3333.333],
    "rightPulsePerUnit": [5000.0, 10000.0, 5000.0, 1666.666667, 2500.0, 333.3333],
    "leftDirectionSign": [-1, 1, -1, 1, -1, -1],
    "rightDirectionSign": [-1, -1, -1, 1, 1, 1],
    "leftSignedPulsePerUnit": [-5000.0, 5000.0, -10000.0, 1666.666667, -2500.0, -3333.333],
    "rightSignedPulsePerUnit": [-5000.0, -10000.0, -5000.0, 1666.666667, 2500.0, 333.3333],
    "syncActionPulseCoeff": True,
    "updatedAt": "2026-04-17T00:00:00",
}

ICF_TELEOP_SOFT_LIMIT_UNIT_SPEC = ["um", "um", "um", "deg", "deg", "deg"]
ICF_LEFT_TELEOP_SOFT_LIMIT_MIN = [-25000.0, -37500.0, -37500.0, -5.0, -30.0, -7.0]
ICF_LEFT_TELEOP_SOFT_LIMIT_MAX = [25000.0, 37500.0, 37500.0, 95.0, 30.0, 7.0]
ICF_RIGHT_TELEOP_SOFT_LIMIT_MIN = [-25000.0, -37500.0, -37500.0, -95.0, -30.0, -7.0]
ICF_RIGHT_TELEOP_SOFT_LIMIT_MAX = [25000.0, 37500.0, 37500.0, 5.0, 30.0, 7.0]

ICF_TELEOP_DEFAULTS: dict[str, Any] = {
    "engine": "hal_native",
    "controlMode": "incremental_position",
    "nativeLoopHz": 100,
    "nativeTranslationDeadzoneM": 0.002,
    "nativeTranslationFullScaleM": 0.04,
    "nativeRotationDeadzoneDeg": 2.0,
    "nativeRotationFullScaleDeg": 30.0,
    "nativeVelocitySmoothingMs": 40.0,
    "kalmanFilterEnabled": False,
    "kalmanBeta": 0.05,
    "kalmanMinVariance": 1e-12,
    "kalmanMaxVariance": 100.0,
    "kalmanDtMinSec": 0.001,
    "kalmanDtMaxSec": 0.05,
    "kalmanTranslationPositionVariance": 1e-8,
    "kalmanTranslationVelocityVariance": 1e-4,
    "kalmanTranslationMeasurementVariance": 1e-8,
    "kalmanTranslationProcessPositionVariance": 1e-10,
    "kalmanTranslationProcessVelocityVariance": 1e-8,
    "kalmanRotationPositionVariance": 0.25,
    "kalmanRotationVelocityVariance": 4.0,
    "kalmanRotationMeasurementVariance": 0.04,
    "kalmanRotationProcessPositionVariance": 1e-4,
    "kalmanRotationProcessVelocityVariance": 1e-3,
    "kalmanTranslationIntentVelocityThreshold": 0.0005,
    "kalmanRotationIntentVelocityThreshold": 0.5,
    "strategyVersion": ICF_TELEOP_STRATEGY_VERSION,
    "mappingMode": "direct",
    "swapHands": False,
    "swapTeleopChannels": True,
    "stabilityMode": "off",
    "leftTranslationScale": 1.0,
    "rightTranslationScale": 1.0,
    "leftRotationScale": 1.0,
    "rightRotationScale": 1.0,
    "homeBeforeStart": True,
    "leftAxisOutputScale": [0.60, 0.50, 0.375, 0.60, 0.08, 0.10],
    "rightAxisOutputScale": [0.60, 0.50, 0.375, 0.60, 0.08, 0.001],
    "translationDeadzone": 0.00002,
    "rotationDeadzone": 0.03,
    "incrementalTranslationMinEffectiveDelta": 0.000025,
    "incrementalTranslationReverseDeadzone": 0.00005,
    "translationStepLimitPulse": 4000,
    "rotationStepLimitPulse": 1250,
    "translationPulseDeadband": 2,
    "rotationPulseDeadband": 2,
    "translationStepUm": 5000.0,
    "rotationStepDeg": 0.2,
    "translationStartVelocityUmS": 600.0,
    "translationMaxVelocityUmS": 8000.0,
    "rotationStartVelocityDegS": 1.0,
    "rotationMaxVelocityDegS": 12.0,
    "motionProfileAccSec": 0.05,
    "motionProfileDecSec": 0.05,
    "continuousIncrementMode": True,
    "translationInputEpsilon": 0.00002,
    "rotationInputEpsilon": 0.03,
    "translationMinActivePulse": 3,
    "rotationMinActivePulse": 3,
    "continuousMicroConfirmTicks": 0,
    "diagLog": False,
    "leftEnabledAxes": [True, True, True, True, True, True],
    "rightEnabledAxes": [True, True, True, True, True, False],
    "softLimitUnitSpec": list(ICF_TELEOP_SOFT_LIMIT_UNIT_SPEC),
    "leftSoftLimitMin": list(ICF_LEFT_TELEOP_SOFT_LIMIT_MIN),
    "leftSoftLimitMax": list(ICF_LEFT_TELEOP_SOFT_LIMIT_MAX),
    "rightSoftLimitMin": list(ICF_RIGHT_TELEOP_SOFT_LIMIT_MIN),
    "rightSoftLimitMax": list(ICF_RIGHT_TELEOP_SOFT_LIMIT_MAX),
    "leftImpulseCoeff": [-5000000, -5000000, -10000000, 1667, 2500, -333.3333],
    "rightImpulseCoeff": [-5000000, 10000000, -5000000, 1667, -2500, 3333.333],
    "leftDirectionSign": [1, -1, -1, 1, -1, -1],
    "rightDirectionSign": [1, 1, -1, 1, 1, 1],
    "syncImpulseCoeffFromKinematics": False,
}

ICF_WORK_ORIGIN_DEFAULTS: dict[str, Any] = {
    "valid": True,
    "leftValid": True,
    "rightValid": True,
    "leftPulse": [258494.0, -200013.0, 274821.0, 49833.0, 84839.0, 381102.0],
    "rightPulse": [99772.0, 382486.0, 881207.0, 19527.0, -175127.0, -9668.0],
    "updatedAt": 1779201369000,
    "previousValid": True,
    "previousLeftPulse": [258494.0, -200013.0, 274821.0, 49833.0, 84839.0, 381102.0],
    "previousRightPulse": [99771.0, 382485.0, 881208.0, 19527.0, -175123.0, -7412.0],
    "previousUpdatedAt": 1779190104000,
}

ICF_HOME_REFERENCE_DEFAULTS: dict[str, Any] = {
    "valid": True,
    "leftValid": True,
    "rightValid": True,
    "leftPulse": list(ICF_WORK_ORIGIN_DEFAULTS["leftPulse"]),
    "rightPulse": list(ICF_WORK_ORIGIN_DEFAULTS["rightPulse"]),
    "updatedAt": ICF_WORK_ORIGIN_DEFAULTS["updatedAt"],
}

ICF_WORK_ORIGIN_OFFSET_DEFAULTS: dict[str, Any] = {
    "valid": True,
    "leftValid": True,
    "rightValid": True,
    "leftPulseDelta": [0.0] * 6,
    "rightPulseDelta": [0.0] * 6,
    "updatedAt": ICF_WORK_ORIGIN_DEFAULTS["updatedAt"],
}

ICF_RELATIVE_SOFT_LIMIT_DEFAULTS: dict[str, Any] = {
    "left": deepcopy(ICF_LEFT_MOTION_SOFT_LIMITS),
    "right": deepcopy(ICF_RIGHT_MOTION_SOFT_LIMITS),
}


def _axis_limit_to_ui(limit: dict[str, Any], axis_index: int) -> dict[str, float]:
    scale = 1000.0 if axis_index >= 3 else 1.0
    return {"min": float(limit["min"]) / scale, "max": float(limit["max"]) / scale}


def _ui_limit_to_config(limit: dict[str, float], axis_index: int) -> dict[str, float]:
    scale = 1000.0 if axis_index >= 3 else 1.0
    return {"min": limit["min"] * scale, "max": limit["max"] * scale}


def _pulse_to_ui(pulse: float, signed_pulse_per_unit: float, axis_index: int) -> float:
    value = float(pulse) / float(signed_pulse_per_unit)
    return value if axis_index >= 3 else value * 1000.0


def anchored_mechanical_soft_limits(
    relative_limits: dict[str, Any],
    origin_pulse: list[float],
    signed_pulse_per_unit: list[float],
) -> dict[str, Any]:
    anchored: dict[str, Any] = {}
    for axis_index, axis_key in enumerate(("x", "y", "z", "roll", "pitch", "yaw")):
        if axis_index < 3:
            anchored[axis_key] = {
                "min": float(relative_limits[axis_key]["min"]),
                "max": float(relative_limits[axis_key]["max"]),
            }
            continue
        relative = _axis_limit_to_ui(relative_limits[axis_key], axis_index)
        origin_ui = _pulse_to_ui(origin_pulse[axis_index], signed_pulse_per_unit[axis_index], axis_index)
        anchored[axis_key] = _ui_limit_to_config(
            {
                "min": origin_ui + relative["min"],
                "max": origin_ui + relative["max"],
            },
            axis_index,
        )
    return anchored


def rotation_work_limits_from_soft_limits(left_limits: dict[str, Any], right_limits: dict[str, Any]) -> dict[str, Any]:
    def side(limits: dict[str, Any]) -> dict[str, dict[str, float]]:
        return {
            axis: _axis_limit_to_ui(limits[axis], index)
            for index, axis in ((3, "roll"), (4, "pitch"), (5, "yaw"))
        }

    return {
        "enabled": True,
        "left": side(left_limits),
        "right": side(right_limits),
    }


ICF_LEFT_MOTION_MECHANICAL_LIMITS: dict[str, Any] = anchored_mechanical_soft_limits(
    ICF_LEFT_MOTION_SOFT_LIMITS,
    ICF_WORK_ORIGIN_DEFAULTS["leftPulse"],
    ICF_KINEMATICS_DEFAULTS["leftSignedPulsePerUnit"],
)

ICF_RIGHT_MOTION_MECHANICAL_LIMITS: dict[str, Any] = anchored_mechanical_soft_limits(
    ICF_RIGHT_MOTION_SOFT_LIMITS,
    ICF_WORK_ORIGIN_DEFAULTS["rightPulse"],
    ICF_KINEMATICS_DEFAULTS["rightSignedPulsePerUnit"],
)

ICF_ROTATION_WORK_LIMIT_DEFAULTS: dict[str, Any] = rotation_work_limits_from_soft_limits(
    ICF_LEFT_MOTION_SOFT_LIMITS,
    ICF_RIGHT_MOTION_SOFT_LIMITS,
)

DEFAULT_CONFIG: dict[str, Any] = {
    "hal": {
        "baseUrl": "http://localhost:8091",
        "wsUrl": "ws://localhost:8091/ws/telemetry",
        "axisCount": 12,
        "apiConfirmed": False,
        "mode": "real",
        "timeoutMs": 5000,
        "ltdmcDllPath": "F:/E2EAPP_MicroMani/hal/vendor/leishine/bin/LTDMC.dll",
        "ltdmcLibPath": "F:/E2EAPP_MicroMani/hal/vendor/leishine/lib/x64/LTDMC.lib",
        "dhdDllPath": "F:/E2EAPP_MicroMani/hal/vendor/force_dimension/bin/dhd64.dll",
    },
    "cameras": deepcopy(ICF_CAMERA_DEFAULTS),
    "force": {
        "leftIp": "Dev5/ai0:5",
        "rightIp": "Dev3/ai0:5",
        "port": 49152,
        "sampleHz": 200,
        "recordWindowSamples": 0,
        "tareSamples": 0,
        "certificateConfirmed": False,
        "calibrationEnabled": True,
        "leftCalibrationPath": "C:/Program Files (x86)/ATI Industrial Automation/ATIDAQFT.NET/FT32918.cal",
        "rightCalibrationPath": "C:/Program Files (x86)/ATI Industrial Automation/ATIDAQFT.NET/FT38799.cal",
        "inputMode": "DIFF",
        "voltageMin": -10,
        "voltageMax": 10,
        "lowpassEnabled": True,
        "lowpassCutoffHz": 10,
        "swapHands": False,
    },
    "motion": {
        "leftCardNo": 1,
        "rightCardNo": 0,
        "motionThreadHz": 1000,
        "jogStepUm": 50,
        "jogStepDeg": 0.05,
        "yawSoftLimitDeg": 7,
        "positionSource": "dmc_get_position",
        "workOriginStrategyVersion": ICF_WORK_ORIGIN_VERSION,
        "homeReferenceVersion": ICF_HOME_REFERENCE_VERSION,
        "origin": deepcopy(ICF_WORK_ORIGIN_DEFAULTS),
        "homeReference": deepcopy(ICF_HOME_REFERENCE_DEFAULTS),
        "workOriginOffset": deepcopy(ICF_WORK_ORIGIN_OFFSET_DEFAULTS),
        "relativeSoftLimits": deepcopy(ICF_RELATIVE_SOFT_LIMIT_DEFAULTS),
        "homeOnStartup": {
            "enabled": False,
            "mode": "work_origin",
        },
        "leftProfile": deepcopy(DEFAULT_MOTION_PROFILE),
        "rightProfile": deepcopy(DEFAULT_MOTION_PROFILE),
        "leftSoftLimits": deepcopy(ICF_LEFT_MOTION_MECHANICAL_LIMITS),
        "rightSoftLimits": deepcopy(ICF_RIGHT_MOTION_MECHANICAL_LIMITS),
        "rotationWorkLimits": deepcopy(ICF_ROTATION_WORK_LIMIT_DEFAULTS),
        "kinematics": deepcopy(ICF_KINEMATICS_DEFAULTS),
    },
    "gripper": {
        "leftPort": "COM8",
        "rightPort": "COM9",
        "baudrate": 115200,
        "leftSlaveId": 10,
        "rightSlaveId": 9,
        "strokeMm": 26,
        "targetLeftMm": 13,
        "targetRightMm": 13,
        "leftEnabled": False,
        "rightEnabled": False,
        "commandForceLimitN": 8,
        "commandSpeed": 10,
        "commandTorque": 1,
        "icfTargetProtectionEnabled": True,
        "icfTargetMinGapMm": 1.02,
        "sampleMode": "dual_worker",
        "sampleHz": 30,
        "sampleStaleMs": 500,
        "sampleEnableOnNegative": True,
        "workerCommandTimeoutSec": 2.0,
        "processWorkersEnabled": True,
        "jodellWorkerExePath": "",
        "forceFeedbackAvailable": False,
        "jodellDllPath": (
            "F:/E2EAPP_MicroMani/backend/vendor/jodell/jodellTool.dll"
        ),
    },
    "safety": {
        "fxyWarnN": 2,
        "fxyStopN": 4,
        "fzWarnN": 3,
        "fzStopN": 5,
        "momentWarnNm": 0.02,
        "momentStopNm": 0.04,
        "yawSoftLimitDeg": 7,
        "watchdogMs": 50,
    },
    "zmq": {
        "observationPush": "tcp://127.0.0.1:8082",
        "actionPull": "tcp://127.0.0.1:8083",
        "timeoutMs": 50,
    },
    "storage": {
        "datasetRoot": "~/.appstation/datasets",
        "recordFps": 30,
        "videoCrf": 23,
        "pushToHub": False,
    },
    "auto": {
        "allowHardwareDispatch": False,
        "translationStepUm": 200,
        "rotationStepDeg": 0.2,
        "translationVelocityUmS": 1000,
        "rotationVelocityDegS": 0.5,
    },
    "picoVision": {
        "ip": "10.90.129.166",
        "adbPort": 5555,
        "videoPort": 12345,
        "commandPort": 13579,
        "gateway": "10.90.0.1",
        "ifIndex": 13,
        "rotation": "ccw90",
        "cameraSource": "global",
        "scriptsDir": "F:/ICFNewProject - 副本/QSerialTest3.0/QSerialTest/QSerialTest/tools/pico_mono_sender/build",
        "senderBuildDir": "F:/ICFNewProject - 副本/QSerialTest3.0/QSerialTest/QSerialTest/tools/pico_mono_sender/build",
    },
    "teleop": {
        "coarse": 1,
        "medium": 0.35,
        "fine": 0.08,
        "inputIntervalMs": 10,
        "commandIntervalMs": 10,
        "leftOpenId": 0,
        "rightOpenId": 1,
        "leftConnected": False,
        "rightConnected": False,
        "leftGravityCompensation": True,
        "rightGravityCompensation": True,
        "leftForceFeedback": True,
        "rightForceFeedback": True,
        **deepcopy(ICF_TELEOP_DEFAULTS),
        "requireClutch": False,
        "stabilityMode": "off",
        "tcpFallbackPort": 12345,
        "gripperTeleop": {
            "enabled": True,
            "loopHz": 100,
            "leftGapMinMm": 0.0,
            "leftGapMaxMm": 25.0,
            "rightGapMinMm": 0.0,
            "rightGapMaxMm": 25.0,
            "leftGapInvert": False,
            "rightGapInvert": False,
            "openThreshold": 0.30,
            "closeThreshold": 0.70,
            "gripSpeed": 255,
            "gripTorque": 1,
            "positionDeadbandCounts": 1,
            "minCommandIntervalMs": 20,
            "autoGapCalibration": True,
            "autoGapMinSpanMm": 2.0,
            "autoGapMarginMm": 1.0,
            "releaseSpeed": 255,
            "releaseTorque": 1,
            "leftSourceHand": "PhysicalRight",
            "rightSourceHand": "PhysicalLeft",
            "objectDetectMargin": 10,
            "buttonFallback": True,
            "diagLog": False,
        },
    },
    "wsl": {
        "distro": "Ubuntu-22.04",
        "condaEnv": "lerobot",
        "pythonPath": "/home/user/miniconda3/envs/lerobot/bin/python",
        "pendingWindowsValidation": True,
    },
}


def default_config() -> dict[str, Any]:
    return deepcopy(DEFAULT_CONFIG)
