import json
import os
from unittest.mock import Mock

import pytest

from backend.core.config import SettingsService


def sharing_error():
    error = PermissionError("配置文件被占用")
    error.winerror = 5
    return error


@pytest.mark.parametrize("failures", [2, 5])
def test_atomic_replace_retries_without_rewriting_or_losing_original(tmp_path, monkeypatch, failures):
    path = tmp_path / "config.json"
    path.write_text('{"old": true}', encoding="utf-8")
    replace = os.replace
    sources = []

    def busy_then_replace(source, target):
        sources.append(source)
        assert json.loads(path.read_text()) == {"old": True}
        if len(sources) <= failures:
            raise sharing_error()
        replace(source, target)

    monkeypatch.setattr("backend.core.config.os.replace", busy_then_replace)
    monkeypatch.setattr("backend.core.config.time.sleep", lambda _: None)
    service = SettingsService.__new__(SettingsService)
    if failures == 5:
        with pytest.raises(PermissionError):
            service._atomic_write_json(path, {"new": True})
        assert json.loads(path.read_text()) == {"old": True}
        assert len(sources) == 5
    else:
        service._atomic_write_json(path, {"new": True})
        assert json.loads(path.read_text()) == {"new": True}
        assert len(sources) == 3
    assert len(set(sources)) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_replace_retry_checks_cancellation_before_next_commit(tmp_path, monkeypatch):
    replace = Mock(side_effect=sharing_error())
    check = Mock(side_effect=[None, RuntimeError("已急停")])
    monkeypatch.setattr("backend.core.config.os.replace", replace)
    monkeypatch.setattr("backend.core.config.time.sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="已急停"):
        SettingsService.__new__(SettingsService)._atomic_write_json(
            tmp_path / "config.json", {}, before_commit=check,
        )
    assert check.call_count == 2
    replace.assert_called_once()
    assert not list(tmp_path.glob("*.tmp"))


def test_nontransient_write_error_is_not_retried(tmp_path, monkeypatch):
    replace = Mock(side_effect=OSError(28, "disk full"))
    monkeypatch.setattr("backend.core.config.os.replace", replace)
    with pytest.raises(OSError):
        SettingsService.__new__(SettingsService)._atomic_write_json(tmp_path / "config.json", {})
    replace.assert_called_once()
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="验证 Windows 文件共享限制")
def test_windows_open_reader_releases_before_retry(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    replace = os.replace
    errors = []
    with path.open("r", encoding="utf-8") as reader:
        def replace_with_reader(source, target):
            try:
                replace(source, target)
            except PermissionError as exc:
                errors.append(exc.winerror)
                reader.close()
                raise

        monkeypatch.setattr("backend.core.config.os.replace", replace_with_reader)
        SettingsService.__new__(SettingsService)._atomic_write_json(path, {"saved": True})
    assert len(errors) == 1
    assert errors[0] in {5, 32, 33}
    assert json.loads(path.read_text()) == {"saved": True}
