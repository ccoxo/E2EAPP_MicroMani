from __future__ import annotations

from pathlib import Path

from backend.core.defaults import default_config
from backend.drivers.force_nidaq import ForceProbeResult, NidaqForceDriver


def test_force_driver_applies_reference_calibration_fallback() -> None:
    config = default_config()
    config["force"]["leftCalibrationPath"] = "Z:/missing/FT32918.cal"
    config["force"]["lowpassEnabled"] = False
    driver = NidaqForceDriver()

    left, right = driver._process_raw_windows(config, [[1, 0, 0, 0, 0, 0]], [[0, 0, 0, 0, 0, 0]])

    assert round(left[0][0], 5) == -0.00458
    assert round(left[0][2], 5) == 1.84054
    assert round(left[0][3], 8) == 0.00004046
    assert right[0] == [0.0] * 6
    assert driver.calibration_info(config)["left"]["source"] == "embedded-reference:left"


def test_force_driver_tare_bias_is_subtracted_before_calibration() -> None:
    config = default_config()
    config["force"]["calibrationEnabled"] = False
    config["force"]["lowpassEnabled"] = False
    driver = NidaqForceDriver()
    driver._tare_bias["left"] = [1, 2, 3, 4, 5, 6]

    left, _right = driver._process_raw_windows(config, [[2, 4, 6, 8, 10, 12]], [[0, 0, 0, 0, 0, 0]])

    assert left[0] == [1, 2, 3, 4, 5, 6]


def test_force_driver_loads_ati_xml_calibration_file(tmp_path: Path) -> None:
    config = default_config()
    cal_path = tmp_path / "test.cal"
    cal_path.write_text(
        """
        <Calibration>
          <UserAxis Name="Fx" values="1 0 0 0 0 0" />
          <UserAxis Name="Fy" values="0 2 0 0 0 0" />
          <UserAxis Name="Fz" values="0 0 3 0 0 0" />
          <UserAxis Name="Tx" values="0 0 0 4 0 0" />
          <UserAxis Name="Ty" values="0 0 0 0 5 0" />
          <UserAxis Name="Tz" values="0 0 0 0 0 6" />
        </Calibration>
        """,
        encoding="utf-8",
    )
    config["force"]["leftCalibrationPath"] = str(cal_path)
    config["force"]["lowpassEnabled"] = False
    driver = NidaqForceDriver()

    left, _right = driver._process_raw_windows(config, [[1, 1, 1, 1, 1, 1]], [[0, 0, 0, 0, 0, 0]])

    assert left[0] == [1, 2, 3, 0.004, 0.005, 0.006]
    assert driver.calibration_info(config)["left"]["source"].startswith("file:")


def test_force_driver_latest_window_repeats_last_scalar_without_blocking() -> None:
    config = default_config()
    config["force"]["sampleHz"] = 1000
    driver = NidaqForceDriver()
    driver._last_sample = ForceProbeResult(
        True,
        "cached",
        [1, 2, 3, 4, 5, 6],
        [6, 5, 4, 3, 2, 1],
        sample_hz=1000,
        sample_count=1,
    )

    result = driver.latest_window(config, 4)

    assert result.ok is True
    assert result.sample_count == 4
    assert result.left_window == [[1, 2, 3, 4, 5, 6]] * 4
    assert result.right_window == [[6, 5, 4, 3, 2, 1]] * 4


def test_force_driver_sample_window_failure_uses_latest_scalar_fallback(monkeypatch) -> None:
    config = default_config()
    driver = NidaqForceDriver()
    driver._last_sample = ForceProbeResult(
        True,
        "cached",
        [1, 0, 0, 0, 0, 0],
        [0, 1, 0, 0, 0, 0],
        sample_hz=200,
        sample_count=1,
    )

    monkeypatch.setattr("backend.drivers.force_nidaq.import_module", lambda _name: object())
    monkeypatch.setattr(
        driver,
        "_read_channel_window",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("daq timeout")),
    )

    result = driver.sample_window(config, 3)

    assert result.ok is True
    assert "daq timeout" in result.message
    assert result.left_window == [[1, 0, 0, 0, 0, 0]] * 3
