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
    assert '$env:APPSTATION_HAL_DDS_ENABLED = "1"' in script
    assert '$env:APPSTATION_DDS_DOMAIN_ID = "42"' in script
    assert '"http://127.0.0.1:$Port/health"' in script
    assert 'url = "http://127.0.0.1:$Port"' in script


def test_start_hal_injects_force_runtime_config_from_backend_config() -> None:
    script = (REPO_ROOT / "scripts" / "start-hal.ps1").read_text(encoding="utf-8")

    assert "$forceRuntimeConfig" in script
    assert "APPSTATION_FORCE_CONFIG_JSON" in script
    assert 'leftPort = "COM15"' in script
    assert 'rightPort = "COM14"' in script
    assert "leftAxisSign" in script
    assert "rightAxisSign" in script
    assert "leftSignedPulsePerUnit" in script
    assert "rightSignedPulsePerUnit" in script
    assert "fxyStopN = 30.0" in script
    assert "fzStopN = 30.0" in script
    assert "momentStopNm = 1.0" in script
    for key in (
        "source",
        "protocol",
        "leftPort",
        "rightPort",
        "leftAxisSign",
        "rightAxisSign",
        "baudrate",
        "expectedSampleHz",
        "fxyWarnN",
        "fzStopN",
        "momentStopNm",
        "watchdogMs",
        "complianceEnabled",
        "leftComplianceMatrix",
        "rightComplianceMatrix",
    ):
        assert key in script


def test_start_hal_binds_hkvl_sides_to_pnp_instance_ids() -> None:
    script = (REPO_ROOT / "scripts" / "start-hal.ps1").read_text(encoding="utf-8")

    assert r'USB\VID_1A86&PID_55D3\5C7B023865' in script
    assert r'USB\VID_1A86&PID_55D3\5C7B030018' in script
    assert "Get-PnpDevice -PresentOnly -Class Ports" in script
    assert "HKVL serial binding not found" in script
    assert 'APPSTATION_HKVL_LEFT_PORT' in script
    assert 'APPSTATION_HKVL_RIGHT_PORT' in script


def test_hkvl_capture_script_is_read_only_and_validates_candidate_protocol() -> None:
    script = (REPO_ROOT / "scripts" / "capture-hkvl-force.ps1").read_text(encoding="utf-8")

    assert '[string]$LeftPort = "COM15"' in script
    assert '[string]$RightPort = "COM14"' in script
    assert "[int]$Baudrate = 1000000" in script
    assert "[double]$DurationSec = 10.0" in script
    assert "DtrEnable = $false" in script
    assert "RtsEnable = $false" in script
    assert ".Write(" not in script
    assert "CRC-16/Modbus" in script
    assert "53 54" in script
    assert "validFrames" in script


def test_start_hal_redirects_hal_stdout_to_runtime_logs() -> None:
    script = (REPO_ROOT / "scripts" / "start-hal.ps1").read_text(encoding="utf-8")

    assert '$logDir = Join-Path $repo "backend\\runtime\\logs"' in script
    assert '$halOutLog = Join-Path $logDir "hal-server.out.log"' in script
    assert '$halErrLog = Join-Path $logDir "hal-server.err.log"' in script
    assert "-RedirectStandardOutput $halOutLog" in script
    assert "-RedirectStandardError $halErrLog" in script


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


def test_start_hal_promotes_candidate_by_hash_not_timestamp() -> None:
    script = (REPO_ROOT / "scripts" / "start-hal.ps1").read_text(encoding="utf-8")
    promote_body = script.split("function Promote-HalCandidate", 1)[1].split("function Copy-RuntimeDllIfNewer", 1)[0]

    assert "Get-FileHash" in promote_body
    assert "$candidateHash.Hash -ne $targetHash.Hash" in promote_body
    assert "LastWriteTimeUtc" not in promote_body


def test_start_hal_restart_cleans_repo_hal_workers_before_runtime_promotion() -> None:
    script = (REPO_ROOT / "scripts" / "start-hal.ps1").read_text(encoding="utf-8")

    assert "function Stop-HalRuntimeProcessTrees" in script
    assert '$process.Name -eq "HalServer.exe"' in script
    assert '$process.Name -like "JodellGripperWorker*.exe"' in script
    assert r"$normalizedCommandLine = $process.CommandLine.Replace('\\', '\')" in script
    assert "$normalizedCommandLine -match $escapedHalBuild" in script
    assert "taskkill.exe" in script
    assert "/T" in script
    assert "/F" in script
    cleanup_call = script.split("if ($Restart) {", 1)[1].split("$existing =", 1)[0]
    assert "Stop-HalRuntimeProcessTrees" in cleanup_call
    assert script.index("Stop-HalRuntimeProcessTrees") < script.index("Promote-HalCandidate")


def test_start_hal_uses_runtime_worker_copy_when_worker_target_is_locked() -> None:
    script = (REPO_ROOT / "scripts" / "start-hal.ps1").read_text(encoding="utf-8")

    assert "$workerRuntimeExe" in script
    assert "JodellGripperWorker.runtime-" in script
    assert 'JodellGripperWorker.runtime-*.exe' in script
    assert "Copy-Item -LiteralPath $CandidateExe -Destination $workerRuntimeExe -Force" in script
    assert '$env:APPSTATION_JODELL_WORKER_EXE = "$workerRuntimeExe"' in script


def test_start_hal_copies_fastdds_runtime_dlls_beside_hal_exe() -> None:
    script = (REPO_ROOT / "scripts" / "start-hal.ps1").read_text(encoding="utf-8")

    assert "F:\\opt\\ros\\jazzy\\bin" in script
    assert "F:\\opt\\ros\\jazzy\\.pixi\\envs\\default\\Library\\bin" in script
    assert "function Copy-RuntimeDllIfNewer" in script
    assert "fastrtps-2.14.dll" in script
    assert "fastcdr-2.2.dll" in script
    assert "tinyxml2.dll" in script
    assert "libssl-3-x64.dll" in script
    assert "libcrypto-3-x64.dll" in script
    assert "F:\\opt\\ros\\jazzy\\bin;F:\\opt\\ros\\jazzy\\.pixi\\envs\\default\\Library\\bin;$env:PATH" in script


def test_start_stack_retries_hal_on_fallback_port_and_propagates_active_url() -> None:
    script = (REPO_ROOT / "scripts" / "start-stack.ps1").read_text(encoding="utf-8")

    assert "[int]$HalPort = 8091" in script
    assert "$activeHalPort = $HalPort" in script
    assert 'start-hal.ps1") -Restart -Port $activeHalPort' in script
    assert "$activeHalPort = 8092" in script
    assert 'APPSTATION_HAL_TRANSPORT = "dds"' in script
    assert 'APPSTATION_DDS_DOMAIN_ID = "42"' in script
    assert 'APPSTATION_HAL_BASE_URL = "http://127.0.0.1:$activeHalPort"' in script
    assert 'hal = "http://127.0.0.1:$activeHalPort"' in script


def test_start_stack_preserves_existing_dds_domain_for_backend_after_hal_start() -> None:
    script = (REPO_ROOT / "scripts" / "start-stack.ps1").read_text(encoding="utf-8")
    after_hal_start = script.split('start-hal.ps1") -Restart', 1)[1]

    assert 'if (-not $env:APPSTATION_DDS_DOMAIN_ID) { $env:APPSTATION_DDS_DOMAIN_ID = "42" }' in after_hal_start
    assert '$env:APPSTATION_DDS_DOMAIN_ID = "42"' not in after_hal_start.replace(
        'if (-not $env:APPSTATION_DDS_DOMAIN_ID) { $env:APPSTATION_DDS_DOMAIN_ID = "42" }',
        "",
    )


def test_start_stack_preserves_existing_dds_lan_discovery_for_backend_after_hal_start() -> None:
    script = (REPO_ROOT / "scripts" / "start-stack.ps1").read_text(encoding="utf-8")
    after_hal_start = script.split('start-hal.ps1") -Restart', 1)[1]

    assert (
        'if (-not $env:APPSTATION_DDS_LAN_DISCOVERY) { $env:APPSTATION_DDS_LAN_DISCOVERY = "0" }'
        in after_hal_start
    )
    assert '$env:APPSTATION_DDS_LAN_DISCOVERY = "0"' not in after_hal_start.replace(
        'if (-not $env:APPSTATION_DDS_LAN_DISCOVERY) { $env:APPSTATION_DDS_LAN_DISCOVERY = "0" }',
        "",
    )


def test_launch_app_forwards_hal_port_to_initial_start_and_restart() -> None:
    script = (REPO_ROOT / "scripts" / "launch-app.ps1").read_text(encoding="utf-8")
    start_app_stack = script.split("function Start-AppStack", 1)[1].split("function Get-ProcessSummary", 1)[0]

    assert "[int]$HalPort = 8091" in script
    assert "-HalPort $HalPort" in start_app_stack
    assert script.count("Start-AppStack") >= 3


def test_stop_stack_stops_default_and_fallback_hal_ports() -> None:
    script = (REPO_ROOT / "scripts" / "stop-stack.ps1").read_text(encoding="utf-8")

    assert "8091, 8092" in script


def test_stop_stack_cleans_orphaned_hal_workers_from_repo_build() -> None:
    script = (REPO_ROOT / "scripts" / "stop-stack.ps1").read_text(encoding="utf-8")

    assert "function Stop-HalRuntimeProcessTrees" in script
    assert '$process.Name -eq "HalServer.exe"' in script
    assert '$process.Name -like "JodellGripperWorker*.exe"' in script
    assert r"$normalizedCommandLine = $process.CommandLine.Replace('\\', '\')" in script
    assert "$normalizedCommandLine -match $escapedHalBuild" in script
    assert "taskkill.exe" in script
    assert "/T" in script
    assert "/F" in script
    assert script.rindex("Stop-HalRuntimeProcessTrees") > script.index("foreach ($port")


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


def test_launch_app_waits_long_enough_for_cold_frontend_build() -> None:
    script = (REPO_ROOT / "scripts" / "launch-app.ps1").read_text(encoding="utf-8")

    assert "Wait-HttpOk $appUrl 180" in script
    assert "Wait-HttpOk $appUrl 45" not in script


def test_launch_app_timeout_reports_stack_diagnostics() -> None:
    script = (REPO_ROOT / "scripts" / "launch-app.ps1").read_text(encoding="utf-8")

    assert "function Get-LaunchDiagnostics" in script
    assert "Timed out waiting for $Url.`n$(Get-LaunchDiagnostics)" in script
    assert "frontendLauncherPid" in script
    assert "frontendOutLog" in script
    assert "frontendErrLog" in script


def test_start_stack_launches_frontend_on_requested_port_without_duplicate_port_args() -> None:
    script = (REPO_ROOT / "scripts" / "start-stack.ps1").read_text(encoding="utf-8")

    assert "node scripts/serve-dist.mjs" in script
    assert "--host 127.0.0.1 --port $FrontendPort" in script
    assert "npm run dev -- --host 127.0.0.1 --port $FrontendPort" not in script
    assert "--port 5173 --host 127.0.0.1 --port $FrontendPort" not in script


def test_start_dds_stack_enables_hal_direct_dds_without_python_sidecar() -> None:
    script = (REPO_ROOT / "scripts" / "start-stack-dds.ps1").read_text(encoding="utf-8")

    assert "[int]$DomainId = 42" in script
    assert 'APPSTATION_HAL_DDS_ENABLED = "1"' in script
    assert 'APPSTATION_HAL_TRANSPORT = "dds"' in script
    assert 'APPSTATION_DDS_DOMAIN_ID = "$DomainId"' in script
    assert 'APPSTATION_DDS_LAN_DISCOVERY = if ($LanDiscovery) { "1" } else { "0" }' in script
    assert "backend.hal_client." + "dds_" + "bridge_runner" not in script
    assert "ddsBridgePid" not in script
    assert "backend.app:create_app" in script


def test_start_stack_captures_frontend_launcher_logs_for_launch_diagnostics() -> None:
    script = (REPO_ROOT / "scripts" / "start-stack.ps1").read_text(encoding="utf-8")

    assert "$frontendOutLog" in script
    assert "$frontendErrLog" in script
    assert "-RedirectStandardOutput $frontendOutLog" in script
    assert "-RedirectStandardError $frontendErrLog" in script
    assert "frontendOutLog = $frontendOutLog" in script
    assert "frontendErrLog = $frontendErrLog" in script


def test_diagnose_teleop_latency_script_no_longer_uses_direct_hal_http_diagnostics() -> None:
    script = (REPO_ROOT / "scripts" / "diagnose-teleop-latency.ps1").read_text(encoding="utf-8")

    assert "param(" in script
    assert "[int]$BackendPort = 18082" in script
    assert "[int]$HalPort = 8091" in script
    assert "HAL HTTP only supports /health" in script
    assert "backend DDS telemetry" in script
    assert "Invoke-RestMethod" not in script
    assert "/motion/axis_diagnostics" not in script
    assert "/teleop/native/status" not in script


def test_run_act_jepa_deploy_defaults_to_f_drive_checkpoint() -> None:
    script = (REPO_ROOT / "scripts" / "run-act-jepa-deploy.ps1").read_text(encoding="utf-8")

    assert r'F:\model\grab_screw\act_jepa_tarimg_same_v2\050000' in script
    assert '[string]$Checkpoint = "pretrained_model"' in script
    assert 'act_deploy.py not found in checkpoint directory' in script


def test_run_act_jepa_deploy_stops_backend_by_port_without_wmi_scan() -> None:
    script = (REPO_ROOT / "scripts" / "run-act-jepa-deploy.ps1").read_text(encoding="utf-8")

    assert "Get-NetTCPConnection -LocalPort $BackendPort" in script
    assert "Get-CimInstance Win32_Process | Where-Object" not in script


def test_run_act_jepa_deploy_invokes_start_hal_directly() -> None:
    script = (REPO_ROOT / "scripts" / "run-act-jepa-deploy.ps1").read_text(encoding="utf-8")

    assert '& (Join-Path $repo "scripts\\start-hal.ps1") -Restart' in script
    assert 'powershell -ExecutionPolicy Bypass -File (Join-Path $repo "scripts\\start-hal.ps1")' not in script


def test_run_act_jepa_deploy_logs_inference_output_to_desktop_by_default() -> None:
    script = (REPO_ROOT / "scripts" / "run-act-jepa-deploy.ps1").read_text(encoding="utf-8")

    assert 'ACT-JEPA推理日志.txt' in script
    assert "[switch]$NoInferenceLog" in script
    assert "Tee-Object -FilePath $InferenceLogPath -Append" in script


def test_run_act_jepa_deploy_freezes_uncontrolled_state_by_default() -> None:
    script = (REPO_ROOT / "scripts" / "run-act-jepa-deploy.ps1").read_text(encoding="utf-8")

    assert "[object]$FreezeUncontrolledState = $true" in script
    assert '$actArgs += "--freeze_uncontrolled_state"' in script
    assert "FreezeUncontrolledState: $FreezeUncontrolledState" in script
    assert "[object]$ClipModelState = $true" in script
    assert '$actArgs += "--clip_model_state"' in script
    assert "[object]$ClipActionToStats = $true" in script
    assert '$actArgs += "--clip_action_to_stats"' in script
    assert '[string]$TranslationAxes = "x,y,z"' in script
    assert '"--translation_axes", "$TranslationAxes"' in script
    assert "[double]$TranslationDeadbandUm = 0.0" in script
    assert '"--translation_deadband_um", "$TranslationDeadbandUm"' in script
