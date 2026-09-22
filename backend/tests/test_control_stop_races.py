from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core.defaults import default_config
from backend.core.data_contract import data_contract_metadata
from backend.core.logging import LogService
from backend.core.motion_safety import MotionSafetyGate
from backend.core.schemas import ManualAxisMoveRequest
from backend.services.command_service import CommandService
from backend.services.dataset_recorder import DatasetRecorderService
from backend.services.policy_service import PolicyService
from backend.services.teleop_mapping import TeleopMappingService


def test_policy_stop_invalidates_action_already_removed_from_queue() -> None:
    async def exercise() -> None:
        config = default_config()
        config['auto']['allowHardwareDispatch'] = True
        policy = PolicyService(
            SimpleNamespace(get_config=lambda: config),
            SimpleNamespace(command=AsyncMock()),
            LogService(emit_startup=False),
        )
        await policy.auto_start()
        await policy.queue_action({'side': 'left', 'axis': 'X', 'step': 5, 'maxVelocityUiPerSec': 10})
        preparing = asyncio.Event()
        release = asyncio.Event()
        original_config = policy._get_config_async

        async def delayed_config():
            preparing.set()
            await release.wait()
            return config

        policy._get_config_async = delayed_config
        dispatch = asyncio.create_task(policy.dispatch_next())
        await asyncio.wait_for(preparing.wait(), 2)
        policy._get_config_async = original_config
        await policy.auto_stop()
        await policy.auto_start()
        release.set()
        result = await dispatch
        assert result['dispatched'] is False
        policy.hal.command.assert_not_called()

    asyncio.run(exercise())


def test_policy_rejects_actions_queued_during_emergency_before_later_restart() -> None:
    async def exercise() -> None:
        config = default_config()
        policy = PolicyService(
            SimpleNamespace(get_config=lambda: config), SimpleNamespace(command=AsyncMock()),
            LogService(emit_startup=False),
        )
        policy.safety.interrupt(emergency=True)
        with pytest.raises(RuntimeError, match="emergency stop active"):
            await policy.queue_action({"side": "left", "axis": "X", "step": 5})
        policy.safety.acknowledge(policy.safety.capture())
        await policy.auto_start()
        assert policy.auto_status()["queueDepth"] == 0

    asyncio.run(exercise())


def make_commands(config=None, *, safety=None):
    config = config or default_config()
    telemetry = SimpleNamespace(emergency_stop=lambda: None, acknowledge_safety=lambda: None)
    hal = SimpleNamespace(command=AsyncMock(return_value={"ok": True}))
    return CommandService(
        SimpleNamespace(get_config=lambda: config), telemetry, hal, LogService(emit_startup=False), safety=safety
    )


def test_pending_manual_move_cannot_resume_after_emergency_acknowledgement() -> None:
    async def exercise() -> None:
        commands = make_commands()
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_config():
            entered.set()
            await release.wait()
            return default_config()

        commands._get_config_async = delayed_config
        request = ManualAxisMoveRequest(side="left", axis="X", direction=1, step=5, speedMode="fine")
        pending = asyncio.create_task(commands.manual_axis_move(request))
        await asyncio.wait_for(entered.wait(), 2)
        await commands.emergency_stop()
        await commands.acknowledge_safety()
        release.set()
        with pytest.raises(RuntimeError, match="newer stop"):
            await pending
        assert [call.args[0] for call in commands.hal.command.call_args_list] == [
            "motion.emergency_stop", "motion.acknowledge_estop",
        ]

    asyncio.run(exercise())


@pytest.mark.parametrize("stop_kind", ["emergency", "side"])
def test_coarse_manual_move_does_not_send_remaining_chunks_after_stop(stop_kind: str, monkeypatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    async def exercise() -> None:
        config = default_config()
        config["hal"]["mode"] = "real"
        commands = make_commands(config)
        # 使用离线限位/轴状态替身，保留真正的分段派发和停止逻辑。
        commands._validate_motion_axis_enabled = AsyncMock()
        commands._validate_manual_axis_soft_limit = AsyncMock(return_value={})
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_idle(*_args):
            entered.set()
            await release.wait()

        commands._wait_manual_axis_idle = delayed_idle
        request = ManualAxisMoveRequest(side="left", axis="Yaw", direction=1, step=3, speedMode="coarse")
        pending = asyncio.create_task(commands.manual_axis_move(request))
        await asyncio.wait_for(entered.wait(), 2)
        if stop_kind == "emergency":
            await commands.emergency_stop()
            await commands.acknowledge_safety()
        else:
            await commands.stop_motion_side("left")
        release.set()
        with pytest.raises(RuntimeError, match="newer stop"):
            await pending
        moves = [call for call in commands.hal.command.call_args_list if call.args[0] == "motion.manual_axis_move"]
        assert len(moves) == 1
        assert moves[0].args[1]["step"] == 2

    asyncio.run(exercise())


def test_late_acknowledgement_cannot_clear_newer_emergency() -> None:
    async def exercise() -> None:
        commands = make_commands()
        commands.telemetry.acknowledge_safety = AsyncMock()
        entered, release = asyncio.Event(), asyncio.Event()

        async def hal_command(name, *_args):
            if name == "motion.acknowledge_estop":
                entered.set()
                await release.wait()
            return {"ok": True}

        commands.hal.command.side_effect = hal_command
        await commands.emergency_stop()
        pending = asyncio.create_task(commands.acknowledge_safety())
        await asyncio.wait_for(entered.wait(), 2)
        await commands.emergency_stop()
        release.set()
        with pytest.raises(RuntimeError, match="superseded"):
            await pending
        assert commands.safety.latched
        commands.telemetry.acknowledge_safety.assert_not_called()

    asyncio.run(exercise())


def test_local_telemetry_failure_does_not_skip_hardware_emergency_stop() -> None:
    async def exercise() -> None:
        commands = make_commands()

        def failed_projection():
            raise RuntimeError("broken local telemetry")

        commands.telemetry.emergency_stop = failed_projection
        await commands.emergency_stop()
        commands.hal.command.assert_awaited_once_with("motion.emergency_stop")
        assert commands.safety.latched

    asyncio.run(exercise())


def test_failed_emergency_and_acknowledgement_keep_motion_blocked() -> None:
    async def exercise() -> None:
        commands = make_commands()
        commands.hal.command.side_effect = RuntimeError("HAL reply lost")
        with pytest.raises(RuntimeError, match="reply lost"):
            await commands.emergency_stop()
        with pytest.raises(RuntimeError, match="reply lost"):
            await commands.acknowledge_safety()
        commands._get_config_async = AsyncMock(return_value=default_config())
        with pytest.raises(RuntimeError, match="emergency stop active"):
            await commands.enable_motion_side("left")
        commands._get_config_async.assert_not_called()
        commands.hal.command.side_effect = None
        await commands.acknowledge_safety()
        assert not commands.safety.latched

    asyncio.run(exercise())


@pytest.mark.parametrize("stop_kind", ["emergency", "source"])
def test_pending_native_configure_cannot_start_after_stop(stop_kind: str, monkeypatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def exercise() -> None:
        config = default_config()
        config["hal"]["mode"] = "real"
        config["teleop"]["leftConnected"] = True
        safety = MotionSafetyGate()
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []

        async def command(name, payload):
            seen.append(name)
            if name == "teleop.native.configure":
                entered.set()
                await release.wait()
            return {"ok": True}

        mapper = TeleopMappingService(
            SimpleNamespace(get_config=lambda: config), SimpleNamespace(command=command),
            LogService(emit_startup=False), safety=safety,
        )
        mapper._validate_native_start_current_rotation_positions = AsyncMock()
        pending = asyncio.create_task(mapper.start("teleop-connect", pre_home=False))
        await asyncio.wait_for(entered.wait(), 2)
        stopping = None
        if stop_kind == "emergency":
            safety.interrupt(emergency=True)
            safety.acknowledge(safety.capture())
        else:
            stopping = asyncio.create_task(mapper.stop("teleop-connect", restart_remaining=False))
            await asyncio.sleep(0)
        release.set()
        with pytest.raises(RuntimeError, match="newer stop"):
            await pending
        if stopping is not None:
            await stopping
        assert "teleop.native.start" not in seen
        assert not mapper._arm_sources

    asyncio.run(exercise())


def test_pending_record_reset_cannot_start_after_emergency_acknowledgement() -> None:
    async def exercise() -> None:
        recorder = object.__new__(DatasetRecorderService)
        recorder.safety = MotionSafetyGate()
        recorder._lock = asyncio.Lock()
        recorder._recording_config = lambda: {}
        recorder.teleop = SimpleNamespace(start=AsyncMock())
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_clock(_config):
            entered.set()
            await release.wait()
            return (0.0, 0.0)

        recorder._episode_clock_pair = delayed_clock
        pending = asyncio.create_task(recorder.skip_reset())
        await asyncio.wait_for(entered.wait(), 2)
        recorder.safety.interrupt(emergency=True)
        recorder.safety.acknowledge(recorder.safety.capture())
        release.set()
        with pytest.raises(RuntimeError, match="newer stop"):
            await pending
        recorder.teleop.start.assert_not_called()

    asyncio.run(exercise())


def test_disconnect_refresh_ignores_own_side_stop_but_never_global_stop() -> None:
    safety = MotionSafetyGate()
    token = safety.capture()
    safety.interrupt("left")
    safety.check(token, ignore_side="left")
    safety.interrupt(emergency=True)
    safety.acknowledge(safety.capture())
    with pytest.raises(RuntimeError, match="newer stop"):
        safety.check(token, ignore_side="left")


@pytest.mark.parametrize("hardware_fails", [False, True])
def test_emergency_route_prioritizes_hardware_and_keeps_hal_failure_visible(
    tmp_path, monkeypatch, hardware_fails: bool
) -> None:
    from backend.app import create_app

    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    app = create_app(tmp_path)

    async def exercise() -> None:
        events = []

        async def failed_cleanup():
            events.append("cleanup")
            raise RuntimeError("policy cleanup failed")

        async def hardware_stop(name, *_args):
            assert name == "motion.emergency_stop"
            events.append("hardware")
            await asyncio.sleep(0)
            if hardware_fails:
                raise RuntimeError("hardware stop failed")
            return {"stopped": True}

        app.state.policy.auto_stop = failed_cleanup
        app.state.commands.hal.command = hardware_stop
        endpoint = next(
            route.endpoint for route in app.routes
            if getattr(route, "path", "") == "/api/motion/emergency_stop"
        )
        if hardware_fails:
            with pytest.raises(RuntimeError, match="hardware stop failed"):
                await endpoint()
        else:
            response = await endpoint()
            assert response.data["stopped"]
        await asyncio.sleep(0)
        assert events == ["hardware", "cleanup"]
        assert app.state.commands.safety.latched

    asyncio.run(exercise())


def test_pending_policy_action_cannot_send_target_after_emergency_acknowledgement(tmp_path, monkeypatch) -> None:
    from fastapi import HTTPException
    from backend.app import create_app

    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    app = create_app(tmp_path)

    async def exercise() -> None:
        from backend.tests.test_control_watchdog import confirm_mock_browser_lease
        await confirm_mock_browser_lease(app.state.control_watchdog)
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_enable(_side):
            entered.set()
            await release.wait()

        commands = app.state.commands
        commands.enable_motion_side = delayed_enable
        commands.hal.command = AsyncMock(return_value={"ok": True})
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/policy/action")
        pending = asyncio.create_task(endpoint({
            "action": [0.0] * 14, "dryRun": False, "dataContract": data_contract_metadata(),
        }))
        await asyncio.wait_for(entered.wait(), 2)
        await commands.emergency_stop()
        await commands.acknowledge_safety()
        release.set()
        with pytest.raises(HTTPException) as blocked:
            await pending
        assert "newer stop" in blocked.value.detail["message"]
        assert [call.args[0] for call in commands.hal.command.call_args_list] == [
            "motion.emergency_stop", "motion.acknowledge_estop",
        ]

    asyncio.run(exercise())


def test_pending_gravity_enable_cannot_resume_after_emergency_acknowledgement(tmp_path, monkeypatch) -> None:
    from threading import Event
    from fastapi import HTTPException
    from backend.app import create_app

    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    app = create_app(tmp_path)

    async def exercise() -> None:
        from backend.tests.test_control_watchdog import confirm_mock_browser_lease
        await confirm_mock_browser_lease(app.state.control_watchdog)
        entered, release = Event(), Event()

        def delayed_save(config, **_kwargs):
            entered.set()
            assert release.wait(2)
            return config

        app.state.settings.save_config = delayed_save
        commands = app.state.commands
        commands.hal.command = AsyncMock(return_value={"ok": True})
        endpoint = next(
            route.endpoint for route in app.routes
            if getattr(route, "path", "") == "/api/teleop/{side}/gravity_compensation"
        )
        pending = asyncio.create_task(endpoint("left", {"enabled": True}))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            await commands.emergency_stop()
            await commands.acknowledge_safety()
        finally:
            release.set()
        with pytest.raises(HTTPException) as blocked:
            await pending
        assert blocked.value.status_code == 409
        assert [call.args[0] for call in commands.hal.command.call_args_list] == [
            "motion.emergency_stop", "motion.acknowledge_estop",
        ]

    asyncio.run(exercise())


def test_gravity_disable_during_emergency_cannot_restore_other_side_force(tmp_path, monkeypatch) -> None:
    from fastapi import HTTPException
    from backend.app import create_app

    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    app = create_app(tmp_path)

    async def exercise() -> None:
        commands = app.state.commands
        commands.hal.command = AsyncMock(return_value={"ok": True})
        await commands.emergency_stop()
        endpoint = next(
            route.endpoint for route in app.routes
            if getattr(route, "path", "") == "/api/teleop/{side}/gravity_compensation"
        )
        with pytest.raises(HTTPException) as blocked:
            await endpoint("left", {"enabled": True})
        assert blocked.value.status_code == 409
        await endpoint("left", {"enabled": False})
        last_call = commands.hal.command.call_args
        assert last_call.args[0] == "omega7.gravity_compensation"
        assert last_call.args[1]["leftEnabled"] is False
        assert last_call.args[1]["rightEnabled"] is False

    asyncio.run(exercise())


@pytest.mark.parametrize("ack_succeeds", [True, False])
def test_hal_only_trip_acknowledgement_invalidates_pending_manual_config(ack_succeeds: bool, monkeypatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")

    async def exercise() -> None:
        commands = make_commands()
        commands.telemetry.apply_axis_move = Mock(return_value=5.0)
        commands.hal.motion_state = AsyncMock(return_value={"enabled": [True] * 12, "pulses": [0.0] * 12})
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_config():
            entered.set()
            await release.wait()
            return default_config()

        commands._get_config_async = delayed_config
        request = ManualAxisMoveRequest(side="left", axis="X", direction=1, step=5, speedMode="fine")
        pending = asyncio.create_task(commands.manual_axis_move(request))
        await asyncio.wait_for(entered.wait(), 2)
        # HAL 自行力急停，后端从未收到 emergency_stop 调用，本地代际尚未变化。
        assert not commands.safety.latched
        if not ack_succeeds:
            async def reject_ack(name, *_args):
                if name == "motion.acknowledge_estop":
                    raise RuntimeError("force remains unsafe")
                return {"ok": True}

            commands.hal.command.side_effect = reject_ack
            with pytest.raises(RuntimeError, match="force remains unsafe"):
                await commands.acknowledge_safety()
        else:
            await commands.acknowledge_safety()
        release.set()
        with pytest.raises(RuntimeError, match="newer stop|emergency stop active"):
            await pending
        assert commands.safety.latched is not ack_succeeds
        commands.telemetry.apply_axis_move.assert_not_called()
        commands.hal.command.assert_awaited_once_with("motion.acknowledge_estop", {})

    asyncio.run(exercise())


def test_hal_only_trip_acknowledgement_clears_old_policy_queue(tmp_path, monkeypatch) -> None:
    from backend.app import create_app
    from backend.tests.test_control_watchdog import confirm_mock_browser_lease

    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    app = create_app(tmp_path)

    async def exercise() -> None:
        policy, commands = app.state.policy, app.state.commands
        config = default_config()
        config["auto"]["allowHardwareDispatch"] = True
        policy._get_config_async = AsyncMock(return_value=config)
        commands.hal.command = AsyncMock(return_value={"response": {"ok": True, "leaseFresh": True}})
        await confirm_mock_browser_lease(app.state.control_watchdog)
        commands.hal.command.reset_mock()
        await policy.auto_start()
        await policy.queue_action({"side": "left", "axis": "X", "step": 5})
        assert not commands.safety.latched
        endpoint = next(
            route.endpoint for route in app.routes
            if getattr(route, "path", "") == "/api/motion/safety/acknowledge"
        )
        await endpoint()
        assert policy.auto_status(config)["running"] is False
        assert policy.auto_status(config)["queueDepth"] == 0
        await policy.auto_start()
        result = await policy.dispatch_next()
        assert result["dispatched"] is False
        commands.hal.command.assert_awaited_once_with("motion.acknowledge_estop", {})
        await app.state.control_watchdog.close()

    asyncio.run(exercise())
