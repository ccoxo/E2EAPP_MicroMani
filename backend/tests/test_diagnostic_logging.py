from __future__ import annotations

import json
import os
from pathlib import Path

from backend.core.config import SettingsService
from backend.core.defaults import default_config
from backend.core.logging import LOG_SCHEMA_VERSION, LogService, stable_config_hash


def test_event_log_formats_stable_key_value_message() -> None:
    logs = LogService(
        clock_ms=lambda: 1_700_000_000_000,
        monotonic_ms=lambda: 12_345,
        session_id="20260520_201500",
        emit_startup=False,
    )

    entry = logs.event(
        "[HAL]",
        "INFO",
        "manual_move",
        component="MOTION",
        op_id="manual_1",
        axis="left.yaw",
        limit=[-120000, 125000],
        accepted=True,
        detail='yaw "clip"',
        missing=None,
    )

    assert entry is not None
    assert entry.msg == (
        'component=MOTION event=manual_move seq=1 monoMs=12345 '
        'session_id=20260520_201500 op_id=manual_1 axis=left.yaw '
        'limit=[-120000,125000] accepted=true detail="yaw \\"clip\\"" missing=null'
    )


def test_event_log_uses_generated_operation_id() -> None:
    logs = LogService(monotonic_ms=lambda: 99, session_id="s", emit_startup=False)

    assert logs.new_op_id("manual") == "manual_1"
    assert logs.new_op_id("manual") == "manual_2"


def test_rate_limited_event_keeps_first_and_suppresses_repeat() -> None:
    current = {"mono": 1000}
    logs = LogService(monotonic_ms=lambda: current["mono"], session_id="s", emit_startup=False)

    first = logs.event(
        "[HAL]",
        "INFO",
        "teleop_status",
        component="TELEOP",
        rate_key="teleop:left",
        rate_ms=1000,
        state="first",
    )
    second = logs.event(
        "[HAL]",
        "INFO",
        "teleop_status",
        component="TELEOP",
        rate_key="teleop:left",
        rate_ms=1000,
        state="second",
    )
    current["mono"] = 2000
    third = logs.event(
        "[HAL]",
        "INFO",
        "teleop_status",
        component="TELEOP",
        rate_key="teleop:left",
        rate_ms=1000,
        state="third",
    )

    assert first is not None
    assert second is None
    assert third is not None
    assert [entry.msg for entry in logs.list_entries()] == [
        "component=TELEOP event=teleop_status seq=1 monoMs=1000 session_id=s op_id=- state=first",
        "component=TELEOP event=teleop_status seq=2 monoMs=2000 session_id=s op_id=- state=third",
    ]


def test_log_service_persists_each_entry_to_session_file(tmp_path: Path) -> None:
    log_file = tmp_path / "appstation-m0-session.log"
    logs = LogService(
        clock_ms=lambda: 1_700_000_000_000,
        monotonic_ms=lambda: 12_345,
        session_id="20260520_201500",
        emit_startup=False,
        log_file_path=log_file,
    )

    logs.info("[BACKEND]", "first line")
    logs.event("[HAL]", "INFO", "startup_check", component="MOTION", ok=True)

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("[BACKEND] INFO first line")
    assert lines[1].endswith(
        "[HAL] INFO component=MOTION event=startup_check seq=2 monoMs=12345 "
        "session_id=20260520_201500 op_id=- ok=true"
    )


def test_log_service_prunes_old_session_files(tmp_path: Path) -> None:
    for index in range(3):
        old_log = tmp_path / f"appstation-m0-old-{index}.log"
        old_log.write_text("old", encoding="utf-8")
        old_log.touch()

    LogService(
        emit_startup=False,
        log_file_path=tmp_path / "appstation-m0-current.log",
        max_log_files=2,
    )

    remaining = sorted(path.name for path in tmp_path.glob("appstation-m0-*.log"))
    assert len(remaining) == 2
    assert "appstation-m0-current.log" in remaining


def test_log_service_prunes_old_session_files_by_total_bytes(tmp_path: Path) -> None:
    for index in range(3):
        old_log = tmp_path / f"appstation-m0-old-{index}.log"
        old_log.write_text("x" * 8, encoding="utf-8")
        os.utime(old_log, (1_700_000_000 + index, 1_700_000_000 + index))

    current_log = tmp_path / "appstation-m0-current.log"
    current_log.write_text("current", encoding="utf-8")
    os.utime(current_log, (1_700_000_100, 1_700_000_100))

    LogService(
        emit_startup=False,
        log_file_path=current_log,
        max_log_files=10,
        max_log_total_bytes=20,
    )

    remaining = sorted(path.name for path in tmp_path.glob("appstation-m0-*.log"))
    assert "appstation-m0-current.log" in remaining
    assert "appstation-m0-old-0.log" not in remaining
    assert sum(path.stat().st_size for path in tmp_path.glob("appstation-m0-*.log")) <= 20


def test_stable_config_hash_ignores_key_order() -> None:
    assert LOG_SCHEMA_VERSION == "e2e-diagnostics-v1"
    assert stable_config_hash({"b": 2, "a": [1, 2]}) == stable_config_hash({"a": [1, 2], "b": 2})


def test_settings_save_logs_config_write_hash_and_changed_keys(tmp_path: Path) -> None:
    logs = LogService(monotonic_ms=lambda: 7, session_id="s", emit_startup=False)
    settings = SettingsService(tmp_path, logs)
    config = settings.get_config()
    config["teleop"]["diagLog"] = True

    settings.save_config(config, source="ui", op_id="settings_1")

    messages = [entry.msg for entry in logs.list_entries()]
    assert any(
        "event=config_write" in message
        and "source=ui" in message
        and "op_id=settings_1" in message
        and "key=teleop.diagLog" in message
        and "old=false" in message
        and "new=true" in message
        and "oldHash=" in message
        and "newHash=" in message
        for message in messages
    )


def test_invalid_config_recovery_logs_validation_reason(tmp_path: Path) -> None:
    logs = LogService(emit_startup=False)
    invalid = default_config()
    invalid["teleop"]["translationDeadzone"] = "not-a-number"
    (tmp_path / "config.json").write_text(json.dumps(invalid), encoding="utf-8")

    config = SettingsService(tmp_path, logs).get_config()

    assert config["teleop"]["translationDeadzone"] == 0.00002
    assert any(
        entry.msg
        == (
            "config.json was invalid; default config restored: "
            "ValueError: could not convert string to float: 'not-a-number'"
        )
        for entry in logs.list_entries()
    )


def test_force_probe_logs_resource_error(monkeypatch: object) -> None:
    from backend.core.defaults import default_config
    from backend.drivers.force_nidaq import NidaqForceDriver

    def fake_import_module(name: str) -> object:
        _ = name
        raise RuntimeError("-50103 resource reserved")

    monkeypatch.setattr("backend.drivers.force_nidaq.import_module", fake_import_module)  # type: ignore[attr-defined]
    logs = LogService(emit_startup=False)
    driver = NidaqForceDriver(logs)

    result = driver.probe(default_config())

    assert result.ok is False
    message = next(entry.msg for entry in logs.list_entries() if "event=force_daq" in entry.msg)
    assert "retCode=-50103" in message
    assert "ownerHint=" in message
