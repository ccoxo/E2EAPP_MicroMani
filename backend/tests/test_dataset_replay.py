from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app import create_app
from backend.core.data_contract import data_contract_metadata
from backend.hal_client.protocol import command_request_policy, hal_command_payload
from backend.services.dataset_replay import validate_episode


@pytest.fixture
def setup_replay(tmp_path, monkeypatch):
    monkeypatch.setenv('APPSTATION_HAL_MODE', 'test')
    monkeypatch.setenv('APPSTATION_DISABLE_CAMERA_PROBE', '1')
    app = create_app(tmp_path)
    svc = app.state.services
    replay = app.state.replay
    replay.safety.readiness_check = None
    config = svc.settings.get_config()
    config['motion']['origin'].update(valid=True, leftValid=True, rightValid=True, leftPulse=[0.]*6, rightPulse=[0.]*6)
    config['motion']['rotationWorkLimits']['enabled'] = False
    config['teleop'].update(leftEnabledAxes=[True]*6, rightEnabledAxes=[True]*6)
    config['gripper'].update(leftEnabled=True, rightEnabled=True, icfTargetProtectionEnabled=False)
    monkeypatch.setattr(svc.settings, 'get_config', lambda: deepcopy(config))
    rows = [{'frame_index': i, 'timestamp': i / 30, 'action': [0.]*14, 'observation.state': [0.]*14} for i in range(3)]
    data = {'fps': 30, 'rows': rows, 'dataContract': data_contract_metadata(), 'episode': {
        'motionOrigin': svc.recorder._episode_motion_origin_snapshot(config),
        'motionCalibration': svc.recorder._motion_calibration_snapshot(config)}}
    monkeypatch.setattr(svc.recorder, 'load_replay_episode', lambda *args: deepcopy(data))
    monkeypatch.setattr(svc.recorder, 'status', lambda: {'active': False})
    monkeypatch.setattr(svc.teleop_mapper, 'status', lambda *args: {'running': False})
    svc.hal.health = AsyncMock(return_value=SimpleNamespace(capabilities=['replay_absolute_target_v1']))
    svc.hal.motion_state = AsyncMock(return_value={'pulses': [0.]*12})
    svc.commands.enable_motion_side = AsyncMock()
    svc.commands.stop_motion_side = AsyncMock()
    svc.commands.emergency_stop = AsyncMock()
    native = {'running': False, 'grippers': {side: {'ok': True, 'positionMm': 0., 'positionOk': True, 'positionSampleTs': time.time()*1000} for side in ('left', 'right')}}
    svc.hal.command = AsyncMock(return_value={'response': native})
    return app, replay, data, config


def test_complete_replay_sends_all_frames_and_maps_sides(setup_replay):
    app, replay, data, config = setup_replay
    # 差异小于到位容差，验证数值下发而不依赖模拟设备跟随。
    for row in data['rows']:
        row['action'][0] = 3.
        row['action'][7] = 4.
        row['action'][3] = 20.
        row['action'][6] = .1
    async def run():
        await replay.start('dataset', 'episode', 1.)
        await replay.task
        assert replay.status()['phase'] == 'completed', replay.status()
        sends = [c.args for c in app.state.hal.command.call_args_list if c.args[0] == 'motion.replay_absolute_target']
        assert len(sends) == 6
        assert sends[0][1]['side'] == 'left'
        assert sends[0][1]['deltas']['X'] == 4.
        assert sends[1][1]['deltas']['X'] == 3.
        assert sends[1][1]['deltas']['Roll'] == .02
        assert not app.state.commands.emergency_stop.called
        assert app.state.commands.stop_motion_side.await_count == 2
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['contract', 'nan', 'missing', 'sequence', 'time', 'origin', 'calibration', 'gripper'])
def test_preflight_rejects_before_motion(setup_replay, mutation):
    app, replay, data, _ = setup_replay
    if mutation == 'contract': data['dataContract'] = {}
    elif mutation == 'nan': data['rows'][0]['action'][0] = float('nan')
    elif mutation == 'missing': data['rows'][0]['action'] = []
    elif mutation == 'sequence': data['rows'][1]['frame_index'] = 0
    elif mutation == 'time': data['rows'][1]['timestamp'] = 10.
    elif mutation == 'origin': data['episode']['motionOrigin']['leftPulse'][0] = 1.
    elif mutation == 'calibration': data['episode']['motionCalibration'] = {}
    elif mutation == 'gripper': data['rows'][0]['action'][6] = 100.
    async def run():
        await replay.start('d', 'e', .25)
        await replay.task
        assert replay.status()['phase'] == 'failed'
        app.state.commands.enable_motion_side.assert_not_called()
        assert not any(c.args[0] == 'motion.replay_absolute_target' for c in app.state.hal.command.call_args_list)
    asyncio.run(run())


def test_old_hal_rejected(setup_replay):
    app, replay, *_ = setup_replay
    app.state.hal.health.return_value.capabilities = []
    async def run():
        await replay.start('d', 'e', 1.)
        await replay.task
        assert 'HAL' in replay.status()['error']
        app.state.commands.enable_motion_side.assert_not_called()
    asyncio.run(run())


def test_stop_during_loading_does_not_enable(setup_replay):
    app, replay, *_ = setup_replay
    async def run():
        await replay.start('d', 'e', 1.)
        await replay.stop()
        assert replay.status()['phase'] == 'stopped'
        app.state.commands.enable_motion_side.assert_not_called()
        app.state.commands.emergency_stop.assert_awaited_once()
    asyncio.run(run())


def test_stop_between_sides_blocks_remaining_commands(setup_replay):
    app, replay, *_ = setup_replay
    original = app.state.hal.command
    async def command(name, payload):
        if name == 'motion.replay_absolute_target':
            replay.safety.interrupt(emergency=True)
        return await original(name, payload)
    app.state.hal.command = command
    async def run():
        await replay.start('d', 'e', 1.)
        await replay.task
        assert replay.status()['phase'] == 'failed'
        sends = [c for c in original.call_args_list if c.args[0] == 'motion.replay_absolute_target']
        assert len(sends) == 1
        assert not any(c.args[0] == 'gripper.replay_target' for c in original.call_args_list)
        app.state.commands.emergency_stop.assert_awaited_once()
    asyncio.run(run())


def test_stale_feedback_stops_replay(setup_replay):
    app, replay, *_ = setup_replay
    original = replay._observe
    calls = 0
    async def observe(config):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise RuntimeError('stale DDS state')
        return await original(config)
    replay._observe = observe
    async def run():
        await replay.start('d', 'e', 1.)
        await replay.task
        assert replay.status()['phase'] == 'failed'
        assert 'stale' in replay.status()['error']
        app.state.commands.emergency_stop.assert_awaited_once()
    asyncio.run(run())


def test_absolute_command_is_flattened_and_never_retried():
    payload = hal_command_payload('motion.replay_absolute_target', {'deltas': {'X': 4., 'Roll': .1}})
    assert payload['X'] == 4.
    assert payload['Roll'] == .1
    assert command_request_policy('motion.replay_absolute_target', 1.) == (1., 1)

def test_reader_loads_complete_episode_without_preview_sampling(setup_replay, tmp_path, monkeypatch):
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq
    from backend.services.dataset_recorder import DatasetRecorderService
    app, _, data, _ = setup_replay
    root = tmp_path / 'dataset'
    (root / 'meta').mkdir(parents=True)
    (root / 'data' / 'chunk-000').mkdir(parents=True)
    (root / 'meta' / 'info.json').write_text(json.dumps({
        'format': 'lerobot-v3-native', 'fps': 30, 'dataContract': data_contract_metadata(),
    }))
    episode = {**data['episode'], 'id': 'episode_000002', 'episodeIndex': 2, 'frames': 401}
    monkeypatch.setattr(app.state.recorder, '_dataset_path', lambda _: root)
    monkeypatch.setattr(app.state.recorder, '_visible_episodes_for_dataset', lambda *_: [episode])
    rows = [{'episode_index': 2, 'frame_index': i, 'timestamp': i/30,
             'action': [float(i)]*14, 'observation.state': [0.]*14} for i in range(401)]
    pq.write_table(pa.Table.from_pylist(rows[200:]), root / 'data/chunk-000/file-000.parquet')
    pq.write_table(pa.Table.from_pylist(rows[:200] + [{**rows[0], 'episode_index': 3}]), root / 'data/chunk-000/file-001.parquet')
    loaded = DatasetRecorderService.load_replay_episode(app.state.recorder, 'dataset', episode['id'])
    validate_episode(loaded)
    assert len(loaded['rows']) == 401
    assert loaded['rows'][-1]['action'] == [400.]*14
    episode['frames'] = 402
    with pytest.raises(ValueError, match='帧数'):
        DatasetRecorderService.load_replay_episode(app.state.recorder, 'dataset', episode['id'])


def test_inspect_is_read_only_and_start_requires_confirmation_and_lease(setup_replay):
    from fastapi.testclient import TestClient
    from backend.services.control_watchdog import ControlLeaseUnavailable
    app, replay, *_ = setup_replay
    client = TestClient(app)
    assert client.post('/api/datasets/d/episodes/e/replay/inspect').status_code == 200
    app.state.hal.command.assert_not_called()
    assert client.post('/api/datasets/d/episodes/e/replay/start', json={}).status_code == 400
    def reject():
        raise ControlLeaseUnavailable('no fresh lease')
    replay.safety.readiness_check = reject
    assert client.post('/api/datasets/d/episodes/e/replay/start', json={'confirmMotion': True}).status_code == 409
    assert not replay.active


@pytest.mark.parametrize('case', ['busy', 'recording', 'native', 'feedback', 'position_stale'])
def test_execution_preconditions_reject(setup_replay, case, monkeypatch):
    app, replay, *_ = setup_replay
    if case == 'recording': monkeypatch.setattr(app.state.recorder, 'status', lambda: {'active': True})
    if case == 'native': app.state.hal.command.return_value['response']['running'] = True
    if case == 'feedback': app.state.hal.motion_state.return_value = {'pulses': []}
    if case == 'position_stale': app.state.hal.command.return_value['response']['grippers']['left']['positionSampleTs'] = 1
    async def run():
        if case == 'busy':
            with replay.safety.operation():
                await replay.start('d', 'e', 1.)
                await replay.task
        else:
            await replay.start('d', 'e', 1.)
            await replay.task
        assert replay.status()['phase'] == 'failed', replay.status()
        app.state.commands.enable_motion_side.assert_not_called()
    asyncio.run(run())


def test_alignment_uses_first_observation_then_actions(setup_replay):
    app, replay, data, _ = setup_replay
    data['rows'][0]['observation.state'][0] = 30.
    observations = [[0.]*14, [0.]*14, [30.] + [0.]*13] + [[0.]*14]*10
    replay._observe = AsyncMock(side_effect=observations)
    async def run():
        await replay.start('d', 'e', 1.)
        await replay.task
        assert replay.status()['phase'] == 'completed', replay.status()
        sends = [c.args for c in app.state.hal.command.call_args_list if c.args[0] == 'motion.replay_absolute_target']
        assert sends[1][1]['side'] == 'right'
        assert sends[1][1]['deltas']['X'] == 30.
        assert len(sends) == 8
    asyncio.run(run())


def test_cancellation_after_enable_requests_emergency_stop(setup_replay):
    app, replay, *_ = setup_replay
    async def enable(_side):
        raise asyncio.CancelledError()
    app.state.commands.enable_motion_side = enable
    async def run():
        await replay.start('d', 'e', 1.)
        await replay.task
        assert replay.status()['phase'] == 'failed'
        app.state.commands.emergency_stop.assert_awaited_once()
    asyncio.run(run())

@pytest.mark.parametrize('reason', ['lease', 'tracking', 'configuration', 'dispatch'])
def test_runtime_faults_stop_without_following_frames(setup_replay, reason):
    app, replay, data, config = setup_replay
    calls = 0
    original = replay._observe
    async def observe(current_config):
        nonlocal calls
        calls += 1
        result = await original(current_config)
        if calls == 3:
            if reason == 'lease':
                replay.safety.readiness_check = lambda: (_ for _ in ()).throw(RuntimeError('lease lost'))
            elif reason == 'tracking': result[0] = 10000.
            elif reason == 'configuration': config['teleop']['translationMaxVelocityUmS'] += 1
        return result
    replay._observe = observe
    if reason == 'dispatch':
        original_command = app.state.hal.command
        async def command(name, payload):
            if name == 'motion.replay_absolute_target': raise RuntimeError('force latch / transport fault')
            return await original_command(name, payload)
        app.state.hal.command = command
    async def run():
        await replay.start('d', 'e', 1.)
        await replay.task
        assert replay.status()['phase'] == 'failed', replay.status()
        assert replay.status()['frame'] == 0
        app.state.commands.emergency_stop.assert_awaited_once()
    asyncio.run(run())


def test_duplicate_start_rejected(setup_replay):
    app, replay, *_ = setup_replay
    async def run():
        await replay.start('d', 'e', 1.)
        with pytest.raises(RuntimeError, match='已有'):
            await replay.start('d', 'e', 1.)
        await replay.stop()
    asyncio.run(run())

def test_missed_deadline_stops_instead_of_catching_up(setup_replay, monkeypatch):
    import backend.services.dataset_replay as replay_module
    app, replay, *_ = setup_replay
    stamps = iter([0., 0., 0., 0., 1.])
    monkeypatch.setattr(replay_module, 'time', SimpleNamespace(monotonic=lambda: next(stamps), time=time.time))
    async def run():
        await replay.start('d', 'e', 1.)
        await replay.task
        assert replay.status()['phase'] == 'failed'
        assert '执行超时' in replay.status()['error']
        assert not any(c.args[0] == 'motion.replay_absolute_target' for c in app.state.hal.command.call_args_list)
        app.state.commands.emergency_stop.assert_awaited_once()
    asyncio.run(run())

def test_shutdown_continues_after_replay_stop_error(setup_replay, monkeypatch):
    from unittest.mock import Mock
    app, replay, *_ = setup_replay
    replay.stop = AsyncMock(side_effect=RuntimeError('stop unconfirmed'))
    watchdog_close = AsyncMock()
    monkeypatch.setattr(app.state.control_watchdog, 'close', watchdog_close)
    cameras_close = Mock()
    telemetry_close = Mock()
    monkeypatch.setattr(app.state.hardware.cameras, 'close_all', cameras_close)
    monkeypatch.setattr(app.state.telemetry, 'shutdown', telemetry_close)
    shutdown = next(fn for fn in app.router.on_shutdown if fn.__name__ == 'shutdown_runtime_services')
    asyncio.run(shutdown())
    watchdog_close.assert_awaited_once()
    cameras_close.assert_called_once()
    telemetry_close.assert_called_once()
