from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_start_stack_cleans_backend_process_tree_even_without_listening_port() -> None:
    script = (REPO_ROOT / "scripts" / "start-stack.ps1").read_text(encoding="utf-8")

    assert "function Stop-BackendProcessTrees" in script
    assert "backend\\.app:create_app" in script
    assert "--port\\s+$BackendPort" in script
    before_start_sleep = script.partition("Start-Sleep -Seconds 1")[0]
    assert "\nStop-BackendProcessTrees\n" in before_start_sleep


def test_launch_app_restarts_stack_when_backend_stops_responding_while_window_is_open() -> None:
    script = (REPO_ROOT / "scripts" / "launch-app.ps1").read_text(encoding="utf-8")

    assert "function Test-HttpOk" in script
    assert "Restart-AppStack" in script
    assert "http://127.0.0.1:$BackendPort/docs" in script
    assert "backend health check failed; restarting stack" in script


def test_launch_app_can_monitor_an_existing_app_window_without_opening_a_duplicate() -> None:
    script = (REPO_ROOT / "scripts" / "launch-app.ps1").read_text(encoding="utf-8")

    assert "Using existing App window" in script
    assert "if ($processes.Count -eq 0)" in script
