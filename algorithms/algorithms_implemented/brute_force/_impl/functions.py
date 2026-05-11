"""Distribution functions used by the brute-force enumeration.

Each function takes an :class:`Iteration` (already configured with the
energy available to this iteration), mutates the consumers' allocations
in place, computes per-consumer residuals/surpluses and aggregated
surplus, and returns ``(Node, iteration)``.
"""

from __future__ import annotations

import numpy as np

from .iteration import Iteration
from .node import Node


def _energy_profile(iteration: Iteration) -> np.ndarray:
    """Return the (T,) production profile for this iteration.

    The legacy implementation kept ``energy_allocated`` as a 2D array of
    shape ``(num_consumers, T)`` with row 0 holding the data; we mirror
    that layout for compatibility with the generator's ``reformat_excedents``
    helper.
    """
    return np.asarray(iteration.energy_allocated[0])


def egalitaire(iteration: Iteration) -> tuple[Node, Iteration]:
    n = len(iteration.consumers)
    share = 1.0 / n
    profile = _energy_profile(iteration)
    for c in iteration.consumers:
        c.energy_allocated_percentage = share
        c.energy_allocated = profile * share
        c.compute_energy_allocated_consumed()
        c.compute_residual_volume_and_surplus()

    iteration.compute_surplus()
    iteration.compute_surplus_total()
    return Node(
        name="egalitaire", iteration=iteration, surplus=iteration.surplus_total
    ), iteration


def prorata_total(iteration: Iteration) -> tuple[Node, Iteration]:
    iteration.compute_consumption_total()
    profile = _energy_profile(iteration)

    if iteration.consumption_total == 0:
        # No demand: assign everything to the first consumer to avoid
        # division by zero (matches legacy fallback).
        first = iteration.consumers[0]
        first.energy_allocated_percentage = 1.0
        first.energy_allocated = profile * 1.0
        first.compute_energy_allocated_consumed()
        first.compute_residual_volume_and_surplus()
        for c in iteration.consumers[1:]:
            c.energy_allocated_percentage = 0.0
            c.energy_allocated = np.zeros_like(profile)
            c.compute_energy_allocated_consumed()
            c.compute_residual_volume_and_surplus()
    else:
        for c in iteration.consumers:
            share = float(np.sum(c.consumption)) / iteration.consumption_total
            c.energy_allocated_percentage = share
            c.energy_allocated = profile * share
            c.compute_energy_allocated_consumed()
            c.compute_residual_volume_and_surplus()

    iteration.compute_surplus()
    iteration.compute_surplus_total()
    return Node(
        name="prorata_total", iteration=iteration, surplus=iteration.surplus_total
    ), iteration


def prorata(iteration: Iteration) -> tuple[Node, Iteration]:
    consumers = iteration.consumers
    T = len(consumers[0].consumption)
    profile = _energy_profile(iteration)

    # Reset per-time-step state and mark as time-varying.
    for c in consumers:
        c.energy_allocated_percentage = -1.0
        c.energy_allocated_percentage_time = np.zeros(T)
        c.energy_allocated = np.zeros(T)

    for t in range(T):
        sum_consumption_time = sum(float(c.consumption[t]) for c in consumers)
        if sum_consumption_time > 0:
            for c in consumers:
                if float(c.consumption[t]) > 0:
                    share_t = float(c.consumption[t]) / sum_consumption_time
                    c.energy_allocated_percentage_time[t] = share_t
                    c.energy_allocated[t] = float(profile[t]) * share_t
        else:
            # Nobody consuming at this time step: dump everything on the
            # first consumer (matches legacy fallback).
            consumers[0].energy_allocated_percentage_time[t] = 1.0
            consumers[0].energy_allocated[t] = float(profile[t])

    for c in consumers:
        c.compute_energy_allocated_consumed()
        c.compute_residual_volume_and_surplus()

    iteration.compute_surplus()
    iteration.compute_surplus_total()
    return Node(
        name="prorata", iteration=iteration, surplus=iteration.surplus_total
    ), iteration


ALGOS: dict[str, callable] = {
    "egalitaire": egalitaire,
    "prorata_total": prorata_total,
    "prorata": prorata,
}

ALGO_NAMES: list[str] = ["egalitaire", "prorata_total", "prorata"]
