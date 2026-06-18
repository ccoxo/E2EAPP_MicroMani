"""DDS-backed HAL client with HTTP-like command semantics.

The backend can talk to HAL over Fast DDS instead of REST. This adapter keeps
the public HalClient contract stable while mapping cached state topics and
request/reply command topics into the same async methods used by callers.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Protocol

from backend.core.logging import LogService
from backend.hal_client.client import HalClient, HalHealth
from backend.hal_client.dds_types import (
    DEFAULT_DDS_DOMAIN_ID,
    TOPIC_HAL_HEALTH,
    TOPIC_HAL_MOTION_STATE,
    TOPIC_HAL_OMEGA_STATE,
    HalCommandReply,
    HalCommandRequest,
    JsonEnvelope,
    now_monotonic_ms,
    now_unix_ms,
)
from backend.hal_client.protocol import command_request_policy, hal_command_payload


class DdsRuntimeUnavailableError(RuntimeError):
    pass


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
    ) -> None:
        self.logs = logs
        self.domain_id = domain_id
        self.reply_timeout_s = reply_timeout_s
        self.transport = transport if transport is not None else create_default_dds_transport(domain_id)
        self.transport.start()

    async def health(self) -> HalHealth:
        try:
            payload = self._read_cached_payload(TOPIC_HAL_HEALTH)
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
        )

    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request_payload = hal_command_payload(name, payload or {})
        timeout_s, attempts = command_request_policy(name, self.reply_timeout_s)
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
            if name == "motion.emergency_stop":
                self.transport.publish_emergency_stop(request)
            else:
                self.transport.publish_command_request(request)
            reply = await asyncio.to_thread(
                self.transport.wait_for_command_reply,
                request.request_id,
                timeout_s,
            )
            if reply is None:
                if attempt < attempts - 1:
                    continue
                raise RuntimeError(f"DDS HAL command timed out: {name} request_id={request.request_id}")
            if not reply.ok:
                raise RuntimeError(reply.error or f"DDS HAL command failed: {name}")
            response = _json_object(reply.result_json or "{}", f"DDS command reply {request.request_id}")
            return {"mode": "real", "transport": "dds", "command": name, "response": response}
        raise RuntimeError(f"DDS HAL command timed out: {name} request_id={last_request_id}")

    async def motion_state(self) -> dict[str, Any]:
        return self._with_receive_timestamp(self._read_cached_payload(TOPIC_HAL_MOTION_STATE))

    async def omega_state(self) -> dict[str, Any]:
        return self._with_receive_timestamp(self._read_cached_payload(TOPIC_HAL_OMEGA_STATE))

    def close(self) -> None:
        self.transport.close()

    def _read_cached_payload(self, topic_name: str) -> dict[str, Any]:
        envelope = self.transport.get_latest(topic_name)
        if envelope is None:
            raise RuntimeError(f"DDS topic cache is empty: {topic_name}")
        return _json_object(envelope.payload_json, topic_name)

    def _with_receive_timestamp(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = now_unix_ms()
        payload = dict(payload)
        payload.setdefault("timestamp_ms", now)
        payload["received_timestamp_ms"] = now
        payload["received_monotonic_ms"] = now_monotonic_ms()
        return payload


def _json_object(payload_json: str, source: str) -> dict[str, Any]:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{source} payload is not JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{source} payload must be a JSON object")
    return payload
