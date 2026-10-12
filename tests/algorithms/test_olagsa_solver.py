"""OLAGSA's convex solver: the former answers, at linear memory.

``WarmStartSolver`` used to multiply a diagonal (n x n) variable by the whole
tiled (n x T) production matrix and compile it with DPP. That compile reserved
address space growing with (n*T)^2, so 20 meters over 92 days of quarter-hours
got the solver process OOM-killed during its first solve. These tests pin the
replacement to the former formulation's allocations and keep the compile from
growing back.
"""

import os
import pathlib
import subprocess
import sys
import textwrap

import cvxpy as cp
import numpy as np
import pytest

from algorithms.algorithms_implemented.olagsa._impl.warm_start_solver import WarmStartSolver

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Clarabel's default feasibility/gap tolerance. Rebuilding the same LP another
# way only changes float rounding in its data: measured gaps are 1e-14 to 3e-10,
# the latter where the optimum is flat. A wrong formulation is off by far more.
_SOLVER_TOL = 1e-8


def _data(num_consumers: int, horizon: int) -> tuple[np.ndarray, np.ndarray]:
    """Quarter-hour-like data: a daily solar bell for VA, noisy loads for C."""
    rng = np.random.default_rng(3)
    hour = (np.arange(horizon) % 96) / 4
    production = 20 * np.clip(np.sin((hour - 6) / 12 * np.pi), 0, None)
    production *= rng.uniform(0.5, 1.0, horizon)
    consumption = rng.gamma(2.0, 0.5, (num_consumers, horizon))
    return consumption, np.tile(production, (num_consumers, 1))


def _former_allocation(VA: np.ndarray, C: np.ndarray) -> tuple[str, np.ndarray, float]:
    """The formulation WarmStartSolver replaced, kept here as the oracle."""
    num_consumers = C.shape[0]
    VAP = cp.Variable((num_consumers, num_consumers), diag=True)
    VAPA = VAP @ VA
    constraints = [cp.trace(VAP) == 1] + [VAP[i, i] >= 0 for i in range(num_consumers)]
    E = ((VAPA - C) + cp.abs(VAPA - C)) / 2
    problem = cp.Problem(cp.Minimize(cp.sum(E)), constraints)
    problem.solve(max_iter=200)
    allocation = VAP.value
    assert allocation is not None
    return problem.status, np.asarray(allocation.diagonal()).ravel(), float(problem.value)


@pytest.mark.parametrize("share", [1.0, 0.4, 0.05])
def test_allocation_matches_the_former_formulation(share):
    C, VA = _data(6, 192)

    solution = WarmStartSolver(C.shape, VA.shape, 200)._solve(VA * share, C)
    status, expected, surplus = _former_allocation(VA * share, C)

    assert solution.status == status == "optimal"
    np.testing.assert_allclose(solution.VAP.diagonal(), expected, rtol=0, atol=_SOLVER_TOL)
    assert np.sum(solution.E) == pytest.approx(surplus, rel=1e-9)
    # Downstream code (solve_genetic's residual demand) relies on VAPA being
    # the full (n, T) allocation, not the (1, T) production row.
    np.testing.assert_allclose(
        solution.VAPA, np.outer(solution.VAP.diagonal(), VA[0] * share), rtol=0, atol=1e-12
    )


def test_a_reused_solver_follows_each_new_problem():
    # The GA builds one solver and re-solves it thousands of times with new
    # VA / C values, so a stale value would silently bias every later candidate.
    C, VA = _data(5, 96)
    solver = WarmStartSolver(C.shape, VA.shape, 200)

    for share, served in ((1.0, 0.0), (0.3, 0.2), (0.7, 0.5)):
        residual = np.maximum(C - served, 0)
        solution = solver._solve(VA * share, residual)
        _status, expected, _surplus = _former_allocation(VA * share, residual)
        np.testing.assert_allclose(solution.VAP.diagonal(), expected, rtol=0, atol=_SOLVER_TOL)


def test_refuses_a_production_matrix_that_is_not_one_tiled_profile():
    # The (1, T) row stands for every consumer row; anything else would be
    # silently replaced by its first row.
    C, VA = _data(3, 24)
    VA = VA.copy()
    VA[1, 5] += 1.0
    solver = WarmStartSolver(C.shape, VA.shape, 200)

    with pytest.raises(ValueError, match="one production profile"):
        solver._solve(VA, C)


def test_refuses_mismatched_shapes():
    with pytest.raises(ValueError, match="does not match"):
        WarmStartSolver((3, 24), (3, 25), 200)


@pytest.mark.skipif(sys.platform == "win32", reason="RLIMIT_AS is POSIX-only")
def test_a_month_of_quarter_hours_compiles_inside_a_small_address_space():
    # 20 meters x 31 days. The former DPP compile reserved ~18.6 GB of address
    # space for this (~130 GB at 92 days), so under this cap it aborted with
    # std::bad_alloc. The current compile peaks around 0.5 GB.
    script = textwrap.dedent(
        """
        import resource
        cap = 3 * 1024**3
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))

        import numpy as np
        from algorithms.algorithms_implemented.olagsa._impl.warm_start_solver import (
            WarmStartSolver,
        )

        rng = np.random.default_rng(0)
        horizon = 31 * 96
        C = rng.gamma(2.0, 0.5, (20, horizon))
        VA = np.tile(rng.uniform(0.0, 20.0, horizon), (20, 1))
        print(WarmStartSolver(C.shape, VA.shape, 200)._solve(VA * 0.6, C).status)
        """
    )
    # One BLAS thread: per-thread buffers would otherwise scale with the CI
    # runner's core count and eat into the cap for reasons unrelated to cvxpy.
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}

    result = subprocess.run(  # noqa: S603 — fixed interpreter and script, no user input
        [sys.executable, "-c", script],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "optimal"
