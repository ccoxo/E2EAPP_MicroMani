from __future__ import annotations

import json

import pytest

from backend.core.config import SettingsService
from backend.core.logging import LogService


@pytest.mark.parametrize("old_samples", [1, 199, 5000, 200.5])
def test_legacy_tare_count_migration_preserves_other_device_configuration(tmp_path, old_samples):
    logs = LogService(emit_startup=False)
    settings = SettingsService(tmp_path, logs)
    old_config = settings.get_config()
    old_config["force"]["serial"]["leftPort"] = "COM77"
    old_config["force"]["tareSamples"] = old_samples
    old_config["storage"]["datasetRoot"] = "D:/existing-datasets"
    settings.config_path.write_text(json.dumps(old_config), encoding="utf-8")

    restored = settings.get_config()

    expected = dict(old_config)
    expected["force"] = {**old_config["force"], "tareSamples": 0}
    assert restored == expected
    assert json.loads(settings.config_path.read_text(encoding="utf-8")) == expected
    assert any("默认 200" in entry.msg for entry in logs.list_entries())
    with pytest.raises(ValueError, match="tareSamples"):
        settings.save_config(old_config)
    assert json.loads(settings.config_path.read_text(encoding="utf-8")) == expected


def test_nidaq_legacy_tare_count_is_not_migrated(tmp_path):
    settings = SettingsService(tmp_path, LogService(emit_startup=False))
    config = settings.get_config()
    config["force"].update(source="nidaq", tareSamples=12)
    settings.save_config(config)
    assert settings.get_config()["force"]["tareSamples"] == 12
