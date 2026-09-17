import subprocess
from types import SimpleNamespace

import pytest

from backend.drivers import camera_opencv


def test_fixed_fps_targets_exact_device_and_reapplies(monkeypatch):
    driver = camera_opencv.OpenCVCameraDriver()
    path = "\\\\?\\usb#camera'left"
    monkeypatch.setattr(camera_opencv.sys, 'platform', 'win32')
    monkeypatch.setattr(subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setattr(driver, '_camera_identities_by_index', lambda: {2: {'devicePath': path}})
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='fixed_frame_rate=1 previous_priority=1')

    monkeypatch.setattr(subprocess, 'run', run)
    driver._disable_low_light_compensation(2)
    driver._disable_low_light_compensation(2)
    assert len(calls) == 2
    assert calls[0][0][-1].endswith("::SetFixedFrameRate('\\\\?\\usb#camera''left')")
    assert '$listing =' not in calls[0][0][-1]
    assert calls[0][1]['timeout'] == 8


@pytest.mark.parametrize('failure', ['unsupported', 'timeout'])
def test_fixed_fps_failure_warns_without_blocking_capture(monkeypatch, failure):
    driver = camera_opencv.OpenCVCameraDriver()
    monkeypatch.setattr(camera_opencv.sys, 'platform', 'win32')
    monkeypatch.setattr(subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setattr(driver, '_camera_identities_by_index', lambda: {2: {'devicePath': 'camera'}})
    logs = []
    monkeypatch.setattr(driver, '_log', lambda level, message: logs.append((level, message)))

    def run(*args, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('powershell', 8)
        return SimpleNamespace(returncode=1, stdout='')

    monkeypatch.setattr(subprocess, 'run', run)
    driver._disable_low_light_compensation(2)
    assert logs and logs[0][0] == 'warning'


def test_fixed_fps_does_not_touch_missing_device(monkeypatch):
    driver = camera_opencv.OpenCVCameraDriver()
    monkeypatch.setattr(camera_opencv.sys, 'platform', 'win32')
    monkeypatch.setattr(driver, '_camera_identities_by_index', lambda: {})
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('must not open another camera'))
    driver._disable_low_light_compensation(2)


@pytest.mark.parametrize('role,auto,expected', [
    ('wrist_left', True, [2]), ('wrist_right', True, [2]),
    ('wrist_left', False, []), ('global', True, []),
])
def test_open_applies_fixed_fps_only_to_auto_exposed_wrists(monkeypatch, role, auto, expected):
    driver = camera_opencv.OpenCVCameraDriver()
    calls = []
    monkeypatch.setattr(driver, '_disable_low_light_compensation', calls.append)
    monkeypatch.setattr(driver, '_process_capture_enabled', lambda *args: False)
    monkeypatch.setattr(camera_opencv, '_backend_candidates', lambda cv2: [])
    config = {'cameras': {'tuning': {role: {'autoExposure': auto}}}}
    driver._get_capture(object(), 2, 640, 480, 30, role, config)
    assert calls == expected
