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


def test_quarantined_transport_tells_browser_restart_is_required() -> None:
    async def exercise():
        watchdog, _, hal, _, _ = make_watchdog()
        messages = []
        async def send(message):
            messages.append(message)
        watchdog.register(send)
        hal._control_transport_failed = True
        watchdog.trip("DDS reply timed out")
        await flush()
        assert messages[-1]["data"]["restartRequired"] is True
        with pytest.raises(ControlLeaseUnavailable):
            watchdog.require_ready()
        await watchdog.close()
    asyncio.run(exercise())


async def confirm_mock_browser_lease(watchdog: ControlWatchdog) -> str:
    """旧业务测试显式完成执行侧确认；不覆盖 require_ready 或修改内部确认状态。"""
    watchdog._run = AsyncMock()
    messages = []

    async def send(message):
        messages.append(message)

    session = watchdog.register(send)
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




def test_websocket_control_session_does_not_wait_for_initial_telemetry(tmp_path, monkeypatch) -> None:
    from backend.app import create_app
    from starlette.websockets import WebSocketState

    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    app = create_app(tmp_path)
    app.state.telemetry.hardware = None

    async def exercise() -> None:
        active = asyncio.Event()
        inbox = asyncio.Queue()

        async def blocked_health():
            await asyncio.Event().wait()

        class Socket:
            client_state = WebSocketState.CONNECTED

            async def accept(self):
                pass

            async def send_json(self, message):
                if message["type"] == "control_lease" and message["data"]["status"] == "active":
                    active.set()

            async def receive_json(self):
                return await inbox.get()

        app.state.hal.health = blocked_health
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/ws")
        task = asyncio.create_task(endpoint(Socket()))
        try:
            await asyncio.wait_for(active.wait(), 0.5)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await app.state.control_watchdog.close()
            app.state.telemetry.shutdown()

    asyncio.run(exercise())


def test_websocket_without_browser_heartbeats_keeps_hal_lease(tmp_path, monkeypatch) -> None:
    from backend.app import create_app
    from starlette.websockets import WebSocketState

    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_WS_PERIOD_SEC", "0.001")
    app = create_app(tmp_path)
    app.state.telemetry.hardware = None
    watchdog = app.state.control_watchdog
    watchdog.renew_interval_s = 0.01

    async def exercise() -> None:
        inbox = asyncio.Queue()
        active, stopped = asyncio.Event(), asyncio.Event()
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
                if message["type"] == "control_lease" and message["data"]["status"] == "active":
                    active.set()

            async def receive_json(self):
                return await inbox.get()

        app.state.hal.command = command
        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/ws")
        task = asyncio.create_task(endpoint(Socket()))
        try:
            await asyncio.wait_for(active.wait(), 1)
            await asyncio.sleep(0.2)
            assert not stopped.is_set()
            watchdog.require_ready()
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


def test_backend_renews_without_any_browser_response():
    async def exercise():
        watchdog, now, hal, invalidate, stop = make_watchdog()
        messages = []
        async def send(message):
            messages.append(message)
        watchdog.register(send)
        try:
            for tick in range(121):
                now[0] = tick * 0.5
                await watchdog.cycle()
                watchdog.require_ready()
                await flush()
            assert hal.command.await_count == 121
            assert len(messages) == 1
            assert messages[0]["data"]["renewalOwner"] == "backend"
            invalidate.assert_not_called()
            stop.assert_not_awaited()
        finally:
            await watchdog.close()
    asyncio.run(exercise())


def test_slow_browser_send_does_not_block_renewal_and_is_cancelled_on_close():
    async def exercise():
        watchdog, now, hal, invalidate, _stop = make_watchdog()
        entered, release = asyncio.Event(), asyncio.Event()
        async def send(message):
            entered.set()
            await release.wait()
        watchdog.register(send)
        try:
            await watchdog.cycle()
            await entered.wait()
            await asyncio.sleep(0.55)
            now[0] = 1
            await watchdog.cycle()
            watchdog.require_ready()
            assert hal.command.await_count == 2
            invalidate.assert_not_called()
            assert len(watchdog._send_tasks) == 1
        finally:
            await watchdog.close()
        assert not watchdog._send_tasks
    asyncio.run(exercise())
