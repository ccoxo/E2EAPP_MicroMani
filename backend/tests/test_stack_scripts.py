from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_start_stack_cleans_backend_process_tree_even_without_listening_port() -> None:
    script = (REPO_ROOT / "scripts" / "start-stack.ps1").read_text(encoding="utf-8")

    assert "function Stop-BackendProcessTrees" in script
    assert "backend\\.app:create_app" in script
    assert "--port\\s+$BackendPort" in script
    before_start_sleep = script.partition("Start-Sleep -Seconds 1")[0]
    assert "\nStop-BackendProcessTrees\n" in before_start_sleep


def test_start_stack_stops_backend_before_restarting_hal() -> None:
    script = (REPO_ROOT / "scripts" / "start-stack.ps1").read_text(encoding="utf-8")

    stop_call = "\nStop-BackendProcessTrees\n"
    start_hal_call = '& (Join-Path $PSScriptRoot "start-hal.ps1") -Restart'
    assert script.index(stop_call) < script.index(start_hal_call)


def test_start_hal_passes_configured_port_to_hal_process_and_health_check() -> None:
    script = (REPO_ROOT / "scripts" / "start-hal.ps1").read_text(encoding="utf-8")

    assert "[int]$Port = 8091" in script
    assert '$env:APPSTATION_HAL_PORT = "$Port"' in script
    assert '"http://127.0.0.1:$Port/health"' in script
    assert 'url = "http://127.0.0.1:$Port"' in script


def test_start_hal_skips_locked_runtime_promotion_without_failing_launch() -> None:
    script = (REPO_ROOT / "scripts" / "start-hal.ps1").read_text(encoding="utf-8")
    promote_body = script.split("function Promote-HalCandidate", 1)[1].split("$existing =", 1)[0]

    assert "try {" in promote_body
    assert "Copy-Item -LiteralPath $CandidateExe -Destination $TargetExe -Force" in promote_body
    assert "Write-Warning" in promote_body
    assert "HAL runtime promotion skipped for ${TargetExe}" in promote_body
    assert "HAL runtime promotion skipped for $TargetExe:" not in promote_body
    assert "HAL runtime promotion skipped" in promote_body
    assert "return" in promote_body.split("catch", 1)[1]


def test_start_stack_retries_hal_on_fallback_port_and_propagates_active_url() -> None:
    script = (REPO_ROOT / "scripts" / "start-stack.ps1").read_text(encoding="utf-8")

    assert "[int]$HalPort = 8091" in script
    assert "$activeHalPort = $HalPort" in script
    assert 'start-hal.ps1") -Restart -Port $activeHalPort' in script
    assert "$activeHalPort = 8092" in script
    assert 'APPSTATION_HAL_BASE_URL = "http://127.0.0.1:$activeHalPort"' in script
    assert 'hal = "http://127.0.0.1:$activeHalPort"' in script


def test_launch_app_forwards_hal_port_to_initial_start_and_restart() -> None:
    script = (REPO_ROOT / "scripts" / "launch-app.ps1").read_text(encoding="utf-8")

    assert "[int]$HalPort = 8091" in script
    assert script.count("-HalPort $HalPort") >= 2


def test_stop_stack_stops_default_and_fallback_hal_ports() -> None:
    script = (REPO_ROOT / "scripts" / "stop-stack.ps1").read_text(encoding="utf-8")

    assert "8091, 8092" in script


def test_launch_app_restarts_stack_when_backend_stops_responding_while_window_is_open() -> None:
    script = (REPO_ROOT / "scripts" / "launch-app.ps1").read_text(encoding="utf-8")

    assert "function Test-HttpOk" in script
    assert "function Get-RecordStatus" in script
    assert "$backendHealthFailures" in script
    assert "$backendHealthFailureLimit" in script
    assert "$recordStatus.active -or $recordStatus.recording" in script
    assert "Restart-AppStack" in script
    assert "http://127.0.0.1:$BackendPort/docs" in script
    assert "backend health check failed; restarting stack" in script


def test_launch_app_can_monitor_an_existing_app_window_without_opening_a_duplicate() -> None:
    script = (REPO_ROOT / "scripts" / "launch-app.ps1").read_text(encoding="utf-8")

    assert "Using existing App window" in script
    assert "if ($processes.Count -eq 0)" in script


def test_launch_app_uses_cache_busting_url_before_the_hash_fragment() -> None:
    script = (REPO_ROOT / "scripts" / "launch-app.ps1").read_text(encoding="utf-8")

    assert re.search(
        r'\$appUrl\s*=\s*"http://127\.0\.0\.1:\$FrontendPort/settings\?[^"#]+#manual"',
        script,
    )


def test_diagnose_teleop_latency_script_is_read_only_and_reports_action_history_stats() -> None:
    script = (REPO_ROOT / "scripts" / "diagnose-teleop-latency.ps1").read_text(encoding="utf-8")

    assert "param(" in script
    assert "[int]$BackendPort = 18082" in script
    assert "[int]$HalPort = 8091" in script
    assert "Invoke-RestMethod -Uri" in script
    assert "http://127.0.0.1:$BackendPort/api/settings" in script
    assert "http://127.0.0.1:$HalPort/motion/axis_diagnostics" in script
    assert "http://127.0.0.1:$HalPort/teleop/native/status" in script
    assert "Get-GapStats" in script
    assert "Get-UpdateReturnStats" in script
    assert "axisCounts" in script
    assert "sideCounts" in script
    assert "updateReturnByCode" in script
    assert "positive hard limit blocks positive PMOVE start" in script
    assert "negative hard limit blocks negative PMOVE start" in script
    assert "moveStartedAxisCounts" in script
    assert "Invoke-RestMethod -Method Post" not in script
