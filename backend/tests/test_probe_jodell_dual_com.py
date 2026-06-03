from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "probe_jodell_dual_com.py"


def load_probe_module():
    spec = importlib.util.spec_from_file_location("probe_jodell_dual_com", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_script_parses_com_ports_and_latency_percentiles() -> None:
    probe = load_probe_module()

    assert probe.parse_port("COM8") == 8
    assert probe.parse_port("com9") == 9
    assert probe.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert probe.percentile([1.0, 2.0, 3.0, 4.0], 95) == 3.85


def test_probe_stats_report_success_rate_and_raw_range() -> None:
    probe = load_probe_module()
    stats = probe.ReadStats("dual-open")

    stats.add(10.0, True, 245)
    stats.add(20.0, True, 244)
    stats.add(5.0, False, -1, "read failed")

    summary = stats.summary()
    assert summary["name"] == "dual-open"
    assert summary["attempts"] == 3
    assert summary["ok"] == 2
    assert summary["failures"] == 1
    assert summary["raw_min"] == 244
    assert summary["raw_max"] == 245
    assert summary["latency_ms"]["mean"] == 15.0


def test_probe_command_check_requires_explicit_unsafe_flag() -> None:
    probe = load_probe_module()

    args = probe.parse_args([])
    assert args.unsafe_command_check is False
    assert args.run_concurrent_read is False

    unsafe = probe.parse_args(["--unsafe-command-check"])
    assert unsafe.unsafe_command_check is True
