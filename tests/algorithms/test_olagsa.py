"""OLAGSA run-level tests on a small synthetic community.

``iterations`` accepts 1 to 3, the same range as brute_force and the CRM's
1-based iteration numbers. The GA only has something to search with 2 or 3
iterations. With one iteration the split across iterations is fixed at 100 %,
so the run is a single convex solve. It used to index a 2-D/3-D species space
with a 1-gene chromosome and fail with ``IndexError: list index out of range``
(dev stack, 2026-10-07).
"""

import random

import numpy as np
import pytest
from pydantic import ValidationError

from algorithms.algorithms_implemented.olagsa._impl.warm_start_solver import WarmStartSolver
from algorithms.algorithms_implemented.olagsa.algorithm import OlagsaAlgorithm
from algorithms.algorithms_implemented.olagsa.inputs import OlagsaInput
from algorithms.base import AlgorithmRawData

_CONSUMERS = ["EAN-A", "EAN-B", "EAN-C", "EAN-D"]


@pytest.fixture
def raw_data() -> AlgorithmRawData:
    """One day of 15-minute readings: 4 consumers plus a midday production bell."""
    rng = np.random.default_rng(7)
    horizon = 96
    consumption = rng.uniform(0.05, 0.6, size=(len(_CONSUMERS), horizon))
    hours = np.arange(horizon) / 4
    production = np.clip(np.sin((hours - 6) / 12 * np.pi), 0, None) * 1.5
    return AlgorithmRawData(
        C=consumption,
        VA=np.tile(production, (len(_CONSUMERS), 1)),
        consumer_names=list(_CONSUMERS),
    )


@pytest.fixture(autouse=True)
def _seed() -> None:
    random.seed(0)
    np.random.seed(0)  # the GA's roulette wheel draws from numpy's global RNG


async def test_single_iteration_puts_all_production_in_iteration_1(raw_data):
    """The reported payload: iterations=1, generations=8, population_size=16."""
    inputs = OlagsaInput(iterations=1, generations=8, population_size=16)

    result = await OlagsaAlgorithm().run(inputs, raw_data)

    assert len(result.keys) == 1
    [iteration] = result.keys[0].iterations
    assert iteration.number == 1
    assert iteration.energy_allocated_percentage == 1.0
    assert [c.name for c in iteration.consumers] == _CONSUMERS
    shares = [c.energy_allocated_percentage for c in iteration.consumers]
    assert all(share >= -1e-6 for share in shares)
    assert sum(shares) == pytest.approx(1.0, abs=1e-4)


async def test_single_iteration_does_not_breed_a_population(raw_data, monkeypatch):
    """Every candidate is the same 1-gene chromosome [1.0], so there is
    nothing to search: one solve for the fitness and one for the key,
    whatever the population size and generation count.
    """
    calls = 0
    original = WarmStartSolver._solve

    def counting_solve(self, *args):
        nonlocal calls
        calls += 1
        return original(self, *args)

    monkeypatch.setattr(WarmStartSolver, "_solve", counting_solve)
    inputs = OlagsaInput(iterations=1, generations=25, population_size=50)

    await OlagsaAlgorithm().run(inputs, raw_data)

    assert calls == 2


@pytest.mark.parametrize("iterations", [2, 3])
async def test_multi_iteration_keys_split_production_across_iterations(raw_data, iterations):
    inputs = OlagsaInput(iterations=iterations, generations=3, population_size=8)

    result = await OlagsaAlgorithm().run(inputs, raw_data)

    assert result.keys
    for key in result.keys:
        assert [it.number for it in key.iterations] == list(range(1, iterations + 1))
        split = sum(it.energy_allocated_percentage for it in key.iterations)
        assert split == pytest.approx(1.0)


@pytest.mark.parametrize("iterations", [0, 4])
def test_iterations_outside_1_to_3_are_rejected(iterations):
    with pytest.raises(ValidationError):
        OlagsaInput.model_validate({"iterations": iterations})


def test_iterations_schema_bounds_drive_the_frontend_form():
    """The start form reads ``minimum``/``maximum`` straight off this schema."""
    prop = OlagsaInput.model_json_schema()["properties"]["iterations"]

    assert (prop["minimum"], prop["maximum"]) == (1, 3)
