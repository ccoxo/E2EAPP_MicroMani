# 阅读导航 05｜DDS 传输
# 职责：把 HAL 状态主题缓存与带 request_id 的命令应答转换为异步 HalClient 接口。
# 先看：DdsRuntimeUnavailableError → DdsHalTransport → create_default_dds_transport → DdsHalClient。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

"""DDS-backed HAL client with HTTP-like command semantics.

The backend can talk to HAL over Fast DDS instead of REST. This adapter keeps
the public HalClient contract stable while mapping cached state topics and
request/reply command topics into the same async methods used by callers.
"""

from __future__ import annotations

import asyncio
import json
import uuid
import time
from typing import Any, Protocol

from backend.core.logging import LogService
from backend.hal_client.client import HalClient, HalHealth
from backend.hal_client.bounded_lane import BlockingCallTimeout, BoundedLane
from backend.hal_client.dds_types import (
    DEFAULT_DDS_DOMAIN_ID,
    TOPIC_HAL_HEALTH,
    TOPIC_HAL_FORCE_STATE,
    TOPIC_HAL_MOTION_STATE,
    TOPIC_HAL_NATIVE_TELEOP_STATUS,
    TOPIC_HAL_OMEGA_STATE,
    HalCommandReply,
    HalCommandRequest,
    JsonEnvelope,
    now_unix_ms,
)
from backend.hal_client.protocol import command_request_policy, hal_command_payload


class DdsRuntimeUnavailableError(RuntimeError):
    pass


class DdsTransportCallError(RuntimeError):
    pass


# 与运动反馈的新鲜度门限一致；不以 WebSocket 重发时间刷新 DDS 源时间。
_DDS_STATE_MAX_AGE_MS = 500
_DDS_STATE_MAX_FUTURE_MS = 100


class DdsHalTransport(Protocol):
    def start(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def get_latest(self, topic_name: str) -> JsonEnvelope | None:
        raise NotImplementedError

    def publish_command_request(self, request: HalCommandRequest) -> None:
        raise NotImplementedError

    def publish_emergency_stop(self, request: HalCommandRequest) -> None:
        raise NotImplementedError

    def wait_for_command_reply(self, request_id: str, timeout_s: float) -> HalCommandReply | None:
        raise NotImplementedError


def create_default_dds_transport(domain_id: int) -> DdsHalTransport:
    try:
        from backend.hal_client.dds_runtime import FastDdsHalTransport
    except ImportError as exc:
        raise DdsRuntimeUnavailableError("Fast-DDS Python bindings are required for APPSTATION_HAL_TRANSPORT=dds") from exc
    return FastDdsHalTransport(domain_id=domain_id)


class DdsHalClient(HalClient):
    """HalClient implementation that reads cached DDS state and waits for replies."""

    def __init__(
        self,
        logs: LogService,
        *,
        domain_id: int = DEFAULT_DDS_DOMAIN_ID,
        transport: DdsHalTransport | None = None,
        reply_timeout_s: float = 5.0,
        state_timeout_s: float = 0.25,
    ) -> None:
        self.logs = logs
        self.domain_id = domain_id
        self.reply_timeout_s = reply_timeout_s
        self.state_timeout_s = state_timeout_s
        self._command_lane = BoundedLane("dds-command", 4)
        self._emergency_lane = BoundedLane("dds-emergency", 1)
        self._lease_lane = BoundedLane("dds-lease", 1)
        self._state_lane = BoundedLane("dds-state", 2)
        # 录制各源与健康查询并发读取，不能争抢同一对名额；每类仍有界且超时不释放阻塞调用。
        self._motion_state_lane = BoundedLane("dds-motion-state", 2)
        self._omega_state_lane = BoundedLane("dds-omega-state", 2)
        self._force_state_lane = BoundedLane("dds-force-state", 2)
        self._teleop_state_lane = BoundedLane("dds-teleop-state", 2)
        self._close_lane = BoundedLane("dds-close", 1)
        self._closed = False
        self._control_transport_failed = False
        self.on_control_transport_fault = None
        self.transport = transport if transport is not None else create_default_dds_transport(domain_id)
        self.transport.start()

    async def health(self) -> HalHealth:
        try:
            payload = await self._state_lane.run(
                lambda: self._read_cached_payload(TOPIC_HAL_HEALTH), self.state_timeout_s,
            )
        except RuntimeError as exc:
            return HalHealth(
                ltdmc_ok=False,
                omega7_ok=False,
                version="real-hal/dds-unavailable",
                uptime_s=0.0,
                connected=False,
                mode="real",
                message=str(exc),
            )
        return HalHealth(
            ltdmc_ok=bool(payload.get("ltdmc_ok", False)),
            omega7_ok=bool(payload.get("omega7_ok", False)),
            version=str(payload.get("version", "real-hal/unknown")),
            uptime_s=float(payload.get("uptime_s", 0.0)),
            connected=True,
            mode="real",
            message=payload.get("message") if isinstance(payload.get("message"), str) else None,
            capabilities=(
                [str(value) for value in payload["capabilities"]]
                if isinstance(payload.get("capabilities"), list)
                else None
            ),
            source_valid_until_ms=payload["dds_stamp_unix_ms"] + _DDS_STATE_MAX_AGE_MS,
        )

    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        stop_or_read = name in {
            "motion.emergency_stop", "motion.disable_side", "motion.teleop_stop_side",
            "teleop.native.stop", "omega7.zero_force_feedback", "hal.reconnect", "teleop.native.status",
        }
        stop_or_read = stop_or_read or (name == "omega7.gravity_compensation" and (payload or {}).get("leftEnabled") is False and (payload or {}).get("rightEnabled") is False)
        if self._control_transport_failed and (not stop_or_read or name == "hal.reconnect"):
            raise RuntimeError("DDS control transport is quarantined after a blocked call; restart required")
        if name == "teleop.native.status":
            cached_status = await self._teleop_state_lane.run(self._read_cached_native_teleop_status, self.state_timeout_s)
            if cached_status is not None:
                return {"mode": "real", "transport": "dds", "command": name, "response": cached_status}

        request_payload = hal_command_payload(name, payload or {})
        # 超时与重试由共享命令表决定；耗时的回原点命令不使用普通命令的短超时重试。
        timeout_s, attempts = command_request_policy(name, self.reply_timeout_s)
        lane = self._lease_lane if name == "control.lease" else (
            self._emergency_lane if name == "motion.emergency_stop" else self._command_lane
        )
        if name in {"control.lease", "motion.emergency_stop"}:
            timeout_s = min(timeout_s, 0.5)
        control_critical = name not in {"hal.reconnect", "teleop.native.status"}
        if not stop_or_read and name != "control.lease":
            request_payload = dict(request_payload)
            request_payload["commandExpiresAtUnixMs"] = now_unix_ms() + int(timeout_s * 1000)
        last_request_id = ""
        for attempt in range(attempts):
            # Command replies are correlated by request_id because DDS topics are
            # shared; callers should see one success/error result just like REST.
            request = HalCommandRequest(
                request_id=uuid.uuid4().hex,
                stamp_unix_ms=now_unix_ms(),
                name=name,
                payload_json=json.dumps(request_payload, ensure_ascii=False, separators=(",", ":"), default=str),
            )
            last_request_id = request.request_id
            deadline = time.monotonic() + timeout_s

            def exchange() -> HalCommandReply | None:
                # 无队列；线程尚未开始执行就已取消/超时的请求不能迟到发布。
                if self._closed or time.monotonic() >= deadline:
                    raise RuntimeError("DDS request expired before publication")
                try:
                    if name in {"motion.emergency_stop", "control.lease"}:
                        self.transport.publish_emergency_stop(request)
                    else:
                        self.transport.publish_command_request(request)
                    return self.transport.wait_for_command_reply(request.request_id, timeout_s)
                except Exception as exc:
                    raise DdsTransportCallError(f"DDS native exchange failed: {name}: {exc}") from exc

            # publish 与 wait 都可能在原生绑定中阻塞；不得占用事件循环或公共 executor。
            try:
                reply = await lane.run(exchange, timeout_s + 0.05)
            except (BlockingCallTimeout, DdsTransportCallError, asyncio.CancelledError):
                if control_critical:
                    self._quarantine(f"DDS native control call blocked or cancelled: {name}")
                raise
            if reply is None:
                if control_critical:
                    # 停止请求失去应答也必须停止续租，不能让心跳掩盖急停通道故障。
                    self._quarantine(f"DDS control reply timed out: {name}")
                if attempt < attempts - 1:
                    continue
                raise RuntimeError(f"DDS HAL command timed out: {name} request_id={request.request_id}")
            if not reply.ok:
                if name == "motion.emergency_stop":
                    self._quarantine("HAL rejected emergency stop")
                raise RuntimeError(reply.error or f"DDS HAL command failed: {name}")
            try:
                response = _json_object(reply.result_json or "{}", f"DDS command reply {request.request_id}")
            except RuntimeError:
                if control_critical:
                    self._quarantine(f"DDS control reply is invalid: {name}")
                raise
            return {"mode": "real", "transport": "dds", "command": name, "response": response}
        raise RuntimeError(f"DDS HAL command timed out: {name} request_id={last_request_id}")

    def _quarantine(self, reason: str) -> None:
        self._control_transport_failed = True
        if self.on_control_transport_fault is not None:
            self.on_control_transport_fault(reason)

    async def motion_state(self) -> dict[str, Any]:
        return await self._motion_state_lane.run(
            lambda: self._read_cached_payload(TOPIC_HAL_MOTION_STATE), self.state_timeout_s,
        )

    async def omega_state(self) -> dict[str, Any]:
        return await self._omega_state_lane.run(
            lambda: self._read_cached_payload(TOPIC_HAL_OMEGA_STATE), self.state_timeout_s,
        )

    async def force_state(self) -> dict[str, Any]:
        return await self._force_state_lane.run(
            lambda: self._read_cached_payload(TOPIC_HAL_FORCE_STATE), self.state_timeout_s,
        )

    def close(self) -> None:
        self._closed = True
        lanes = (self._command_lane, self._emergency_lane, self._lease_lane, self._state_lane,
                 self._motion_state_lane, self._omega_state_lane, self._force_state_lane, self._teleop_state_lane)
        for lane in lanes:
            lane.close()
        if any(lane.active for lane in lanes):
            # 原生句柄可能仍被阻塞调用持有，不能在后台调用返回前释放它。
            self.logs.error("[HAL]", "DDS close deferred: native calls remain blocked; process restart required")
            return
        self.transport.close()

    async def aclose(self) -> None:
        await self._close_lane.run(self.close, 0.5)

    def _read_cached_payload(self, topic_name: str) -> dict[str, Any]:
        envelope = self.transport.get_latest(topic_name)
        if envelope is None:
            raise RuntimeError(f"DDS topic cache is empty: {topic_name}")
        return self._payload_from_envelope(envelope, topic_name)

    def _read_cached_native_teleop_status(self) -> dict[str, Any] | None:
        envelope = self.transport.get_latest(TOPIC_HAL_NATIVE_TELEOP_STATUS)
        if envelope is None:
            return None
        return self._payload_from_envelope(envelope, TOPIC_HAL_NATIVE_TELEOP_STATUS)

    def _payload_from_envelope(self, envelope: JsonEnvelope, topic_name: str) -> dict[str, Any]:
        stamp_unix_ms = envelope.stamp_unix_ms
        stamp_monotonic_ms = envelope.stamp_monotonic_ms
        if (type(stamp_unix_ms) is not int or stamp_unix_ms <= 0
                or type(stamp_monotonic_ms) is not int or stamp_monotonic_ms < 0):
            raise RuntimeError(f"DDS topic has invalid source timestamp: {topic_name}")
        age_ms = now_unix_ms() - stamp_unix_ms
        if age_ms > _DDS_STATE_MAX_AGE_MS or age_ms < -_DDS_STATE_MAX_FUTURE_MS:
            raise RuntimeError(f"DDS topic source timestamp is stale or in the future: {topic_name} age_ms={age_ms}")
        payload = dict(_json_object(envelope.payload_json, topic_name))
        payload.setdefault("timestamp_ms", stamp_unix_ms)
        payload.setdefault("monotonicMs", stamp_monotonic_ms)
        payload.setdefault("monotonic_s", stamp_monotonic_ms / 1000.0)
        payload["dds_source"] = envelope.source
        payload["dds_stamp_unix_ms"] = stamp_unix_ms
        payload["dds_stamp_monotonic_ms"] = stamp_monotonic_ms
        return payload


def _json_object(payload_json: str, source: str) -> dict[str, Any]:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{source} payload is not JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{source} payload must be a JSON object")
    return payload
