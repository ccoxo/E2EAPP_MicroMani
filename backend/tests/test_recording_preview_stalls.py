from types import SimpleNamespace

from backend.core.defaults import default_config
from backend.drivers import camera_opencv
from backend.services.telemetry_hub import TelemetryHub


def test_resolved_camera_binding_does_not_expire_during_capture(monkeypatch):
    driver = camera_opencv.OpenCVCameraDriver()
    config = default_config()
    calls = []
    monkeypatch.setattr(driver, '_resolve_indices_by_identity', lambda _: calls.append(1) or {})
    monkeypatch.setattr(driver, '_camera_identities_by_index', lambda: {})
    monkeypatch.setattr(driver, '_remap_software_sources', lambda resolved, _: resolved)
    for key in camera_opencv.CAMERA_IDENTITY_KEYS.values():
        config['cameras'][key] = ''
    for i, key in enumerate(camera_opencv.CAMERA_DESCRIPTOR_KEYS.values()):
        config['cameras'][key] = f'index {i}'
    first = driver._resolved_indices(object(), config, 30)
    driver._resolved_cache_at -= 120
    assert driver._resolved_indices(object(), config, 30) == first
    assert len(calls) == 1
    driver._clear_probe_cache()
    driver._resolved_indices(object(), config, 30)
    assert len(calls) == 2
    config['cameras']['wristLeft'] = 'index 4'
    assert driver._resolved_indices(object(), config, 30)['wrist_left'] == 4
    assert len(calls) == 3


def test_telemetry_excludes_action_history_without_mutating_recorder_status():
    config = default_config()
    config['hal']['mode'] = 'real'
    telemetry = TelemetryHub(SimpleNamespace(get_config=lambda: config))
    history = [{'deltaVector': [1.0] * 12}] * 1000
    status = {'nativeStatus': {'actionHistory': history, 'running': True}, 'positionMm': {'left': 1, 'right': 2}}
    try:
        frame = telemetry.next_frame(native_gripper_status=status)
        assert 'actionHistory' not in frame.gripperStatus['nativeStatus']
        assert frame.gripperStatus['nativeStatus']['running'] is True
        assert status['nativeStatus']['actionHistory'] is history
    finally:
        telemetry.shutdown()


def test_unresolved_identity_is_retried_without_index_fallback(monkeypatch):
    driver = camera_opencv.OpenCVCameraDriver()
    config = default_config()
    for key in camera_opencv.CAMERA_IDENTITY_KEYS.values():
        config['cameras'][key] = 'missing-device'
    calls = []
    monkeypatch.setattr(driver, '_resolve_indices_by_identity', lambda _: calls.append(1) or {})
    monkeypatch.setattr(driver, '_camera_identities_by_index', lambda: {})
    monkeypatch.setattr(driver, '_remap_software_sources', lambda resolved, _: resolved)
    assert set(driver._resolved_indices(object(), config, 30).values()) == {-1}
    driver._resolved_cache_at -= 120
    driver._resolved_indices(object(), config, 30)
    assert len(calls) == 2
