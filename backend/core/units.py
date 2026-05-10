ROTATION_AXES = {3, 4, 5, 9, 10, 11}
LEFT_PULSE_PER_UNIT = (-9000.0, -10000.0, -10000.0, 1666.666667, 2500.0, 3333.333333)
RIGHT_PULSE_PER_UNIT = (-4878.0487804878, 10000.0, -1923.07692307692, 1666.666667, -2500.0, -3333.333333)
MOTION_PULSE_PER_UNIT = LEFT_PULSE_PER_UNIT + RIGHT_PULSE_PER_UNIT


def pulse_to_lerobot(pulse: float, axis_idx: int, pulse_per_unit: float) -> float:
    """Convert pulse count to LeRobot observation.state units: um or 0.001 degree."""
    return pulse / pulse_per_unit * 1000.0


def pulse_to_ui(pulse: float, axis_idx: int, pulse_per_unit: float) -> float:
    """Convert pulse count to frontend TelemetryFrame units: um or degree."""
    value = pulse_to_lerobot(pulse, axis_idx, pulse_per_unit)
    if axis_idx in ROTATION_AXES:
        return value / 1000.0
    return value


def ui_to_lerobot_state(values: list[float]) -> list[float]:
    """Convert UI joint units to LeRobot state units: um and 0.001 degree."""
    return [float(value) * 1000.0 if idx in ROTATION_AXES else float(value) for idx, value in enumerate(values)]


def lerobot_to_ui_state(values: list[float]) -> list[float]:
    """Convert LeRobot state units back to UI units: um and degree."""
    return [float(value) / 1000.0 if idx in ROTATION_AXES else float(value) for idx, value in enumerate(values)]


def pulses_to_ui_state(pulses: list[float]) -> list[float]:
    """Convert signed LTDMC pulse counts to frontend units for all 12 axes."""
    values = (list(pulses) + [0.0] * 12)[:12]
    return [
        pulse_to_ui(float(pulse), idx, MOTION_PULSE_PER_UNIT[idx])
        for idx, pulse in enumerate(values)
    ]
