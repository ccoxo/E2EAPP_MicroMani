from __future__ import annotations

import pytest

from backend.app import make_hal_client
from backend.core.logging import LogService
from backend.hal_client.client import TestHalClient


class FakeDdsHalClient:
    def __init__(self, logs: LogService, *, domain_id: int) -> None:
        self.logs = logs
        self.domain_id = domain_id


def test_make_hal_client_defaults_to_dds_real_hal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPSTATION_HAL_MODE", raising=False)
    monkeypatch.delenv("APPSTATION_HAL_TRANSPORT", raising=False)
    monkeypatch.setattr("backend.app.DdsHalClient", FakeDdsHalClient)
    config = {"hal": {"mode": "real", "baseUrl": "http://127.0.0.1:8091", "timeoutMs": 5000}}
    client = make_hal_client(config, LogService())

    assert isinstance(client, FakeDdsHalClient)
    assert client.domain_id == 42


def test_make_hal_client_keeps_test_hal_even_when_dds_transport_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_HAL_TRANSPORT", "dds")

    client = make_hal_client({"hal": {"mode": "real"}}, LogService())

    assert isinstance(client, TestHalClient)


def test_make_hal_client_uses_dds_transport_for_real_hal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPSTATION_HAL_MODE", raising=False)
    monkeypatch.setenv("APPSTATION_HAL_TRANSPORT", "dds")
    monkeypatch.setenv("APPSTATION_DDS_DOMAIN_ID", "43")
    monkeypatch.setattr("backend.app.DdsHalClient", FakeDdsHalClient)

    client = make_hal_client({"hal": {"mode": "real"}}, LogService())

    assert isinstance(client, FakeDdsHalClient)
    assert client.domain_id == 43


def test_make_hal_client_rejects_http_transport_for_real_hal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPSTATION_HAL_MODE", raising=False)
    monkeypatch.setenv("APPSTATION_HAL_TRANSPORT", "http")

    with pytest.raises(RuntimeError, match="HTTP HAL transport is disabled"):
        make_hal_client({"hal": {"mode": "real"}}, LogService())


def test_make_hal_client_dds_runtime_error_is_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenDdsHalClient:
        def __init__(self, logs: LogService, *, domain_id: int) -> None:
            _ = (logs, domain_id)
            raise RuntimeError("Fast-DDS Python bindings are required")

    monkeypatch.delenv("APPSTATION_HAL_MODE", raising=False)
    monkeypatch.setenv("APPSTATION_HAL_TRANSPORT", "dds")
    monkeypatch.setattr("backend.app.DdsHalClient", BrokenDdsHalClient)

    with pytest.raises(RuntimeError, match="Real HAL DDS transport failed to start.*Fast-DDS Python bindings are required"):
        make_hal_client({"hal": {"mode": "real"}}, LogService())
