import pytest

from backend.services.policy_gripper_feedback import GripperIncrementTracker, gripper_target_reached


def feedback(**changes):
    return {"positionOk": True, "positionMm": 26 * (1 - 155 / 255),
            "positionSampleTs": 1100, "lastCommandTs": 1050, **changes}


def test_at_most_one_encoder_count_is_allowed():
    assert gripper_target_reached(feedback(), target_mm=10.1586, stroke_mm=26, sent_at_ms=1000, now_ms=1200)
    assert gripper_target_reached(feedback(positionMm=10.298), target_mm=10.1586, stroke_mm=26, sent_at_ms=1000, now_ms=1200)
    assert not gripper_target_reached(feedback(positionMm=10.4), target_mm=10.1586, stroke_mm=26, sent_at_ms=1000, now_ms=1200)


def test_small_increments_overcome_one_count_lag_without_exceeding_step():
    tracker = GripperIncrementTracker()
    position = 26 * (1 - 155 / 255)
    initial = position
    for _ in range(20):
        target = tracker.next_target(position, -0.075)
        assert 0 <= position - target <= .200001
        raw = int((26 - target) / 26 * 255 + .5)
        position = 26 * (1 - (raw - 1) / 255)
    assert initial - position > .5


def test_jammed_gripper_stops_before_further_commands():
    tracker = GripperIncrementTracker()
    with pytest.raises(RuntimeError, match="no progress"):
        for _ in range(5):
            tracker.next_target(10.1961, -.075)


def test_reverse_and_hold_discard_unfinished_close_target():
    tracker = GripperIncrementTracker()
    tracker.next_target(10, -.075)
    assert tracker.next_target(10, .05) == pytest.approx(10.05)
    assert tracker.next_target(10, 0) == 10


def test_limits_and_invalid_inputs():
    tracker = GripperIncrementTracker()
    assert tracker.next_target(1.02, -.2) == 1.02
    assert tracker.next_target(26, .2) == 26
    with pytest.raises(ValueError):
        tracker.next_target(float('nan'), -.1)


@pytest.mark.parametrize("position", [1.12157, 1.01961])
def test_reached_minimum_holds_without_false_stall(position):
    tracker = GripperIncrementTracker()
    for _ in range(10):
        assert tracker.next_target(position, -.2) == 1.02
    assert tracker.next_target(position, .15) > position


def test_stall_two_counts_before_minimum_is_still_rejected():
    tracker = GripperIncrementTracker()
    with pytest.raises(RuntimeError, match="no progress"):
        for _ in range(6):
            tracker.next_target(1.22353, -.2)


def test_reached_maximum_holds_without_false_stall():
    tracker = GripperIncrementTracker()
    for _ in range(10):
        assert tracker.next_target(25.89804, .2) == 26


@pytest.mark.parametrize("changes", [
    {"positionOk": False}, {"positionMm": None}, {"positionMm": float("nan")},
    {"positionSampleTs": 999}, {"positionSampleTs": 1201},
    {"lastCommandTs": 999}, {"positionSampleTs": 600},
])
def test_invalid_or_old_feedback_cannot_confirm(changes):
    assert not gripper_target_reached(feedback(**changes), target_mm=10.1586, stroke_mm=26, sent_at_ms=1000, now_ms=1200)
