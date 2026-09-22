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


@pytest.mark.parametrize('role,auto,fixed_fps', [
    ('wrist_left', True, True), ('wrist_right', True, True),
    ('wrist_left', False, False), ('global', True, False),
])
def test_tuning_existing_direct_capture_reapplies_fixed_fps_after_exposure(monkeypatch, role, auto, fixed_fps):
    driver = camera_opencv.OpenCVCameraDriver()
    capture = object()
    driver._captures[2] = capture
    calls = []
    monkeypatch.setattr(camera_opencv, 'import_module', lambda name: object())
    monkeypatch.setattr(driver, '_resolved_indices', lambda *args: {role: 2})
    monkeypatch.setattr(driver, '_capture_size', lambda *args: (640, 480))
    monkeypatch.setattr(driver, '_get_capture', lambda *args: capture)
    monkeypatch.setattr(driver, '_apply_tuning', lambda cv2, cap, profile: (
        calls.append(('autoExposure', profile['autoExposure'])) or {}
    ))
    monkeypatch.setattr(driver, '_disable_low_light_compensation', lambda index: calls.append(('fixed_fps', index)))

    driver.apply_tuning({'cameras': {'tuning': {role: {'autoExposure': auto}}}}, role)

    assert calls == [('autoExposure', auto)] + ([('fixed_fps', 2)] if fixed_fps else [])
    assert driver._captures[2] is capture
