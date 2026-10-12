"""The participants x timesteps limit shared by the pre-flight, the upload route and the worker."""

from shared.problem_size import MAX_PROBLEM_CELLS, too_large


def test_exactly_at_the_limit_is_accepted():
    assert too_large(1, MAX_PROBLEM_CELLS) is None
    assert too_large(MAX_PROBLEM_CELLS, 1) is None


def test_one_value_over_the_limit_is_refused_with_the_numbers():
    detail = too_large(1, MAX_PROBLEM_CELLS + 1)

    assert detail is not None
    assert f"{MAX_PROBLEM_CELLS + 1} values" in detail
    assert f"{MAX_PROBLEM_CELLS} limit" in detail


def test_the_limit_is_on_the_product_not_on_either_dimension():
    # 3 months of quarter-hours for 20 meters (the size that was OOM-killed
    # before the solver was reformulated) must stay well inside the limit.
    assert too_large(20, 92 * 96) is None
    # A year for 20 meters must not, whatever the period alone looks like.
    assert too_large(20, 365 * 96) is not None
