import asyncio

from starlette.websockets import WebSocketDisconnect, WebSocketState

from backend.app import create_app


def test_ws_disconnect_logs_close_code_without_weakening_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    app = create_app(tmp_path)
    app.state.telemetry.hardware = None

    async def exercise():
        sent = asyncio.Event()

        class Socket:
            client_state = WebSocketState.CONNECTED

            async def accept(self):
                pass

            async def receive_json(self):
                await sent.wait()
                raise WebSocketDisconnect(code=1006, reason="test disconnect")

            async def send_json(self, message):
                if message["type"] == "telemetry":
                    sent.set()

        endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/ws")
        task = asyncio.create_task(endpoint(Socket()))
        try:
            await asyncio.wait_for(sent.wait(), 2)
            for _ in range(100):
                messages = [entry.msg for entry in app.state.logs.list_entries()]
                if any("event=ws_receive_ended" in msg for msg in messages):
                    break
                await asyncio.sleep(.01)
            entry = next(msg for msg in messages if "event=ws_receive_ended" in msg)
            assert "closeCode=1006" in entry
            assert "errorType=WebSocketDisconnect" in entry
            assert app.state.control_watchdog._tripped
            assert not app.state.control_watchdog.clients
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await app.state.control_watchdog.close()
            app.state.telemetry.shutdown()

    asyncio.run(exercise())


def test_safety_reason_is_logged_once_per_transition(tmp_path, monkeypatch):
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    app = create_app(tmp_path)
    app.state.telemetry.hardware = None
    original = app.state.hal.force_state

    async def force_state():
        payload = await original()
        payload["safety"] = {
            "latched": True, "reason": "control_lease_lost", "side": "",
            "channel": "", "value": 0, "canAcknowledge": False,
        }
        return payload

    monkeypatch.setattr(app.state.hal, "force_state", force_state)

    async def exercise():
        class Socket:
            query_params = {"mode": "observe"}
            client_state = WebSocketState.CONNECTED
            frames = 0

            async def accept(self):
                pass

            async def send_json(self, message):
                if message["type"] == "telemetry":
                    self.frames += 1
                    if self.frames == 3:
                        raise WebSocketDisconnect(code=1000)

        endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/ws")
        try:
            await asyncio.wait_for(endpoint(Socket()), 2)
            messages = [entry.msg for entry in app.state.logs.list_entries()
                        if "event=hal_safety_state" in entry.msg]
            assert len(messages) == 1
            assert "reason=control_lease_lost" in messages[0]
            assert "latched=true" in messages[0]
        finally:
            await app.state.control_watchdog.close()
            app.state.telemetry.shutdown()

    asyncio.run(exercise())
