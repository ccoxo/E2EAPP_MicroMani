from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from backend.core.defaults import default_config
from backend.core.logging import LogService
from backend.core.motion_safety import MotionSafetyGate
from backend.core.schemas import ManualAxisMoveRequest
from backend.hal_client.client import TestHalClient as LeaseHal
from backend.services.policy_service import PolicyService
from backend.services.control_watchdog import ControlLeaseUnavailable
from backend.tests.test_control_watchdog import make_watchdog, confirm_mock_browser_lease


def test_expired_open_socket_does_not_block_new_owner_or_revoke_it_later():
    async def exercise():
        watchdog, now, hal, invalidate, _stop = make_watchdog()
        old = await confirm_mock_browser_lease(watchdog)
        watchdog.trip("HAL control lease renewal failed")
        assert not watchdog.clients
        new = await confirm_mock_browser_lease(watchdog)
        watchdog.remove(old)
        assert old not in watchdog.clients
        assert new in watchdog.clients
        watchdog.require_ready()
        assert invalidate.call_count == 1
        assert hal.command.await_count == 2
        await watchdog.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -1])
def test_nonfinite_or_negative_steps_rejected_at_both_inputs(value):
    with pytest.raises(ValidationError):
        ManualAxisMoveRequest(side="left", axis="X", direction=1, step=value, speedMode="fine")
    policy = PolicyService(SimpleNamespace(get_config=default_config), SimpleNamespace(), LogService(emit_startup=False))
    with pytest.raises(RuntimeError):
        policy._validated_action({"step": value}, default_config())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -1, 0])
def test_invalid_policy_velocity_rejected(value):
    policy = PolicyService(SimpleNamespace(get_config=default_config), SimpleNamespace(), LogService(emit_startup=False))
    with pytest.raises(RuntimeError):
        policy._validated_action({"step": 1, "maxVelocityUiPerSec": value}, default_config())


def test_resource_conflict_rejected_but_stop_remains_available():
    async def exercise():
        gate = MotionSafetyGate()
        async def competing():
            with pytest.raises(RuntimeError, match="already in progress"):
                with gate.operation():
                    pytest.fail("conflicting operation entered")
            gate.interrupt(emergency=True)
        with gate.operation("left"):
            with gate.operation("left"):
                await asyncio.create_task(competing())
        assert gate.latched
        with gate.operation():
            pass
    asyncio.run(exercise())


def test_test_hal_requires_lease_and_explicit_ack_after_expiry():
    async def exercise():
        now = [0.0]
        hal = LeaseHal(LogService(emit_startup=False), clock=lambda: now[0])
        with pytest.raises(RuntimeError, match="lease"):
            await hal.command("motion.enable_side", {"side": "left"})
        async def renew(sequence):
            await hal.command("control.lease", {"sessionId": "test", "sequence": sequence, "timeoutMs": 2500})
        await renew(1)
        with pytest.raises(RuntimeError, match="emergency"):
            await hal.command("motion.enable_side", {"side": "left"})
        await hal.command("motion.acknowledge_estop")
        await hal.command("motion.enable_side", {"side": "left"})
        now[0] = 3
        await renew(2)
        assert (await hal.motion_state())["estop_active"]
        assert not any((await hal.motion_state())["enabled"])
        with pytest.raises(RuntimeError, match="replay"):
            await renew(2)
        await hal.command("motion.disable_side", {"side": "left"})
    asyncio.run(exercise())


def test_http_commands_require_owner_even_with_other_browser_lease(tmp_path, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from backend.app import create_app
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    app = create_app(tmp_path)
    app.state.telemetry.hardware = None
    async def exercise():
        watchdog = app.state.control_watchdog
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
                for path in ("/api/motion/left/enable_all", "/api/motion/home_all", "/api/auto/start"):
                    assert (await http.post(path)).status_code == 409
                candidate = (await http.get("/api/settings")).json()
                candidate["motion"]["leftProfile"]["translation"]["maxSpeed"] += 1
                assert (await http.put("/api/settings", json=candidate)).status_code == 409
                session = await confirm_mock_browser_lease(watchdog)
                assert (await http.post("/api/motion/safety/acknowledge")).status_code == 409
                http.headers["X-Control-Session"] = session
                assert (await http.post("/api/motion/safety/acknowledge")).status_code == 200
                assert (await http.put("/api/settings", json=candidate)).status_code == 200
                # 模拟设备处于配置中的合法工作原点，不能用全零姿态绕过现场旋转限位。
                origin = candidate["motion"]["origin"]
                app.state.hal._pulses = list(origin["leftPulse"]) + list(origin["rightPulse"])
                captured = await http.post("/api/motion/left/origin/capture", json={"confirmLargeDrift": True})
                assert captured.status_code == 200, captured.text
                assert (await http.post("/api/motion/left/enable_all")).status_code == 200
                assert (await http.post("/api/motion/left/return_origin")).status_code == 200
                http.headers["X-Control-Session"] = "observer"
                assert (await http.post("/api/motion/left/enable_all")).status_code == 409
                assert (await http.post("/api/runtime/release_handles", json={"controlSessionId": "observer"})).json()["data"]["released"] is False
                assert (await http.post("/api/motion/emergency_stop")).status_code == 200
        finally:
            await watchdog.close()
            app.state.telemetry.shutdown()
    asyncio.run(exercise())


def test_policy_revalidates_after_queue_and_reports_post_dispatch_stop():
    async def exercise():
        config = default_config()
        config["auto"]["allowHardwareDispatch"] = True
        policy = PolicyService(SimpleNamespace(get_config=lambda: config), SimpleNamespace(command=AsyncMock()), LogService(emit_startup=False))
        policy.validate_hardware_action = AsyncMock()
        await policy.auto_start()
        await policy.queue_action({"step": 1, "maxVelocityUiPerSec": 1})
        async def command(*_args):
            policy.invalidate_pending_actions()
            return {"ok": True}
        policy.hal.command = command
        result = await policy.dispatch_next()
        assert result["dispatched"] is True and result["outcome"] == "interrupted"
        policy.validate_hardware_action.assert_awaited_once()
    asyncio.run(exercise())


def test_record_interruption_keeps_queued_jobs_and_never_clears_episode():
    from backend.services.dataset_recorder import DatasetRecorderService
    async def exercise():
        recorder = object.__new__(DatasetRecorderService)
        recorder._session_active = recorder._recording = recorder._accepting_frame_jobs = True
        recorder._episode_index = 1
        recorder._episode_generation = 2
        recorder._queued_episode_frames = 3
        recorder.telemetry = SimpleNamespace(recording=True)
        recorder.logs = LogService(emit_startup=False)
        recorder.teleop = SimpleNamespace(stop=AsyncMock())
        release = asyncio.Event()
        recorder._drain_recording_queues = release.wait
        recorder._clear_native_episode_buffer = AsyncMock()
        recorder.interrupt_for_safety()
        assert not recorder._accepting_frame_jobs and not recorder.telemetry.recording
        job = SimpleNamespace(episode_index=1, episode_generation=2, frame_index=3)
        assert not recorder._is_current_frame_job_locked(job)
        assert recorder._frame_job_belongs_to_current_episode_locked(job)
        recorder.interrupt_for_safety()
        release.set()
        await recorder._interruption_task
        assert not recorder._recording
        assert recorder._session_active
        recorder.teleop.stop.assert_awaited_once_with("recording")
        recorder._clear_native_episode_buffer.assert_not_called()
    asyncio.run(exercise())


def test_recovered_lease_does_not_allow_ack_after_stop_failure():
    async def exercise():
        watchdog, _now, _hal, _invalidate, stop = make_watchdog()
        await confirm_mock_browser_lease(watchdog)
        stop.side_effect = RuntimeError("stop not confirmed")
        watchdog.trip("connection lost")
        await confirm_mock_browser_lease(watchdog)
        watchdog.require_ready()
        with pytest.raises(ControlLeaseUnavailable, match="stop is still pending"):
            watchdog.require_ack_ready()
        await watchdog.close()
    asyncio.run(exercise())


def test_cleanup_failure_cannot_prevent_hardware_emergency():
    from backend.tests.test_control_stop_races import make_commands
    async def exercise():
        commands = make_commands()
        def broken():
            raise RuntimeError("recorder failure")
        commands.safety.on_emergency = broken
        await commands.emergency_stop()
        commands.hal.command.assert_awaited_once_with("motion.emergency_stop")
        assert commands.safety.latched
    asyncio.run(exercise())


def test_auto_stop_during_final_validation_prevents_dispatch():
    async def exercise():
        config = default_config()
        config["auto"]["allowHardwareDispatch"] = True
        policy = PolicyService(SimpleNamespace(get_config=lambda: config), SimpleNamespace(command=AsyncMock()), LogService(emit_startup=False))
        async def validate(_action):
            await policy.auto_stop()
        policy.validate_hardware_action = validate
        await policy.auto_start()
        await policy.queue_action({"step": 1, "maxVelocityUiPerSec": 1})
        with pytest.raises(RuntimeError, match="newer stop"):
            await policy.dispatch_next()
        policy.hal.command.assert_not_called()
    asyncio.run(exercise())


def test_origin_confirmation_rejects_stale_or_moving_feedback(monkeypatch):
    from backend.services.command_service import CommandService
    async def exercise():
        command = object.__new__(CommandService)
        command.safety = MotionSafetyGate()
        state = {"timestamp_ms": 0, "pulses": [0.0] * 12, "moving": [True] * 12}
        command.hal = SimpleNamespace(motion_state=AsyncMock(return_value=state))
        times = iter([0.0, 3.0])
        monkeypatch.setattr("backend.services.command_service.time", SimpleNamespace(monotonic=lambda: next(times)))
        with pytest.raises(RuntimeError, match="unconfirmed"):
            await command._confirm_work_origin({"left": [0.0] * 6}, 1, command.safety.capture())
    asyncio.run(exercise())
