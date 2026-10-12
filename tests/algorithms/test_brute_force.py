"""brute_force: the former keys, at memory that no longer grows with them.

``BruteForceGenerator`` used to return a tree in which every node kept its
whole ``Iteration``: each consumer's consumption, allocation, residual and
surplus series. Three iterations enumerate 2,574 iterations for 1,782 keys, so
20 meters over 7 days of quarter-hours went past 1.5 GB and the process was
OOM-killed. Nodes now keep only the figures their key reports. These tests
pin the keys to the former implementation (``_brute_force_former.py``) and
keep the memory from growing back.
"""

import tracemalloc

import numpy as np
import pytest

from algorithms.algorithms_implemented.brute_force.algorithm import BruteForceAlgorithm
from algorithms.algorithms_implemented.brute_force.inputs import BruteForceInput
from algorithms.base import AlgorithmRawData
from tests.algorithms._brute_force_former import run_former

_QUARTER_HOURS = 96
_NUM_KEYS = {1: 3, 2: 99, 3: 1782}


def _solar(days: int, seed: int) -> np.ndarray:
    """A daily production bell from 06:00 to 18:00, with passing clouds."""
    rng = np.random.default_rng(seed)
    hour = (np.arange(days * _QUARTER_HOURS) % _QUARTER_HOURS) / 4
    bell = np.clip(np.sin((hour - 6) / 12 * np.pi), 0, None)
    production: np.ndarray = 3.0 * bell * rng.uniform(0.3, 1.0, days * _QUARTER_HOURS)
    return production


def _loads(num_consumers: int, days: int, seed: int) -> np.ndarray:
    """Noisy quarter-hour loads, a tenth of them exactly zero."""
    rng = np.random.default_rng(seed)
    loads = rng.gamma(2.0, 0.08, (num_consumers, days * _QUARTER_HOURS))
    loads[rng.random(loads.shape) < 0.1] = 0.0
    return loads


def _cases() -> dict[str, tuple[np.ndarray, np.ndarray, list[str]]]:
    n = 4
    names = [f"Meter {i}" for i in range(n)]
    loads = _loads(n, 1, seed=1)
    production = np.tile(_solar(1, seed=0), (n, 1))

    # A silent meter, and night hours when nobody draws (prorata's fallback).
    idle = _loads(n, 1, seed=2)
    idle[2] = 0.0
    idle[:, :20] = 0.0

    # Rounded readings, a few negative ones, and more names than meters.
    rounded = np.round(_loads(n, 1, seed=5) * 10)
    rounded[1, 50:60] = -1.0

    return {
        "typical": (loads, production, names),
        "idle_meter_and_nights": (idle, production, names),
        # prorata_total's fallback: nobody draws anything.
        "no_consumption": (np.zeros_like(loads), production, names),
        # Every surplus is 0, so the sort must keep the enumeration order.
        "no_production": (loads, np.zeros_like(production), names),
        # Only row 0 of the production matrix is ever read.
        "untiled_production": (
            loads,
            np.stack([_solar(1, seed=10 + i) for i in range(n)]),
            names,
        ),
        # An unnamed meter falls back to "Consumer 0".
        "single_unnamed_meter": (loads[:1], production[:1], [""]),
        "rounded_and_negative": (
            rounded.astype(np.int64),
            np.round(production * 10),
            [*names, "Extra"],
        ),
    }


_CASES = _cases()


@pytest.mark.parametrize("iterations", [1, 2, 3])
@pytest.mark.parametrize("case", list(_CASES))
async def test_keys_are_the_former_implementations(case: str, iterations: int) -> None:
    consumption, production, names = _CASES[case]
    expected = run_former(consumption, production, names, iterations)

    result = await BruteForceAlgorithm().run(
        BruteForceInput(iterations=iterations),
        AlgorithmRawData(C=consumption, VA=production, consumer_names=names),
    )

    assert len(result.keys) == _NUM_KEYS[iterations]
    assert result.model_dump() == expected.model_dump()


async def _peak_traced_bytes(num_consumers: int, timesteps: int) -> int:
    """Peak memory traced while brute_force enumerates 3 iterations, from 06:00."""
    window = slice(24, 24 + timesteps)
    raw = AlgorithmRawData(
        C=_loads(num_consumers, 2, seed=1)[:, window],
        VA=np.tile(_solar(2, seed=0)[window], (num_consumers, 1)),
        consumer_names=[f"Meter {i}" for i in range(num_consumers)],
    )
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        before = tracemalloc.get_traced_memory()[0]
        await BruteForceAlgorithm().run(BruteForceInput(iterations=3), raw)
        return tracemalloc.get_traced_memory()[1] - before
    finally:
        tracemalloc.stop()


async def test_memory_grows_with_the_period_not_with_the_number_of_keys() -> None:
    """Four times the timesteps must cost about the same, beyond a few series.

    Both runs enumerate the same 1,782 keys; only the time series are longer.
    The former tree kept about 7 (meters x timesteps) matrices per iteration
    for 2,574 iterations, so the 72 extra timesteps cost it ~30 MB here, some
    17,000 matrices. Only a few iterations are now alive at a time; between
    two runs the traced peak varies by about 10 KB, some 6 matrices.
    """
    num_consumers = 3
    # The first run also pays one-off allocations (schema and string caches).
    await _peak_traced_bytes(num_consumers, 24)

    short = await _peak_traced_bytes(num_consumers, 24)
    long = await _peak_traced_bytes(num_consumers, 96)

    extra_matrix = num_consumers * (96 - 24) * 8  # float64 bytes
    assert long - short < 100 * extra_matrix
