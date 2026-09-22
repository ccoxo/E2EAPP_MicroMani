from copy import deepcopy

import pytest

from backend.services.replay_timing import build_replay_timing
from backend.core.motion_profile import replay_motion_profile as replay_profile


def fixture_data(axis=8, distance=337.8):
    initial = [0.] * 14
    target = initial.copy()
    target[axis] = distance
    return {'fps': 30., 'rows': [
        {'frame_index': 0, 'timestamp': 0., 'action': initial, 'observation.state': initial},
        {'frame_index': 1, 'timestamp': 1 / 30, 'action': target, 'observation.state': initial},
    ]}


SELECTED = {'version': 'appstation.participation.v1', 'arms': ['right'], 'grippers': ['right']}
CONFIG = {'teleop': {'translationMaxVelocityUmS': 1000., 'rotationMaxVelocityDegS': 1.,
                    'motionProfileAccSec': .05, 'motionProfileDecSec': .05}}


@pytest.mark.parametrize('axis,distance,minimum,label', [
    (8, 337.8, .3878, '右臂 Y'), (10, 614.4, .6644, '右臂 Roll'), (8, -337.8, .3878, '右臂 Y'),
])
def test_retime_known_translation_and_rotation_segments_without_changing_data(axis, distance, minimum, label):
    data = fixture_data(axis, distance)
    original = deepcopy(data)
    result = build_replay_timing(data, CONFIG, SELECTED, .25)
    assert result['segments'][1]['durationS'] == pytest.approx(minimum)
    assert result['summary']['minimumSpeed'] < .1
    assert label in result['summary']['limitingChannels']
    assert result['summary']['limitedFrames'] == 1
    assert data == original


def test_same_timeline_uses_slowest_participating_axis_and_ignores_inactive_arm():
    data = fixture_data()
    data['rows'][1]['action'][0] = 1000000.
    data['rows'][1]['action'][10] = 1000.
    result = build_replay_timing(data, CONFIG, SELECTED, .25)
    assert result['segments'][1]['durationS'] == pytest.approx(1.05)
    assert result['summary']['limitingChannels'] == ['右臂 Roll']
    assert result['summary']['gripperFeedbackLimited'] is False


def test_small_moves_account_for_acceleration_and_stationary_frames_keep_requested_rate():
    data = fixture_data(8, 1.)
    data['fps'] = 120.
    data['rows'][1]['timestamp'] = 1 / 120
    result = build_replay_timing(data, CONFIG, SELECTED, 1.)
    assert result['segments'][0]['durationS'] == pytest.approx(1 / 120)
    assert result['segments'][1]['durationS'] == pytest.approx((.0002) ** .5)
    data['rows'][1]['action'][8] = 0.
    result = build_replay_timing(data, CONFIG, SELECTED, .25)
    assert result['summary']['plannedDurationS'] == pytest.approx(2 / 120 / .25)
    assert result['summary']['plannedAverageSpeed'] == pytest.approx(.25)
    assert result['summary']['limitedFrames'] == 0


def test_first_action_distance_is_planned_from_first_observation_and_uses_lower_configured_speed():
    data = fixture_data()
    data['rows'][0]['observation.state'] = [0.] * 14
    data['rows'][0]['observation.state'][8] = -200.
    config = deepcopy(CONFIG)
    config['teleop']['translationMaxVelocityUmS'] = 200.
    result = build_replay_timing(data, config, SELECTED, .25)
    assert result['segments'][0]['durationS'] == pytest.approx(1.05)
    assert replay_profile(config)['translationVelocityUiPerSec'] == 200.
    assert replay_profile(config)['rotationVelocityUiPerSec'] == 1.


@pytest.mark.parametrize('speed', [0, float('nan'), float('inf'), 2])
def test_invalid_requested_speed_is_rejected(speed):
    with pytest.raises(ValueError, match='倍率'):
        build_replay_timing(fixture_data(), CONFIG, SELECTED, speed)


@pytest.mark.parametrize('field,value', [('translationMaxVelocityUmS', 0), ('rotationMaxVelocityDegS', float('nan')), ('motionProfileAccSec', float('inf'))])
def test_invalid_motion_profile_is_rejected_before_execution(field, value):
    config = deepcopy(CONFIG)
    config['teleop'][field] = value
    with pytest.raises(ValueError):
        build_replay_timing(fixture_data(), config, SELECTED, .25)


def test_current_teaching_profile_has_no_extra_replay_cap_or_claim_of_hardware_validation():
    config = deepcopy(CONFIG)
    config['teleop'].update(translationMaxVelocityUmS=8000., rotationMaxVelocityDegS=12.,
                           translationStartVelocityUmS=600., rotationStartVelocityDegS=1.)
    profile = replay_profile(config)
    assert profile == {
        'translationStartVelocityUiPerSec': 600., 'translationVelocityUiPerSec': 8000.,
        'rotationStartVelocityUiPerSec': 1., 'rotationVelocityUiPerSec': 12.,
        'accTimeSec': .05, 'decTimeSec': .05,
    }
    result = build_replay_timing(fixture_data(), config, SELECTED, .25)['summary']
    assert result['limitedFrames'] == 0
    assert result['plannedAverageSpeed'] == pytest.approx(.25)
    assert result['profile'] == profile
    assert result['profileSource'] == 'teleop' and result['hardwareValidated'] is False
