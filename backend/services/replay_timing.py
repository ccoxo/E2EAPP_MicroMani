"""回放时间规划；只延长时间，不修改录制目标或执行侧保护。"""
import math

from backend.core.motion_profile import replay_motion_profile
from backend.core.participation import action_mask


def channel_label(index):
    return ('左' if index < 7 else '右') + ('夹爪' if index % 7 == 6 else '臂 ' + ('X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw')[index % 7])


def build_replay_timing(data, config, selected, speed):
    if not math.isfinite(speed) or not .1 <= speed <= 1.:
        raise ValueError('回放倍率必须在 0.1 到 1 之间')
    profile = replay_motion_profile(config)
    mask = action_mask(selected)
    period = 1. / float(data['fps'])
    requested_interval = period / speed
    ramps = profile['accTimeSec'] + profile['decTimeSec']
    previous = data['rows'][0]['observation.state']
    segments = []
    limiting = set()
    for row in data['rows']:
        times = [0.] * 14
        for index, enabled in enumerate(mask):
            if not enabled or index % 7 == 6:
                continue
            velocity = profile['translationVelocityUiPerSec'] if index % 7 < 3 else profile['rotationVelocityUiPerSec'] * 1000.
            distance = abs(row['action'][index] - previous[index])
            # 按静止到静止的梯形/三角速度段留出加减速时间，不要求每帧实际停稳。
            times[index] = distance / velocity + ramps / 2 if distance >= velocity * ramps / 2 else math.sqrt(2 * distance * ramps / velocity)
        duration = max(requested_interval, max(times))
        axes = [i for i, seconds in enumerate(times) if seconds > requested_interval and abs(seconds - duration) < 1e-9]
        limiting.update(axes)
        segments.append({'durationS': duration, 'limitingChannels': [channel_label(i) for i in axes]})
        previous = row['action']
    duration = sum(segment['durationS'] for segment in segments)
    return {'segments': segments, 'summary': {
        'profileSource': 'teleop', 'hardwareValidated': False, 'profile': profile,
        'requestedSpeed': speed, 'plannedDurationS': duration,
        'plannedAverageSpeed': len(segments) * period / duration,
        'minimumSpeed': period / max(segment['durationS'] for segment in segments),
        'limitedFrames': sum(bool(segment['limitingChannels']) for segment in segments),
        'limitingChannels': [channel_label(i) for i in sorted(limiting)],
        # 夹爪 gripSpeed 为设备档位；连续回放不因夹爪位置误差延长段时间。
        'gripperFeedbackLimited': False,
    }}
