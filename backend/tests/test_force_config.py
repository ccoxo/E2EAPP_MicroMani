# 阅读导航 07｜测试与验证
# 职责：回归验证：HKVL 端口绑定、方向校准、阈值和柔顺参数校验。
# 先看：hkvl_config → test_hkvl_force_config_payload_matches_hal_flat_contract → test_hkvl_force_config_payload_uses_pnp_bound_runtime_ports → test_hkvl_force_config_payload_rejects_duplicate_pnp_bound_ports。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from backend.core.config import SettingsService
from backend.core.defaults import default_config
from backend.core.force_config import hal_force_config_payload, validate_force_config
from backend.core.logging import LogService


@pytest.mark.parametrize("source", ["hkvl_serial", "nidaq"])
def test_force_source_selection_survives_settings_reload(tmp_path: Path, source: str) -> None:
    settings = SettingsService(tmp_path, LogService(emit_startup=False))
    config = settings.get_config()
    assert config["force"]["source"] == "hkvl_serial"
    config["force"]["source"] = source
    config["force"]["leftIp"] = "DevBackup/ai0:5"

    settings.save_config(config, emit_log=False)
    restored = SettingsService(tmp_path, LogService(emit_startup=False)).get_config()

    assert restored["force"]["source"] == source
    assert restored["force"]["leftIp"] == "DevBackup/ai0:5"
    assert hal_force_config_payload(restored)["source"] == source


def test_legacy_config_without_force_source_defaults_to_hkvl(tmp_path: Path) -> None:
    config = default_config()
    config["force"].pop("source")
    config["force"]["leftIp"] = "DevBackup/ai0:5"
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")

    restored = SettingsService(tmp_path, LogService(emit_startup=False)).get_config()

    assert restored["force"]["source"] == "hkvl_serial"
    assert restored["force"]["leftIp"] == "DevBackup/ai0:5"


def hkvl_config() -> dict:
    config = default_config()
    config["force"]["source"] = "hkvl_serial"
    return config


def test_hkvl_force_config_payload_matches_hal_flat_contract() -> None:
    config = hkvl_config()
    config["force"]["compliance"]["left"].update(
        {
            "mappingConfirmed": True,
            "matrix": [0.0, 1.0, -1.0, 0.0],
            "deadbandN": [0.2, 0.3],
            "gainUmPerNs": [12.0, 13.0],
            "maxStepUm": [4.0, 5.0],
            "maxOffsetUm": [40.0, 50.0],
        }
    )

    payload = hal_force_config_payload(config)

    assert payload["source"] == "hkvl_serial"
    assert payload["protocol"] == "hkvl_active_v1"
    assert payload["leftPort"] == "COM15"
    assert payload["rightPort"] == "COM14"
    assert payload["leftAxisSign"] == [1.0, 1.0, -1.0, -1.0, -1.0, 1.0]
    assert payload["rightAxisSign"] == [1.0, -1.0, 1.0, -1.0, 1.0, -1.0]
    assert payload["baudrate"] == 1_000_000
    assert payload["expectedSampleHz"] == 1000
    assert payload["fxyWarnN"] == 2
    assert payload["fxyStopN"] == 30
    assert payload["fzStopN"] == 30
    assert payload["momentStopNm"] == 1
    assert payload["watchdogMs"] == 50
    assert payload["acknowledgeStableMs"] == 500
    assert payload["leftMappingConfirmed"] is True
    assert payload["leftComplianceMatrix"] == [0.0, 1.0, -1.0, 0.0]
    assert payload["leftComplianceGainUmPerNs"] == [12.0, 13.0]


def test_hkvl_force_config_payload_uses_pnp_bound_runtime_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HKVL_LEFT_PORT", "COM31")
    monkeypatch.setenv("APPSTATION_HKVL_RIGHT_PORT", "COM32")

    payload = hal_force_config_payload(hkvl_config())

    assert payload["leftPort"] == "COM31"
    assert payload["rightPort"] == "COM32"


def test_hkvl_force_config_payload_rejects_duplicate_pnp_bound_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPSTATION_HKVL_LEFT_PORT", "COM31")
    monkeypatch.setenv("APPSTATION_HKVL_RIGHT_PORT", "com31")

    with pytest.raises(ValueError, match="runtime ports must be present and different"):
        hal_force_config_payload(hkvl_config())


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("force", "source"), "serial", "force.source"),
        (("force", "serial", "protocol"), "modbus", "protocol"),
        (("force", "serial", "baudrate"), 115_200, "1 Mbps"),
        (("force", "serial", "rightPort"), "COM15", "different"),
        (("safety", "fxyWarnN"), 30.0, "fxy"),
        (("safety", "fzStopN"), 31.0, "fz"),
        (("safety", "momentStopNm"), 1.1, "moment"),
    ],
)
def test_invalid_hkvl_force_configuration_is_rejected(
    path: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    config = hkvl_config()
    target = config
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValueError, match=message):
        validate_force_config(config)


def test_compliance_cannot_be_enabled_until_both_mappings_are_confirmed() -> None:
    config = hkvl_config()
    config["force"]["compliance"]["enabled"] = True
    config["force"]["compliance"]["left"]["mappingConfirmed"] = True

    with pytest.raises(ValueError, match="mappingConfirmed"):
        validate_force_config(config)


def test_compliance_arrays_have_fixed_sizes_and_nonnegative_limits() -> None:
    too_short = hkvl_config()
    too_short["force"]["compliance"]["left"]["matrix"] = [1.0, 0.0]
    with pytest.raises(ValueError, match="matrix"):
        validate_force_config(too_short)

    negative = deepcopy(hkvl_config())
    negative["force"]["compliance"]["right"]["maxStepUm"] = [1.0, -1.0]
    with pytest.raises(ValueError, match="maxStepUm"):
        validate_force_config(negative)


def test_hkvl_force_axis_direction_uses_the_dedicated_calibration() -> None:
    config = hkvl_config()
    config["motion"]["kinematics"]["leftSignedPulsePerUnit"] = [1.0] * 6

    assert hal_force_config_payload(config)["leftAxisSign"] == [1.0, 1.0, -1.0, -1.0, -1.0, 1.0]


@pytest.mark.parametrize("value", ([1.0, 0.0, -1.0, 1.0, -1.0, 1.0], [1.0] * 5))
def test_hkvl_force_axis_direction_requires_six_unit_signs(value: list[float]) -> None:
    config = hkvl_config()
    config["force"]["axisSign"]["left"] = value

    with pytest.raises(ValueError, match="force.axisSign.left"):
        validate_force_config(config)
