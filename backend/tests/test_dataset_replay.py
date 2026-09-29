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
    from backend.services import dataset_replay as replay_module
    clock = SimpleNamespace(now=0.)
    async def advance(seconds):
        clock.now += max(0., seconds)
        await asyncio.sleep(0)
    monkeypatch.setattr(replay_module, 'time', SimpleNamespace(monotonic=lambda: clock.now, time=time.time))
    monkeypatch.setattr(replay_module, 'asyncio', SimpleNamespace(
        Event=asyncio.Event, Task=asyncio.Task, create_task=asyncio.create_task, to_thread=asyncio.to_thread,
        CancelledError=asyncio.CancelledError, sleep=advance))
    monkeypatch.setenv('APPSTATION_HAL_MODE', 'test')
    monkeypatch.setenv('APPSTATION_DISABLE_CAMERA_PROBE', '1')
    app = create_app(tmp_path)
    svc = app.state.services
    replay = app.state.replay
    replay._test_clock = clock
    replay.safety.readiness_check = None
    config = svc.settings.get_config()
    config['motion']['origin'].update(valid=True, leftValid=True, rightValid=True, leftPulse=[0.]*6, rightPulse=[0.]*6)
    config['motion']['rotationWorkLimits']['enabled'] = False
    config['teleop'].update(leftEnabledAxes=[True]*6, rightEnabledAxes=[True]*6)
    config['gripper'].update(leftEnabled=True, rightEnabled=True, icfTargetProtectionEnabled=False)
    monkeypatch.setattr(svc.settings, 'get_config', lambda: deepcopy(config))
    rows = [{'frame_index': i, 'timestamp': i / 30, 'action': [0.]*14, 'observation.state': [0.]*14} for i in range(3)]
    data = {'fps': 30, 'rows': rows, 'dataContract': data_contract_metadata(), 'episode': {
        'participation': {'version': 'appstation.participation.v1', 'arms': ['left', 'right'], 'grippers': ['left', 'right']},
        'motionOrigin': svc.recorder._episode_motion_origin_snapshot(config),
        'motionCalibration': svc.recorder._motion_calibration_snapshot(config)}}
    monkeypatch.setattr(svc.recorder, 'load_replay_episode', lambda *args: deepcopy(data))
    monkeypatch.setattr(svc.recorder, 'status', lambda: {'active': False})
    monkeypatch.setattr(svc.teleop_mapper, 'status', lambda *args: {'running': False})
    svc.hal.health = AsyncMock(return_value=SimpleNamespace(capabilities=['replay_absolute_target_v1', 'record_participation_v1']))
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
        native = app.state.services.teleop_mapper._native_payload(config)
        for replay_key, teleop_key in (
            ('translationVelocityUiPerSec', 'translationMaxVelocityUmS'),
            ('rotationVelocityUiPerSec', 'rotationMaxVelocityDegS'),
            ('translationStartVelocityUiPerSec', 'translationStartVelocityUmS'),
            ('rotationStartVelocityUiPerSec', 'rotationStartVelocityDegS'),
            ('accTimeSec', 'motionProfileAccSec'), ('decTimeSec', 'motionProfileDecSec'),
        ):
            assert sends[0][1][replay_key] == native[teleop_key]
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


def test_replay_resolves_native_episode_by_frame_range_when_sidecar_index_has_gaps(tmp_path, monkeypatch):
    import json
    import pyarrow as pa
    import pyarrow.parquet as pq
    from backend.services.dataset_recorder import DatasetRecorderService

    root = tmp_path / 'dataset'
    (root / 'meta' / 'episodes' / 'chunk-000').mkdir(parents=True)
    (root / 'data' / 'chunk-000').mkdir(parents=True)
    (root / 'meta' / 'info.json').write_text(json.dumps({
        'format': 'lerobot-v3-native', 'fps': 30, 'total_episodes': 3,
        'dataContract': data_contract_metadata(),
    }))
    episode = {
        'id': 'episode_000000', 'episodeIndex': 0, 'frames': 4,
        'datasetFromIndex': 5, 'datasetToIndex': 9, 'status': 'review',
    }
    (root / 'meta' / 'episodes.jsonl').write_text(json.dumps(episode) + '\n')
    native_meta = []
    offset = 0
    for native_index, frames in enumerate((2, 3, 4)):
        rows = [
            {'episode_index': native_index, 'frame_index': frame, 'timestamp': frame / 30,
             'action': [float(native_index)] * 14, 'observation.state': [0.] * 14}
            for frame in range(frames)
        ]
        pq.write_table(pa.Table.from_pylist(rows), root / 'data' / 'chunk-000' / f'file-{native_index:03d}.parquet')
        native_meta.append({
            'episode_index': native_index, 'dataset_from_index': offset,
            'dataset_to_index': offset + frames, 'length': frames, 'tasks': ['task'],
        })
        offset += frames
    pq.write_table(pa.Table.from_pylist(native_meta), root / 'meta' / 'episodes' / 'chunk-000' / 'file-000.parquet')

    recorder = object.__new__(DatasetRecorderService)
    monkeypatch.setattr(recorder, '_dataset_path', lambda _dataset_id: root)
    assert recorder._next_episode_index(root) == 3
    info_path = root / 'meta' / 'info.json'
    info = json.loads(info_path.read_text())
    info['total_episodes'] = 5
    info_path.write_text(json.dumps(info))
    assert recorder._next_episode_index(root) == 5
    loaded = recorder.load_replay_episode('dataset', episode['id'])
    validate_episode(loaded)
    assert len(loaded['rows']) == 4
    assert loaded['rows'][0]['action'] == [2.] * 14

    monkeypatch.setattr(recorder, '_native_video_row_to_jpeg',
                        lambda _root, _episode, _camera, _frame, row: str(row['episode_index']).encode())
    assert recorder._native_video_frame_to_jpeg(root, episode, 'global', 0) == b'2'
    legacy_episode = {**episode, 'episodeIndex': 1, 'frames': 3}
    legacy_episode.pop('datasetFromIndex')
    legacy_episode.pop('datasetToIndex')
    assert recorder._native_video_frame_to_jpeg(root, legacy_episode, 'global', 0) == b'1'

    episode['datasetFromIndex'] = 6
    episode['datasetToIndex'] = 10
    (root / 'meta' / 'episodes.jsonl').write_text(json.dumps(episode) + '\n')
    with pytest.raises(ValueError, match='原生数据'):
        recorder.load_replay_episode('dataset', episode['id'])
    with pytest.raises(FileNotFoundError, match='video mapping'):
        recorder._native_video_frame_to_jpeg(root, episode, 'global', 0)


def test_inspect_is_read_only_and_start_requires_confirmation_and_lease(setup_replay):
    from fastapi.testclient import TestClient
    from backend.services.control_watchdog import ControlLeaseUnavailable
    app, replay, *_ = setup_replay
    client = TestClient(app)
    assert client.post('/api/datasets/d/episodes/e/replay/inspect').status_code == 200
    planned = client.post('/api/datasets/d/episodes/e/replay/inspect', json={'speed': .5}).json()['data']['timing']
    assert planned['requestedSpeed'] == .5
    assert planned['plannedDurationS'] == pytest.approx(.2)
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


@pytest.mark.parametrize('hardware_side,operator_label', [('left', '右侧'), ('right', '左侧')])
def test_disabled_gripper_reports_operator_side_before_sampling(setup_replay, hardware_side, operator_label):
    app, replay, _, config = setup_replay
    config['gripper'][f'{hardware_side}Enabled'] = False
    async def run():
        await replay.start('d', 'e', 1.)
        await replay.task
        assert f'操作者{operator_label}夹爪未启用' in replay.status()['error']
        assert '反馈' not in replay.status()['error']
        app.state.hal.command.assert_not_called()
        app.state.commands.enable_motion_side.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize('hardware_side,operator_label', [('left', '右侧'), ('right', '左侧')])
@pytest.mark.parametrize('patch,reason', [
    ({'positionOk': False}, '读取失败'),
    ({'ok': False}, '读取失败'),
    ({'positionSampleTs': 1}, '超时'),
    ({'positionSampleTs': None}, '时间戳无效'),
    ({'positionSampleTs': 'bad'}, '时间戳无效'),
    ({'positionSampleTs': float('nan')}, '时间戳无效'),
    ({'positionSampleTs': float('inf')}, '时间戳无效'),
    ({'positionSampleTs': time.time()*1000 + 3600000}, '时间戳无效'),
    ({'positionMm': None}, '位置数值异常'),
    ({'positionMm': 'bad'}, '位置数值异常'),
    ({'positionMm': float('nan')}, '位置数值异常'),
    ({'positionMm': -1}, '位置数值异常'),
])
def test_gripper_feedback_reports_specific_reason(setup_replay, hardware_side, operator_label, patch, reason):
    app, replay, data, config = setup_replay
    replay._participation = data['episode']['participation']
    app.state.hal.command.return_value['response']['grippers'][hardware_side].update(patch)
    async def run():
        with pytest.raises(RuntimeError, match=f'操作者{operator_label}夹爪反馈.*{reason}'):
            await replay._observe(config)
        app.state.commands.enable_motion_side.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize('age_ms,accepted', [(0, True), (1000, True), (1001, False)])
def test_gripper_feedback_freshness_boundary(setup_replay, monkeypatch, age_ms, accepted):
    app, replay, data, config = setup_replay
    replay._participation = data['episode']['participation']
    now = time.time()
    monkeypatch.setattr('backend.services.dataset_replay.time.time', lambda: now)
    for detail in app.state.hal.command.return_value['response']['grippers'].values():
        detail['positionSampleTs'] = now*1000 - age_ms
    async def run():
        if accepted:
            assert await replay._observe(config) == [0.]*14
        else:
            with pytest.raises(RuntimeError, match='反馈超时'):
                await replay._observe(config)
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
        if reason == 'tracking':
            assert replay.status()['trackingFault']['targetKind'] == 'observation.state'
            assert replay.status()['trackingFault']['targetFrameIndex'] == 0
            assert replay.status()['trackingFault']['targetCommandCompletedAtMs'] is None
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
    async def stalled_sleep(seconds):
        replay._test_clock.now += seconds + 1.
        await asyncio.sleep(0)
    monkeypatch.setattr(replay_module.asyncio, 'sleep', stalled_sleep)
    async def run():
        await replay.start('d', 'e', 1.)
        await replay.task
        assert replay.status()['phase'] == 'failed'
        assert '执行超时' in replay.status()['error']
        assert replay.status()['frame'] == 1
        moves = [c for c in app.state.hal.command.call_args_list if c.args[0] == 'motion.replay_absolute_target']
        assert len(moves) == 2  # 仅第一帧双臂命令，调度停顿后不继续下一帧。
        app.state.commands.emergency_stop.assert_awaited_once()
    asyncio.run(run())

@pytest.mark.parametrize('wake_delay, feedback_delay', [(1., 0.), (0., 1.), (.15, .15)])
def test_replay_timeout_identifies_wakeup_and_feedback_delays(setup_replay, monkeypatch, wake_delay, feedback_delay):
    import backend.services.dataset_replay as replay_module
    app, replay, *_ = setup_replay
    original_observe = replay._observe
    in_segment = False

    async def delayed_sleep(seconds):
        nonlocal in_segment
        in_segment = True
        replay._test_clock.now += seconds + wake_delay
        await asyncio.sleep(0)

    async def delayed_observe(config):
        observed = await original_observe(config)
        if in_segment:
            replay._test_clock.now += feedback_delay
        return observed

    monkeypatch.setattr(replay_module.asyncio, 'sleep', delayed_sleep)
    monkeypatch.setattr(replay, '_observe', delayed_observe)

    async def run():
        await replay.start('d', 'e', 1.)
        await replay.task
        error = replay.status()['error']
        assert '执行超时' in error
        assert '第 1 帧' in error
        assert f'唤醒延迟 {wake_delay * 1000:.1f} ms' in error
        assert f'反馈读取 {feedback_delay * 1000:.1f} ms' in error
        assert '阈值 250.0 ms' in error
        assert replay.status()['frame'] == 1
        app.state.commands.emergency_stop.assert_awaited_once()
    asyncio.run(run())


def test_replay_can_run_longer_than_sixty_seconds(setup_replay):
    app, replay, data, _ = setup_replay
    data['rows'] = [dict(deepcopy(data['rows'][0]), frame_index=i, timestamp=i / 30) for i in range(460)]

    async def run():
        await replay.start('d', 'e', .25)
        await replay.task
        assert replay.status()['phase'] == 'completed', replay.status()
        assert replay.status()['elapsedS'] > 60
        assert replay.status()['completedFrames'] == 460
        app.state.commands.emergency_stop.assert_not_awaited()
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


@pytest.mark.parametrize("operator_side,hardware_side", [("left", "right"), ("right", "left")])
@pytest.mark.parametrize("use_gripper", [False, True])
def test_single_arm_never_commands_inactive_side(setup_replay, operator_side, hardware_side, use_gripper):
    app, replay, data, config = setup_replay
    data["episode"]["participation"] = {"version": "appstation.participation.v1", "arms": [operator_side], "grippers": [operator_side] if use_gripper else []}
    inactive = "left" if hardware_side == "right" else "right"
    config["gripper"][f"{inactive}Enabled"] = False
    config["motion"]["origin"][f"{inactive}Valid"] = False
    native = app.state.hal.command.return_value["response"]
    native["grippers"].pop(inactive)
    if not use_gripper:
        config["gripper"][f"{hardware_side}Enabled"] = False
        native["grippers"].clear()
    async def run():
        await replay.inspect("d", "e")
        await replay.start("d", "e", 1.)
        await replay.task
        assert replay.status()["phase"] == "completed", replay.status()
        app.state.commands.enable_motion_side.assert_awaited_once_with(hardware_side)
        app.state.commands.stop_motion_side.assert_awaited_once_with(hardware_side)
        commands = [c.args for c in app.state.hal.command.call_args_list]
        moves = [payload for name, payload in commands if name == "motion.replay_absolute_target"]
        assert len(moves) == 3 and all(p["side"] == hardware_side for p in moves)
        grips = [payload for name, payload in commands if name == "gripper.replay_target"]
        assert len(grips) == (3 if use_gripper else 0)
        assert all(p["side"] == hardware_side for p in grips)
        if use_gripper:
            prepare = next(p for name, p in commands if name == "gripper.prepare_replay")
            assert prepare[f"{hardware_side}GripperParticipating"] is True
            assert prepare[f"{inactive}GripperParticipating"] is False
        assert any(name == "gripper.prepare_replay" for name, _ in commands) == use_gripper
    asyncio.run(run())


def test_legacy_episode_requires_explicit_participation(setup_replay):
    app, replay, data, _ = setup_replay
    data["episode"].pop("participation")
    async def run():
        with pytest.raises(ValueError, match="参与侧"):
            await replay.inspect("d", "e")
        selected = {"version": "appstation.participation.v1", "arms": ["left"], "grippers": []}
        result = await replay.inspect("d", "e", selected)
        assert result["participation"] == selected
        app.state.hal.command.assert_not_called()
        await replay.start("d", "e", 1., selected)
        await replay.task
        assert replay.status()["phase"] == "completed"
    asyncio.run(run())


def test_recorded_participation_cannot_be_overridden(setup_replay):
    app, replay, _, _ = setup_replay
    async def run():
        selected = {"version": "appstation.participation.v1", "arms": ["left"], "grippers": []}
        with pytest.raises(ValueError, match="不一致"):
            await replay.inspect("d", "e", selected)
        await replay.start("d", "e", 1., selected)
        await replay.task
        assert replay.status()["phase"] == "failed"
        app.state.commands.enable_motion_side.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize('phase,label', [('aligning', '起点对齐'), ('settling', '末帧到位')])
def test_alignment_timeout_reports_only_participating_channels_with_ui_units(setup_replay, monkeypatch, phase, label):
    from backend.services import dataset_replay as module
    _, replay, _, config = setup_replay
    replay._participation = {'version': 'appstation.participation.v1', 'arms': ['right'], 'grippers': ['right']}
    replay._status['phase'] = phase
    target = [0.] * 14
    target[0] = 999.  # 未参与左臂不应出现在诊断中。
    target[7], target[10], target[13] = 100., 200., 5.
    observed = [0.] * 14
    observed[7], observed[10], observed[13] = 75., 100., 4.
    replay._observe = AsyncMock(return_value=observed)
    replay._check = lambda token: None
    replay._send = AsyncMock()
    times = iter([0., 31.])
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: next(times)))
    async def run():
        with pytest.raises(RuntimeError) as caught:
            await replay._settle(target, config, None)
        message = str(caught.value)
        assert message.startswith('目标位置对齐超时')
        assert label in message
        assert '操作者右臂（硬件left）X' in message
        assert '目标 100 μm，实际 75 μm，误差 25 μm，允许 10 μm' in message
        assert 'Roll：目标 0.2 °，实际 0.1 °，误差 0.1 °，允许 0.05 °' in message
        assert ('操作者右夹爪（硬件left）开口：目标 5 mm，实际 4 mm，误差 1 mm，允许 0.2 mm' in message) == (phase == 'aligning')
        assert '操作者左' not in message
        assert 'Pitch' not in message
        assert replay.status()['trackingError'][0] == 0
        assert replay.status()['trackingError'][7] == 25
        replay._send.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('hardware_side,operator_side,data_offset,pulse_offset', [
    ('left', 'right', 7, 0), ('right', 'left', 0, 6),
])
def test_replay_nonzero_origin_uses_um_for_translation_and_mdeg_for_rotation(
    setup_replay, hardware_side, operator_side, data_offset, pulse_offset,
):
    app, replay, data, config = setup_replay
    # 日志中的非零原点和毫米标定；旋转选用整脉冲目标，避免量化影响断言。
    config['motion']['kinematics'][f'{hardware_side}SignedPulsePerUnit'] = [-5000., 5000., -10000., 2000., -2500., 500.]
    config['motion']['origin'][f'{hardware_side}Pulse'] = [-50002., -200002., -101., 100000., -2., -521.]
    selected = {'version': 'appstation.participation.v1', 'arms': [operator_side], 'grippers': []}
    replay._participation = selected
    target = [0.] * 14
    target[data_offset:data_offset+6] = [15., 102.2, -17., 200., -300., 500.]
    targets = replay._targets(target, config)
    assert targets[hardware_side][0] == pytest.approx([10015.4, -39898.2, -6.9, 50.2, -.2992, -.542])
    pulses = [0.] * 12
    pulses[pulse_offset:pulse_offset+6] = [-50077., -199491., 69., 100400., 748., -271.]
    app.state.hal.motion_state = AsyncMock(return_value={'pulses': pulses})
    async def run():
        assert await replay._observe(config) == pytest.approx(target)
        data['episode']['participation'] = selected
        data['episode']['motionOrigin'] = app.state.services.recorder._episode_motion_origin_snapshot(config)
        data['episode']['motionCalibration'] = app.state.services.recorder._motion_calibration_snapshot(config)
        for row in data['rows']:
            row['observation.state'] = list(target)
            row['action'] = list(target)
        await replay.start('d', 'e', 1.)
        await replay.task
        assert replay.status()['phase'] == 'completed', replay.status()
        app.state.commands.emergency_stop.assert_not_awaited()
    asyncio.run(run())


def test_replay_feedback_does_not_hide_millimetre_motion_as_micrometres(setup_replay):
    app, replay, _, config = setup_replay
    replay._participation = {'version': 'appstation.participation.v1', 'arms': ['right'], 'grippers': []}
    config['motion']['kinematics']['leftSignedPulsePerUnit'] = [-5000., 5000., -10000., 2000., -2500., 500.]
    config['motion']['origin']['leftPulse'] = [-50002., -200002., -101., 0., 0., 0.]
    app.state.hal.motion_state = AsyncMock(return_value={'pulses': [-125., -50171., 170.] + [0.] * 9})
    async def run():
        observed = await replay._observe(config)
        assert observed[7:10] == pytest.approx([-9975.4, 29966.2, -27.1])
    asyncio.run(run())


@pytest.mark.parametrize('operator_side,hardware_side,offset', [('left', 'right', 0), ('right', 'left', 7)])
@pytest.mark.parametrize('axis,limit,scale,unit', [(0, 500., 1., 'μm'), (3, 1000., 1000., '°')])
def test_tracking_fault_keeps_trigger_sample_before_stop_and_logs_after_stop(setup_replay, monkeypatch, operator_side, hardware_side, offset, axis, limit, scale, unit):
    import json
    from unittest.mock import Mock
    app, replay, data, _ = setup_replay
    data['episode']['participation'] = {'version': 'appstation.participation.v1', 'arms': [operator_side], 'grippers': [operator_side]}
    for row, factor in zip(data['rows'], [.25, 1.3, .1]):
        row['action'][offset + axis] = limit * factor
    motion_ts = time.time() * 1000 - 100
    gripper_ts = motion_ts + 50
    app.state.hal.motion_state.return_value['timestamp_ms'] = motion_ts
    app.state.hal.command.return_value['response']['grippers'][hardware_side]['positionSampleTs'] = gripper_ts
    logger = Mock()
    monkeypatch.setattr(app.state.services.logs, 'error', logger)
    stop_snapshots = []
    async def emergency_stop():
        stop_snapshots.append(deepcopy(replay.status()))
        assert not logger.called
        # 停机后的反馈变化不得覆盖故障现场。
        app.state.hal.motion_state.return_value['pulses'] = [999999.] * 12
    app.state.commands.emergency_stop = AsyncMock(side_effect=emergency_stop)
    async def run():
        await replay.start('dataset', 'episode', 1.)
        await replay.task
        status = replay.status()
        assert status['phase'] == 'failed' and status['frame'] == 2
        assert status['trackingError'][offset + axis] == pytest.approx(limit * 1.3)
        fault = status['trackingFault']
        assert stop_snapshots[0]['trackingFault'] == fault
        assert (fault['datasetId'], fault['episodeId']) == ('dataset', 'episode')
        assert (fault['targetFrameIndex'], fault['nextFrameIndex'], fault['targetKind']) == (1, 2, 'action')
        assert fault['targetTimestampS'] == pytest.approx(1 / 30)
        assert fault['targetCommandCompletedAtMs'] <= fault['readStartedAtMs'] <= fault['checkedAtMs']
        assert len(fault['channels']) == 6
        assert all(c['operatorSide'] == operator_side and c['hardwareSide'] == hardware_side for c in fault['channels'])
        exceeded = [c for c in fault['channels'] if c['exceeded']]
        assert len(exceeded) == 1
        channel = exceeded[0]
        assert channel['index'] == offset + axis and channel['unit'] == unit
        assert channel['target'] == pytest.approx(limit * 1.3 / scale)
        assert channel['observed'] == 0
        assert channel['error'] == pytest.approx(limit * 1.3 / scale)
        assert channel['limit'] == limit / scale
        assert channel['sampleTs'] == motion_ts
        assert channel['sampleAgeMs'] == pytest.approx(fault['checkedAtMs'] - channel['sampleTs'])
        assert '第 2 帧 action' in status['error'] and '准备下发第 3 帧' in status['error']
        assert '超限' in status['error'] and '反馈时间' in status['error']
        moves = [c for c in app.state.hal.command.call_args_list if c.args[0] == 'motion.replay_absolute_target']
        # 允许在段内续发同一绝对目标，但不能提前发送第三帧。
        values = [c.args[1]['deltas'][('X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw')[axis]] for c in moves]
        assert set(values) == {limit * .25 / scale, limit * 1.3 / scale}
        app.state.commands.emergency_stop.assert_awaited_once()
        structured = [c.args[1] for c in logger.call_args_list if 'event=replay_tracking_fault ' in c.args[1]]
        assert len(structured) == 1
        assert json.loads(structured[0].split('event=replay_tracking_fault ', 1)[1]) == fault
        # 新任务不沿用上次诊断。
        app.state.hal.motion_state.return_value['pulses'] = [0.] * 12
        for row in data['rows']:
            row['action'] = [0.] * 14
        await replay.start('dataset', 'next_episode', 1.)
        assert 'trackingFault' not in replay.status()
        await replay.task
        assert replay.status()['phase'] == 'completed'
    asyncio.run(run())


def test_tracking_fault_without_source_time_does_not_invent_sample_timestamp(setup_replay):
    app, replay, data, _ = setup_replay
    data['episode']['participation'] = {'version': 'appstation.participation.v1', 'arms': ['right'], 'grippers': []}
    data['rows'][0]['action'][10] = 1300.
    async def run():
        await replay.start('d', 'e', 1.)
        await replay.task
        fault = replay.status()['trackingFault']
        assert all(c['sampleTs'] is None and c['sampleAgeMs'] is None for c in fault['channels'])
        assert '反馈时间 未提供' in replay.status()['error']
        assert fault['checkedAtMs'] >= fault['readStartedAtMs'] > 0
        app.state.commands.emergency_stop.assert_awaited_once()
    asyncio.run(run())


def test_inspect_plans_selected_speed_without_motion(setup_replay):
    app, replay, data, config = setup_replay
    config['teleop'].update(translationMaxVelocityUmS=1000., rotationMaxVelocityDegS=1.)
    data['rows'][1]['action'][8] = 337.8
    async def run():
        result = await replay.inspect('d', 'e', speed=.25)
        assert result['timing']['minimumSpeed'] < .1
        assert '右臂 Y' in result['timing']['limitingChannels']
        assert result['timing']['plannedDurationS'] > result['durationS'] / .25
        app.state.hal.command.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize('following_speed', [1000., 100.])
def test_retimed_replay_follows_feedback_and_shifts_future_frames(setup_replay, following_speed):
    app, replay, data, config = setup_replay
    config['teleop'].update(translationMaxVelocityUmS=1000., rotationMaxVelocityDegS=1.)
    data['episode']['participation'] = {'version': 'appstation.participation.v1', 'arms': ['right'], 'grippers': []}
    for row, value in zip(data['rows'], [400., 410., 420.]):
        row['action'][8] = value
    clock = replay._test_clock
    actual = 0.
    target = 0.
    last_read = 0.
    sends = []
    original_send = replay._send
    async def send(action, config, token, **kwargs):
        nonlocal target
        await original_send(action, config, token, **kwargs)
        target = action[8]
        sends.append((clock.now, target))
    async def observe(_config):
        nonlocal actual, last_read
        delta = target - actual
        actual += min(abs(delta), following_speed * (clock.now - last_read)) * (1 if delta >= 0 else -1)
        last_read = clock.now
        result = [0.] * 14
        result[8] = actual
        return result
    replay._send = send
    replay._observe = observe
    async def run():
        await replay.start('d', 'e', .25)
        await replay.task
        status = replay.status()
        assert status['phase'] == 'completed', status
        assert status['completedFrames'] == 3
        assert status['effectiveSpeed'] <= .25
        assert status['elapsedS'] >= status['timing']['plannedDurationS']
        first_times = {value: next(t for t, v in sends if v == value) for value in (400., 410., 420.)}
        assert first_times[410.] - first_times[400.] >= .45 - 1e-9
        assert first_times[420.] - first_times[410.] >= 1 / 30 / .25 - 1e-9
        if following_speed == 100.:
            assert status['feedbackWaitS'] >= 1.
            assert first_times[410.] > 1.4
        assert abs(actual - 420.) <= 10.
        app.state.commands.emergency_stop.assert_not_called()
        moves = [c.args[1] for c in app.state.hal.command.call_args_list if c.args[0] == 'motion.replay_absolute_target']
        assert all(p['translationVelocityUiPerSec'] == 1000. and p['rotationVelocityUiPerSec'] == 1. for p in moves)
    asyncio.run(run())


def test_stalled_feedback_stops_after_bounded_wait_without_advancing(setup_replay):
    app, replay, data, _ = setup_replay
    data['episode']['participation'] = {'version': 'appstation.participation.v1', 'arms': ['right'], 'grippers': []}
    for row in data['rows']:
        row['action'][8] = 400.
    async def run():
        await replay.start('d', 'e', .25)
        await replay.task
        status = replay.status()
        assert '跟随等待超时' in status['error'], status
        assert status['frame'] == 1
        assert status['trackingFault']['channels'][1]['error'] == 400.
        assert status['trackingFault']['channels'][1]['waiting'] is True
        assert '下一帧放行阈值 250 μm' in status['error']
        assert replay._test_clock.now < 3.
        app.state.commands.emergency_stop.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['stale', 'stop'])
def test_long_retimed_segment_checks_feedback_and_stop_without_waiting_for_deadline(setup_replay, failure):
    app, replay, data, _ = setup_replay
    data['episode']['participation'] = {'version': 'appstation.participation.v1', 'arms': ['right'], 'grippers': []}
    data['rows'][0]['action'][8] = 5000.
    original = replay._observe
    async def observe(config):
        if replay._test_clock.now >= .2:
            if failure == 'stale':
                raise RuntimeError('stale DDS state')
            replay.safety.interrupt(emergency=True)
        return await original(config)
    replay._observe = observe
    async def run():
        await replay.start('d', 'e', .25)
        await replay.task
        assert replay.status()['phase'] == 'failed'
        assert replay._test_clock.now < .3
        assert replay.status()['frame'] == 1
        app.state.commands.emergency_stop.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['configuration', 'excursion', 'dispatch'])
def test_long_segment_rejects_config_change_excursion_or_failed_refresh(setup_replay, failure):
    app, replay, data, config = setup_replay
    data['episode']['participation'] = {'version': 'appstation.participation.v1', 'arms': ['right'], 'grippers': []}
    data['rows'][0]['action'][8] = 5000.
    original = replay._observe
    async def observe(current):
        result = await original(current)
        if replay._test_clock.now >= .05:
            if failure == 'configuration': config['teleop']['motionProfileAccSec'] = .1
            if failure == 'excursion': result[8] = -600.
        return result
    replay._observe = observe
    original_command = app.state.hal.command
    moves = []
    async def command(name, payload):
        if name == 'motion.replay_absolute_target':
            moves.append(payload)
            if failure == 'dispatch' and len(moves) == 2:
                raise RuntimeError('DDS reply lost')
        return await original_command(name, payload)
    app.state.hal.command = command
    async def run():
        await replay.start('d', 'e', .25)
        await replay.task
        status = replay.status()
        assert status['phase'] == 'failed' and status['frame'] == 1
        assert replay._test_clock.now <= .1
        if failure == 'excursion':
            assert status['trackingFault']['targetKind'] == 'segment.bounds'
        assert len(moves) == (2 if failure == 'dispatch' else 1)
        app.state.commands.emergency_stop.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize('field,value', [('translationMaxVelocityUmS', 0), ('motionProfileDecSec', float('nan'))])
def test_invalid_profile_rejected_before_enable(setup_replay, field, value):
    app, replay, _, config = setup_replay
    config['teleop'][field] = value
    async def run():
        await replay.start('d', 'e', .25)
        await replay.task
        assert '无效的运动参数' in replay.status()['error']
        app.state.commands.enable_motion_side.assert_not_awaited()
        app.state.hal.command.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize('speed', [None, [], 'bad', 0, 2])
def test_inspect_rejects_invalid_speed_without_motion(setup_replay, speed):
    from fastapi.testclient import TestClient
    app, replay, *_ = setup_replay
    response = TestClient(app).post('/api/datasets/d/episodes/e/replay/inspect', json={'speed': speed})
    assert response.status_code == 409
    app.state.hal.command.assert_not_awaited()
    app.state.commands.enable_motion_side.assert_not_awaited()

@pytest.mark.parametrize('offset', [6, 13])
@pytest.mark.parametrize('gap', [1.5, 3.3])
def test_gripper_error_does_not_block_replay_and_final_error_is_warning(setup_replay, offset, gap):
    app, replay, data, _ = setup_replay
    for row in data['rows']:
        row['action'][offset] = gap
    async def run():
        await replay.start('d', 'e', 1.)
        await replay.task
        status = replay.status()
        assert status['phase'] == 'completed', status
        assert status['completedFrames'] == len(data['rows'])
        assert status['feedbackWaitS'] == 0
        assert status['trackingError'][offset] == gap
        assert '末帧夹爪未到位' in status['warning']
        assert ('左夹爪' if offset == 6 else '右夹爪') in status['warning']
        assert 'trackingFault' not in status
        app.state.commands.emergency_stop.assert_not_awaited()
        sends = [c for c in app.state.hal.command.call_args_list if c.args[0] == 'gripper.replay_target']
        assert len(sends) == 2 * len(data['rows'])
        for row in data['rows']:
            row['action'][offset] = 0.
        await replay.start('d', 'e', 1.)
        await replay.task
        assert 'warning' not in replay.status()
    asyncio.run(run())


def test_gripper_initial_alignment_still_required(setup_replay):
    app, replay, data, _ = setup_replay
    data['rows'][0]['observation.state'][6] = 3.
    async def run():
        await replay.start('d', 'e', 1.)
        await replay.task
        assert replay.status()['phase'] == 'failed'
        assert '起点对齐' in replay.status()['error']
        assert '夹爪' in replay.status()['error']
        app.state.commands.emergency_stop.assert_awaited_once()
    asyncio.run(run())
