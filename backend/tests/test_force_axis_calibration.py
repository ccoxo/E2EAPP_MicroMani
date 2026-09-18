# 阅读导航 07｜测试与验证
# 职责：回归验证：力轴方向迁移后要求重新确认柔顺映射。
# 先看：test_axis_sign_migration_requires_a_new_compliance_confirmation。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

from __future__ import annotations

import json

from backend.core.config import SettingsService
from backend.core.defaults import default_config
from backend.core.logging import LogService


def test_axis_sign_migration_requires_a_new_compliance_confirmation(tmp_path) -> None:
    legacy = default_config()
    legacy["force"]["source"] = "hkvl_serial"
    legacy["force"].pop("axisSign")
    legacy["force"]["compliance"]["enabled"] = True
    legacy["force"]["compliance"]["left"]["mappingConfirmed"] = True
    legacy["force"]["compliance"]["right"]["mappingConfirmed"] = True
    (tmp_path / "config.json").write_text(json.dumps(legacy), encoding="utf-8")

    config = SettingsService(tmp_path, LogService(emit_startup=False)).get_config()

    assert config["force"]["axisSign"] == {
        "left": [1.0, 1.0, -1.0, -1.0, -1.0, 1.0],
        "right": [1.0, -1.0, 1.0, -1.0, 1.0, -1.0],
    }
    assert config["force"]["compliance"]["enabled"] is False
    assert config["force"]["compliance"]["left"]["mappingConfirmed"] is False
    assert config["force"]["compliance"]["right"]["mappingConfirmed"] is False
