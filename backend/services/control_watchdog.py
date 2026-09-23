from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from contextvars import ContextVar
from typing import Any

request_control_session: ContextVar[str | None] = ContextVar("request_control_session", default=None)


@dataclass
class BrowserLease:
    session_id: str
    send: Callable[[dict[str, Any]], Awaitable[None]]
    expired: bool = False
    announced: bool = False
    sending: asyncio.Task[None] | None = None
    pending_messages: dict[str, dict[str, Any]] = field(default_factory=dict)


class ControlLeaseUnavailable(RuntimeError):
    pass


class ControlWatchdog:
    """后端独立维护 HAL 执行租约，浏览器仅持有连接会话。"""

    def __init__(
        self,
        hal: Any,
        logs: Any,
        invalidate: Callable[[], None],
        emergency_stop: Callable[[], Awaitable[Any]],
        *,
        clock: Callable[[], float] = time.monotonic,
        renew_interval_s: float = 0.5,
        hal_timeout_ms: int = 2500,
        disconnect_grace_s: float = 0.0,
    ) -> None:
        self.hal = hal
        self.logs = logs
        self.invalidate = invalidate
        self.emergency_stop = emergency_stop
        self.clock = clock
        self.renew_interval_s = renew_interval_s
        self.hal_timeout_ms = hal_timeout_ms
        self.disconnect_grace_s = max(0.0, float(disconnect_grace_s))
        self.session_id = uuid.uuid4().hex
        self.clients: dict[str, BrowserLease] = {}
        self.last_browser_session: str | None = None
        self._sequence = 0
        self._generation = 0
        self._next_renew_at = 0.0
        self._task: asyncio.Task[None] | None = None
        self._stop_task: asyncio.Task[None] | None = None
        self._disconnect_task: asyncio.Task[None] | None = None
        self._stop_confirmed = True
        self._tripped = False
        self._closed = False
        self._confirmed_until = 0.0
        self._confirmed_generation = -1
        self._send_tasks: set[asyncio.Task[None]] = set()

    def require_ready(self) -> None:
        now = self.clock()
        session = request_control_session.get()
        if session is not None and session not in self.clients:
            raise ControlLeaseUnavailable("request does not own the current browser control session")
        if (
            self._closed or self._tripped or not self._healthy()
            or self._confirmed_until <= now or self._confirmed_generation != self._generation
        ):
            raise ControlLeaseUnavailable("connected browser session and confirmed HAL control lease are required")

    def require_ack_ready(self) -> None:
        self.require_ready()
        if not self._stop_confirmed or (self._stop_task is not None and not self._stop_task.done()):
            raise ControlLeaseUnavailable("fail-safe stop is still pending")

    def confirm_stop(self) -> None:
        self._stop_confirmed = True

    def register(self, send: Callable[[dict[str, Any]], Awaitable[None]]) -> str:
        if self._closed:
            raise ControlLeaseUnavailable("control watchdog is closed")
        if self.clients:
            raise ControlLeaseUnavailable("another browser already owns control; use observer mode")
        if self._disconnect_task is not None and not self._disconnect_task.done():
            self._disconnect_task.cancel()
        self._disconnect_task = None
        self._next_renew_at = 0.0
        session_id = uuid.uuid4().hex
        self.last_browser_session = session_id
        self.clients[session_id] = BrowserLease(session_id, send)
        self._generation += 1
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="control-watchdog")
        return session_id

    def remove(self, session_id: str) -> None:
        client = self.clients.pop(session_id, None)
        if client is None:
            return
        if client.sending is not None:
            client.sending.cancel()
        if not client.expired and not self.clients:
            if self.disconnect_grace_s <= 0.0:
                self.trip("browser WebSocket disconnected")
                return
            generation = self._generation
            self._disconnect_task = asyncio.create_task(
                self._trip_after_disconnect_grace(generation),
                name="control-watchdog-disconnect-grace",
            )

    async def _trip_after_disconnect_grace(self, generation: int) -> None:
        try:
            await asyncio.sleep(self.disconnect_grace_s)
            if self._closed or self.clients or generation != self._generation:
                return
            self.trip("browser WebSocket disconnected")
        except asyncio.CancelledError:
            return

    def trip(self, reason: str) -> None:
        disconnect_task = self._disconnect_task
        self._disconnect_task = None
        if disconnect_task is not None and disconnect_task is not asyncio.current_task():
            disconnect_task.cancel()
        self._generation += 1
        self._confirmed_until = 0.0
        self._next_renew_at = 0.0
        for client in self.clients.values():
            client.expired = True
            self._send_status(client, "expired")
        # 旧会话不再参与健康判定，迟到断开/回复不能影响新会话。
        self.clients.clear()
        if self._tripped:
            return
        self._tripped = True
        self._stop_confirmed = False
        # 同步失效旧操作和策略队列，之后才异步发送硬件急停。
        self.invalidate()
        self.logs.error("[SAFETY]", f"control lease lost: {reason}")
        if self._stop_task is None or self._stop_task.done():
            self._stop_task = asyncio.create_task(self._stop_hardware(), name="control-watchdog-emergency")

    async def _stop_hardware(self) -> None:
        for attempt in range(2):
            try:
                await self.emergency_stop()
                self.confirm_stop()
                return
            except Exception as exc:
                self.logs.error("[SAFETY]", f"watchdog emergency stop unconfirmed; HAL lease must expire: {exc}")
                # 原生调用被隔离时不能释放占用或另起线程；仅重试已返回的幂等停止。
                if bool(getattr(self.hal, "_control_transport_failed", False)) or attempt == 1:
                    return
                await asyncio.sleep(0.1)

    def _healthy(self) -> bool:
        return bool(self.clients) and all(not client.expired for client in self.clients.values())

    def _send(self, client: BrowserLease, message: dict[str, Any]) -> None:
        client.pending_messages[message["type"]] = message
        if client.sending is not None and not client.sending.done():
            return  # 只保留最新会话状态，禁止慢客户端积累无界队列。

        async def send() -> None:
            try:
                while client.pending_messages:
                    key = next(iter(client.pending_messages))
                    current = client.pending_messages.pop(key)
                    await client.send(current)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                client.pending_messages.clear()
                if self.clients.get(client.session_id) is client and not client.expired:
                    self.trip(f"browser connection send failed: {type(exc).__name__}: {exc}")

        client.sending = asyncio.create_task(send(), name="control-watchdog-send")
        self._send_tasks.add(client.sending)
        client.sending.add_done_callback(self._send_tasks.discard)

    def _send_status(self, client: BrowserLease, status: str) -> None:
        self._send(client, {"type": "control_lease", "data": {
            "sessionId": client.session_id,
            "renewalOwner": "backend",
            "status": status, "ttlMs": self.hal_timeout_ms,
            "restartRequired": getattr(self.hal, "_control_transport_failed", False) is True,
        }})

    async def cycle(self) -> None:
        now = self.clock()
        if not self._healthy() or now < self._next_renew_at:
            return
        generation = self._generation
        self._sequence += 1
        try:
            result = await asyncio.wait_for(self.hal.command("control.lease", {
                "sessionId": self.session_id,
                "sequence": self._sequence,
                "timeoutMs": self.hal_timeout_ms,
                "issuedAtUnixMs": int(time.time() * 1000),
            }), 0.75)
            response = result.get("response", {})
            if response.get("ok") is not True or response.get("leaseFresh") is not True:
                raise RuntimeError("HAL did not confirm a fresh control lease")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.trip(f"HAL control lease renewal failed: {type(exc).__name__}: {exc}")
            return
        if generation != self._generation or not self._healthy():
            return
        self._next_renew_at = self.clock() + self.renew_interval_s
        self._confirmed_until = now + self.hal_timeout_ms / 1000.0
        self._confirmed_generation = generation
        self._tripped = False
        for client in self.clients.values():
            if not client.announced:
                client.announced = True
                self._send_status(client, "active")

    async def _run(self) -> None:
        try:
            while not self._closed:
                await self.cycle()
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.trip(f"control watchdog failed: {exc}")

    async def close(self) -> None:
        self._closed = True
        if self._disconnect_task is not None:
            self._disconnect_task.cancel()
            self._disconnect_task = None
        if self.clients:
            self.trip("backend control watchdog shutdown")
        for client in self.clients.values():
            if client.sending is not None:
                client.sending.cancel()
        self.clients.clear()
        sending = tuple(self._send_tasks)
        for task in sending:
            task.cancel()
        if sending:
            await asyncio.wait(sending, timeout=0.5)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._stop_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._stop_task), 1.0)
            except TimeoutError:
                self.logs.error("[SAFETY]", "shutdown stop remains unconfirmed; HAL lease renewal is disabled")
