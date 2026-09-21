"""示教与回放共用的软件运动参数；不代表负载下的实机验收能力。"""
import math

from backend.core.defaults import ICF_TELEOP_DEFAULTS


def teleop_motion_profile(config):
    minimums = {
        'translationStartVelocityUmS': 0., 'translationMaxVelocityUmS': 1.,
        'rotationStartVelocityDegS': 0., 'rotationMaxVelocityDegS': 1.,
        'motionProfileAccSec': .001, 'motionProfileDecSec': .001,
    }
    profile = {}
    for key, minimum in minimums.items():
        value = float(config.get('teleop', {}).get(key, ICF_TELEOP_DEFAULTS[key]))
        if not math.isfinite(value) or value < 0 or (minimum > 0 and value == 0):
            raise ValueError(f'无效的运动参数：{key}')
        profile[key] = max(minimum, value)
    return profile


def replay_motion_profile(config):
    profile = teleop_motion_profile(config)
    return {
        'translationStartVelocityUiPerSec': profile['translationStartVelocityUmS'],
        'translationVelocityUiPerSec': profile['translationMaxVelocityUmS'],
        'rotationStartVelocityUiPerSec': profile['rotationStartVelocityDegS'],
        'rotationVelocityUiPerSec': profile['rotationMaxVelocityDegS'],
        'accTimeSec': profile['motionProfileAccSec'],
        'decTimeSec': profile['motionProfileDecSec'],
    }
