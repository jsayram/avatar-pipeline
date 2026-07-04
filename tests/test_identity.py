"""Identity threshold pass/fail + retry seed logic (FR-6)."""
from worker import attempt_seed, identity_passes, mean_cosine


def test_mean_cosine():
    assert mean_cosine([0.8, 0.9, 1.0]) == 0.9
    assert mean_cosine([]) == 0.0


def test_pass_when_mean_meets_threshold():
    # mean(0.90, 0.90, 0.86) ≈ 0.8867 ≥ 0.88
    assert identity_passes([0.90, 0.90, 0.86], 0.88) is True


def test_fail_when_mean_below_threshold():
    assert identity_passes([0.90, 0.70], 0.88) is False  # mean 0.80


def test_boundary_exact_threshold_passes():
    assert identity_passes([0.88, 0.88], 0.88) is True


def test_no_frames_never_passes():
    assert identity_passes([], 0.5) is False


def test_fixed_seed_varies_deterministically_per_retry():
    assert attempt_seed(1000, 0) == 1000
    assert attempt_seed(1000, 1) == 1001
    assert attempt_seed(1000, 2) == 1002


def test_random_seed_when_unset():
    seed = attempt_seed(None, 0)
    assert isinstance(seed, int)
    assert 0 <= seed < 2**32
