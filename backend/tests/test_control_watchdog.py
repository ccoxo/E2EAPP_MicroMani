from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core.logging import LogService
from backend.services.control_watchdog import ControlLeaseUnavailable, ControlWatchdog


def make_watchdog():
    now = [0.0]
    hal = Mock(command=AsyncMock(return_value={"response": {"ok": True, "leaseFresh": True, "timeoutMs": 2500}}))
    invalidate = Mock()
    stop = AsyncMock()
    watchdog = ControlWatchdog(hal, LogService(emit_startup=False), invalidate, stop, clock=lambda: now[0])
    # 周期由测试显式推进；仍测试真正的挑战、应答、续租和急停协程。
    watchdog._run = AsyncMock()
    return watchdog, now, hal, invalidate, stop


async def flush() -> None:
    await asyncio.sleep(0.001)


async def confirm_mock_browser_lease(watchdog: ControlWatchdog) -> str:
    """旧业务测试显式完成租约协议；不覆盖 require_ready 或修改内部确认状态。"""
    watchdog._run = AsyncMock()
    messages = []

    async def send(message):
        messages.append(message)

    session = watchdog.register(send)
    await watchdog.cycle()
    await flush()
    assert watchdog.respond(session, messages[-1]["data"])
    with pytest.raises(ControlLeaseUnavailable):
        watchdog.require_ready()
    await watchdog.cycle()
    await flush()
    watchdog.require_ready()
    return session


def test_ack_readiness_requires_confirmed_current_session_and_rejects_trip_before_hal_expiry() -> None:
    async def exercise() -> None:
        watchdog, _now, _hal, _invalidate, _stop = make_watchdog()
        with pytest.raises(ControlLeaseUnavailable):
            watchdog.require_ready()
        try:
            await confirm_mock_browser_lease(watchdog)
            watchdog.require_ready()
            watchdog.trip("browser lost while HAL TTL remains fresh")
            with pytest.raises(ControlLeaseUnavailable):
                watchdog.require_ready()
        finally:
            await watchdog.close()

    asyncio.run(exercise())


def test_late_response_cannot_revive_browser_before_monitor_checks_timeout() -> None:
    async def exercise() -> None:
        watchdog, now, _hal, invalidate, _stop = make_watchdog()
        session = await confirm_mock_browser_lease(watchdog)
        try:
            now[0] = 0.5
            await watchdog.cycle()
            client = watchdog.clients[session]
            assert client.challenge_deadline == 2.5
            now[0] = 2.01
            # 模拟 monitor 正在等待 HAL：尚未运行下一次 cycle，迟到回应也必须拒绝。
            assert not watchdog.respond(session, {"sessionId": session, "challengeId": client.challenge_id})
            invalidate.assert_called_once()
            with pytest.raises(ControlLeaseUnavailable):
                watchdog.require_ready()
        finally:
            await watchdog.close()

    asyncio.run(exercise())


def test_lease_requires_new_matching_main_thread_response_for_every_renewal() -> None:
    async def exercise() -> None:
        watchdog, now, hal, invalidate, stop = make_watchdog()
        messages = []

        async def send(message):
            messages.append(message)

        session = watchdog.register(send)
        try:
            await watchdog.cycle()
            await flush()
            first = messages[-1]["data"]
            hal.command.assert_not_called()
            assert not watchdog.respond(session, {**first, "challengeId": "wrong"})
            assert watchdog.respond(session, first)
            await watchdog.cycle()
            await flush()
            assert messages[-1]["type"] == "control_lease"
            assert messages[-1]["data"]["status"] == "active"
            assert messages[-1]["data"]["challengeId"] == first["challengeId"]
            assert hal.command.call_count == 1
            payload = hal.command.call_args.args[1]
            assert payload["timeoutMs"] == 2500 and payload["sequence"] == 1
            assert payload["issuedAtUnixMs"] > 0

            now[0] = 0.5
            await watchdog.cycle()
            await flush()
            second = messages[-1]["data"]
            assert second["challengeId"] != first["challengeId"]
            assert not watchdog.respond(session, first)
            assert hal.command.call_count == 1
            now[0] = 2.01
            await watchdog.cycle()
            await flush()
            invalidate.assert_called_once()
            stop.assert_awaited_once()
            assert not watchdog.respond(session, second)
            assert hal.command.call_count == 1
        finally:
            await watchdog.close()

    asyncio.run(exercise())


def test_second_controller_is_rejected_and_owner_disconnect_trips_control() -> None:
    async def exercise() -> None:
        watchdog, _now, hal, invalidate, stop = make_watchdog()
        messages = [[], []]

        async def send_first(message):
            messages[0].append(message)

        async def send_second(message):
            messages[1].append(message)

        first = watchdog.register(send_first)
        with pytest.raises(ControlLeaseUnavailable, match="another browser"):
            watchdog.register(send_second)
        try:
            await watchdog.cycle()
            await flush()
            assert watchdog.respond(first, messages[0][-1]["data"])
            await watchdog.cycle()
            await flush()
            assert hal.command.call_count == 1
            watchdog.remove(first)
            invalidate.assert_called_once()
            await flush()
            stop.assert_awaited_once()
            await watchdog.cycle()
            assert hal.command.call_count == 1
            assert not watchdog.clients
        finally:
            await watchdog.close()

    asyncio.run(exercise())


def test_hal_renewal_failure_never_reports_active_or_automatically_acknowledges() -> None:
    async def exercise() -> None:
        watchdog, _now, hal, invalidate, stop = make_watchdog()
        messages = []

        async def send(message):
            messages.append(message)

        session = watchdog.register(send)
        try:
            await watchdog.cycle()
            await flush()
            watchdog.respond(session, messages[-1]["data"])
            hal.command.side_effect = RuntimeError("lease transport unavailable")
            await watchdog.cycle()
            await flush()
            invalidate.assert_called_once()
            stop.assert_awaited_once()
            assert not any(message["data"].get("status") == "active" for message in messages)
            assert all(call.args[0] == "control.lease" for call in hal.command.call_args_list)
        finally:
            await watchdog.close()

    asyncio.run(exercise())


def test_late_successful_renewal_cannot_reactivate_disconnected_session() -> None:
    async def exercise() -> None:
        watchdog, _now, hal, invalidate, stop = make_watchdog()
        messages = []
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(message):
            messages.append(message)

        async def delayed_renew(*_args):
            entered.set()
            await release.wait()
            return {"response": {"ok": True, "leaseFresh": True}}

        session = watchdog.register(send)
        try:
            await watchdog.cycle()
            await flush()
            watchdog.respond(session, messages[-1]["data"])
            hal.command.side_effect = delayed_renew
            pending = asyncio.create_task(watchdog.cycle())
            await entered.wait()
            watchdog.remove(session)
            release.set()
            await pending
            await flush()
            invalidate.assert_called_once()
            stop.assert_awaited_once()
            assert not any(message["data"].get("status") == "active" for message in messages)
        finally:
            await watchdog.close()

    asyncio.run(exercise())


def test_renewal_acknowledges_only_challenges_captured_before_dispatch() -> None:
    async def exercise() -> None:
        watchdog, now, hal, _invalidate, _stop = make_watchdog()
        messages = []
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(message):
            messages.append(message)

        async def delayed_renew(*_args):
            entered.set()
            await release.wait()
            return {"response": {"ok": True, "leaseFresh": True}}

        session = watchdog.register(send)
        try:
            await watchdog.cycle()
            await flush()
            first = messages[-1]["data"]
            assert watchdog.respond(session, first)
            now[0] = 0.5
            hal.command.side_effect = delayed_renew
            pending = asyncio.create_task(watchdog.cycle())
            await entered.wait()
            await flush()
            second = messages[-1]["data"]
            assert second["challengeId"] != first["challengeId"]
            assert watchdog.respond(session, second)
            release.set()
            await pending
            await flush()
            assert messages[-1]["data"]["challengeId"] == first["challengeId"]
            assert watchdog.clients[session].leased_challenge == first["challengeId"]
            assert watchdog.clients[session].answered_challenge == second["challengeId"]
        finally:
            await watchdog.close()

    asyncio.run(exercise())


def test_websocket_main_thread_stall_triggers_full_backend_emergency(tmp_path, monkeypatch) -> None:
    from backend.app import create_app
    from starlette.websockets import WebSocketState

    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_WS_PERIOD_SEC", "0.001")
    app = create_app(tmp_path)
    app.state.telemetry.hardware = None
    watchdog = app.state.control_watchdog
    watchdog.challenge_interval_s = 0.01
    watchdog.browser_timeout_s = 0.12

    async def exercise() -> None:
        inbox = asyncio.Queue()
        active, stopped = asyncio.Event(), asyncio.Event()
        responds = [True]
        original_command = app.state.hal.command

        async def command(name, payload=None):
            if name == "motion.emergency_stop":
                stopped.set()
            return await original_command(name, payload)

        class Socket:
            client_state = WebSocketState.CONNECTED

            async def accept(self):
                pass

            async def send_json(self, message):
                if message["type"] == "safety_challenge" and responds[0]:
                    await inbox.put({"type": "safety_heartbeat", "data": message["data"]})
                if message["type"] == "control_lease" and message["data"]["status"] == "active":
                    active.set()

            async def receive_json(self):
                return await inbox.get()

        app.state.hal.command = command
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/ws")
        task = asyncio.create_task(endpoint(Socket()))
        try:
            await asyncio.wait_for(active.wait(), 1)
            responds[0] = False
            await asyncio.wait_for(stopped.wait(), 1)
            assert app.state.commands.safety.latched
            assert not app.state.policy.auto_status()["running"]
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await watchdog.close()
            app.state.telemetry.shutdown()

    asyncio.run(exercise())


def test_app_websocket_lease_confirmation_allows_ack_and_disconnect_blocks_it(tmp_path, monkeypatch) -> None:
    from backend.app import create_app
    from httpx import ASGITransport, AsyncClient
    from starlette.websockets import WebSocketDisconnect, WebSocketState

    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_WS_PERIOD_SEC", "0.001")
    app = create_app(tmp_path)
    app.state.telemetry.hardware = None
    watchdog = app.state.control_watchdog

    async def exercise() -> None:
        inbox = asyncio.Queue()
        active, stopped = asyncio.Event(), asyncio.Event()
        commands_seen = []
        original_command = app.state.hal.command

        async def command(name, payload=None):
            commands_seen.append(name)
            result = await original_command(name, payload)
            if name == "motion.emergency_stop":
                stopped.set()
            return result

        class Socket:
            client_state = WebSocketState.CONNECTED

            async def accept(self):
                pass

            async def send_json(self, message):
                if self.client_state != WebSocketState.CONNECTED:
                    raise WebSocketDisconnect()
                if message["type"] == "safety_challenge":
                    await inbox.put({"type": "safety_heartbeat", "data": message["data"]})
                if message["type"] == "control_lease" and message["data"]["status"] == "active":
                    active.set()

            async def receive_json(self):
                message = await inbox.get()
                if message is None:
                    self.client_state = WebSocketState.DISCONNECTED
                    raise WebSocketDisconnect()
                return message

        app.state.hal.command = command
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/ws")
        task = None
        try:
            # ASGITransport 不运行 startup；这里只用 Test HAL 与伪 WebSocket，不连接设备。
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
                response = await http.post("/api/motion/safety/acknowledge")
                assert response.status_code == 409
                assert app.state.commands.safety.latched
                assert "motion.acknowledge_estop" not in commands_seen
                task = asyncio.create_task(endpoint(Socket()))
                await asyncio.wait_for(active.wait(), 1)
                assert "control.lease" in commands_seen
                watchdog.require_ready()
                http.headers["X-Control-Session"] = next(iter(watchdog.clients))
                response = await http.post("/api/motion/safety/acknowledge")
                assert response.status_code == 200
                assert response.json()["data"]["servoRestored"] is False
                assert not app.state.commands.safety.latched
                assert "motion.enable_side" not in commands_seen

                await inbox.put(None)
                await asyncio.wait_for(stopped.wait(), 1)
                assert app.state.commands.safety.latched
                assert not app.state.policy.auto_status()["running"]
                response = await http.post("/api/motion/safety/acknowledge")
                assert response.status_code == 409
                assert commands_seen.count("motion.acknowledge_estop") == 1
                assert not watchdog.clients
        finally:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await watchdog.close()
            app.state.telemetry.shutdown()

    asyncio.run(exercise())


def test_ack_response_after_lease_loss_does_not_clear_backend_latch(tmp_path, monkeypatch) -> None:
    from backend.app import create_app
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    app = create_app(tmp_path)
    watchdog = app.state.control_watchdog

    async def exercise() -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        original_command = app.state.hal.command

        async def command(name, payload=None):
            if name == "motion.acknowledge_estop":
                entered.set()
                await release.wait()
            return await original_command(name, payload)

        app.state.hal.command = command
        session = await confirm_mock_browser_lease(watchdog)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
                http.headers["X-Control-Session"] = session
                pending = asyncio.create_task(http.post("/api/motion/safety/acknowledge"))
                await entered.wait()
                watchdog.trip("lost during HAL acknowledgement")
                release.set()
                response = await pending
                assert response.status_code == 409
                assert app.state.commands.safety.latched
        finally:
            await watchdog.close()
            app.state.telemetry.shutdown()

    asyncio.run(exercise())
