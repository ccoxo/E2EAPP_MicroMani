from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

import pytest

from backend.core.defaults import default_config
from backend.drivers import camera_opencv
from backend.services.telemetry_hub import TelemetryHub


@pytest.fixture
def camera_setup(monkeypatch):
    driver = camera_opencv.OpenCVCameraDriver()
    config = default_config()
    clock = SimpleNamespace(now=100.0)
    calls = []
    roles = list(camera_opencv.CAMERA_IDENTITY_KEYS)
    for index, role in enumerate(roles):
        config['cameras'][camera_opencv.CAMERA_DESCRIPTOR_KEYS[role]] = f'index {index}'
        config['cameras'][camera_opencv.CAMERA_IDENTITY_KEYS[role]] = role
    bindings = {role: index for index, role in enumerate(roles)}
    monkeypatch.setattr(camera_opencv, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(driver, '_resolve_indices_by_identity', lambda _: calls.append(1) or dict(bindings))
    monkeypatch.setattr(driver, '_camera_identities_by_index', lambda: {})
    monkeypatch.setattr(driver, '_remap_software_sources', lambda resolved, _: resolved)
    monkeypatch.setattr(driver, '_discover_readable_indices', lambda *args: pytest.fail('must not open fallback devices'))
    return driver, config, clock, calls, bindings


def test_resolved_camera_binding_does_not_expire_during_capture(camera_setup):
    driver, config, clock, calls, bindings = camera_setup
    first = driver._resolved_indices(object(), config, 30)
    clock.now += 120
    assert driver._resolved_indices(object(), config, 30) == first
    assert len(calls) == 1
    driver._clear_probe_cache()
    driver._resolved_indices(object(), config, 30)
    assert len(calls) == 2
    config['cameras']['wristLeftIdentity'] = 'replaced-left'
    bindings['wrist_left'] = 4
    assert driver._resolved_indices(object(), config, 30)['wrist_left'] == 4
    assert len(calls) == 3


def test_three_camera_preview_and_recording_reads_do_not_reenumerate_at_30_seconds(camera_setup, monkeypatch):
    driver, config, clock, calls, bindings = camera_setup
    cv2 = SimpleNamespace(COLOR_BGR2RGB=1, cvtColor=lambda frame, _: frame)
    monkeypatch.setattr(camera_opencv, 'import_module', lambda _: cv2)
    monkeypatch.setattr(driver, '_get_capture', lambda *args: object())
    for index in bindings.values():
        driver._frame_locks[index] = Lock()
        driver._latest_frames[index] = [index]
        driver._latest_jpegs[index] = f'jpeg-{index}'.encode()
    for elapsed in (0, 29.9, 30, 31, 60, 120):
        clock.now = 100 + elapsed
        for role, index in bindings.items():
            driver._latest_at[index] = clock.now
            assert driver.snapshot(config, role) == f'jpeg-{index}'.encode()
            frame = driver.snapshot_frame_with_timestamp(config, role)
            assert frame.frame == [index]
            assert frame.monotonic_s == clock.now
    assert len(calls) == 1


@pytest.mark.parametrize('missing', [('wrist_left',), ('global', 'wrist_left', 'wrist_right')])
def test_unresolved_identity_retries_after_30_seconds_without_index_fallback(camera_setup, missing):
    driver, config, clock, calls, bindings = camera_setup
    for role in missing:
        bindings.pop(role)
    first = driver._resolved_indices(object(), config, 30)
    assert all(first[role] == -1 for role in missing)
    clock.now += 29
    assert driver._resolved_indices(object(), config, 30) == first
    assert len(calls) == 1
    clock.now += 2
    for index, role in enumerate(missing, start=4):
        bindings[role] = index
    resolved = driver._resolved_indices(object(), config, 30)
    assert all(resolved[role] == bindings[role] for role in missing)
    assert len(calls) == 2


def test_explicit_reconnect_clears_both_binding_and_identity_caches(camera_setup, monkeypatch):
    driver, config, _, calls, bindings = camera_setup
    driver._resolved_indices(object(), config, 30)
    driver._identity_cache = {0: {'devicePath': 'old-camera'}}
    bindings['wrist_left'] = 4
    monkeypatch.setattr(camera_opencv, 'import_module', lambda _: object())

    def probe(candidate):
        assert driver._identity_cache is None
        return driver._resolved_indices(object(), candidate, 30)

    monkeypatch.setattr(driver, 'probe', probe)
    assert driver.reconnect(config)['wrist_left'] == 4
    assert len(calls) == 2


def test_telemetry_excludes_action_history_without_mutating_recorder_status(monkeypatch):
    monkeypatch.setenv('APPSTATION_HAL_MODE', 'real')
    config = default_config()
    config['hal']['mode'] = 'real'
    telemetry = TelemetryHub(SimpleNamespace(get_config=lambda: config))
    history = [{'deltaVector': [1.0] * 12}] * 1000
    last_action = {'deltaVector': [2.0] * 12}
    status = {'nativeStatus': {'actionHistory': history, 'lastAction': last_action, 'running': True},
              'positionMm': {'left': 1, 'right': 2}}
    try:
        frame = telemetry.next_frame(native_gripper_status=status)
        assert 'actionHistory' not in frame.gripperStatus['nativeStatus']
        assert frame.gripperStatus['nativeStatus']['running'] is True
        assert frame.gripperStatus['nativeStatus']['lastAction'] == last_action
        assert status['nativeStatus']['actionHistory'] is history
    finally:
        telemetry.shutdown()
