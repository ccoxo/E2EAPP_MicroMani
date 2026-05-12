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
    assert translation["startSpeed"] == 100
    assert translation["maxSpeed"] == 1000
    assert translation["accTimeSec"] == 0.05
    assert translation["decTimeSec"] == 0.05
    assert "acceleration" not in translation
    assert "maxAcceleration" not in translation


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

    assert config["cameras"]["previewResolution"] == "640x480"
    assert config["cameras"]["fps"] == 30
    assert config["storage"]["recordFps"] == 30
