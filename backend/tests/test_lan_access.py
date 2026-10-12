from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app


@pytest.mark.parametrize("origin,allowed", [
    ("http://127.0.0.1:5174", True),
    ("http://localhost:5173", True),
    ("http://192.168.101.18:5174", True),
    ("http://10.0.0.2:5173", True),
    ("http://172.16.0.2:4173", True),
    ("http://172.31.255.2:5174", True),
    ("http://172.32.0.2:5174", False),
    ("http://8.8.8.8:5174", False),
    ("http://example.com:5174", False),
    ("http://192.168.101.18.evil.example:5174", False),
])
def test_lan_cors_preflight(tmp_path, monkeypatch, origin, allowed):
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    # 不进入 lifespan，不启动设备线程，只验证 CORS 预检。
    client = TestClient(create_app(tmp_path / "runtime"))
    response = client.options("/api/config", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-control-session",
    })
    assert response.status_code == (200 if allowed else 400)
    assert response.headers.get("access-control-allow-origin") == (origin if allowed else None)


@pytest.mark.parametrize("name", [
    "start-ui.ps1", "start-stack.ps1", "start-stack-dds.ps1",
    "run-act-deploy.ps1", "run-act-jepa-deploy.ps1",
])
def test_backend_startup_listens_on_all_interfaces(name):
    root = Path(__file__).resolve().parents[2]
    script = (root / "scripts" / name).read_text(encoding="utf-8")
    assert '"--host", "0.0.0.0"' in script
