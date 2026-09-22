# 阅读导航 07｜测试与验证
# 职责：回归验证：ADB 离线设备不能被报告为连接成功。
# 先看：test_pico_status_reports_offline_device_as_not_ok。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.core.defaults import default_config
from backend.drivers.pico_adb import PicoAdbDriver


def test_pico_status_reports_offline_device_as_not_ok(monkeypatch: Any) -> None:
    config = default_config()
    config["picoVision"]["ip"] = "10.90.129.166"

    class FakeCompletedProcess:
        returncode = 0
        stdout = "10.90.129.166:5555     offline transport_id:64\n"
        stderr = "error: device offline\n"

    monkeypatch.setattr(PicoAdbDriver, "_script", lambda *_args: Path("check_pico4ultra_wireless_status.bat"))
    monkeypatch.setattr("backend.drivers.pico_adb.subprocess.run", lambda *_args, **_kwargs: FakeCompletedProcess())

    result = PicoAdbDriver().status(config)

    assert result.ok is False
    assert "offline" in result.message
