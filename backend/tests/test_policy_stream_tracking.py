import pytest

from backend.services.policy_stream_tracking import StreamProgressWatch, monotonic_z_target


def test_repeated_targets_do_not_reset_stall_timeout():
    watch = StreamProgressWatch([0], [100], [1], 0)
    for i in range(1, 10):
        watch.check([0], [10 + i], i / 5)
    with pytest.raises(RuntimeError, match="no progress"):
        watch.check([0], [30], 2.1)


def test_continuous_progress_and_settled_hold_are_allowed():
    watch = StreamProgressWatch([0], [100], [1], 0)
    for i in range(1, 50):
        watch.check([i * 2], [i * 2 + 10], i / 5)
    watch.check([98], [98], 30)


@pytest.mark.parametrize("actual,target,reason", [([0], [101], "lead"), ([float('nan')], [0], "nonfinite"), ([], [0], "shape")])
def test_faults_are_rejected(actual, target, reason):
    watch = StreamProgressWatch([0], [100], [1], 0)
    with pytest.raises(RuntimeError, match=reason):
        watch.check(actual, target, .1)


def test_old_sample_cannot_reverse_z_and_other_axes_are_preserved():
    actual = [0, 0, 10, 0, 0, 0]
    lower, upper = [-100] * 6, [100] * 6
    assert monotonic_z_target(actual, [1, -1, -3, 0, 0, 0], 12, lower, upper) == [1, -1, 12, 0, 0, 0]
    assert monotonic_z_target(actual, [0, 0, 2, 0, 0, 0], 12, lower, upper)[2] == 14
    with pytest.raises(RuntimeError, match="workspace"):
        monotonic_z_target(actual, [101, 0, 0, 0, 0, 0], 12, lower, upper)
