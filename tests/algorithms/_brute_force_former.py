"""brute_force as it was when its result tree kept every iteration.

A condensed copy of ``algorithms/algorithms_implemented/brute_force`` at
4b19561: every node held its whole iteration, and the keys were read off the
tree once it was complete. The arithmetic is the former code's, expression for
expression; only state that was written and never read is left out
(``energy_allocated_percentage_time``, ``energy_allocated_consumed``, the
residual total). Kept as the oracle for tests/algorithms/test_brute_force.py.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from algorithms.base import (
    AlgorithmResult,
    AllocationKeyResult,
    ConsumerResult,
    IterationResult,
)

_ALGO_NAMES = ["egalitaire", "prorata_total", "prorata"]
_DESCRIPTION = "Clé obtenue grâce à l'algorithme de brute force"


class _Consumer:
    def __init__(self, consumption: np.ndarray) -> None:
        self.consumption = consumption
        self.energy_allocated_percentage = 0.0
        self.energy_allocated: np.ndarray = np.zeros_like(consumption)
        self.residual_volume: np.ndarray = np.zeros_like(consumption)
        self.surplus: np.ndarray = np.zeros_like(consumption)
        self.surplus_total = 0.0

    def settle(self) -> None:
        diff = np.asarray(self.consumption) - np.asarray(self.energy_allocated)
        self.residual_volume = np.maximum(0.0, diff)
        self.surplus = np.maximum(0.0, -diff)
        self.surplus_total = float(np.sum(self.surplus))

    def copy(self) -> _Consumer:
        new = _Consumer(np.array(self.consumption, copy=True))
        new.energy_allocated_percentage = self.energy_allocated_percentage
        new.energy_allocated = np.array(self.energy_allocated, copy=True)
        new.residual_volume = np.array(self.residual_volume, copy=True)
        new.surplus = np.array(self.surplus, copy=True)
        new.surplus_total = self.surplus_total
        return new


class _Iteration:
    def __init__(self, consumers: list[_Consumer], energy_allocated: np.ndarray) -> None:
        self.consumers = consumers
        self.energy_allocated = energy_allocated
        self.energy_allocated_percentage: float = 0.0
        self.consumption_total = 0.0
        self.surplus: np.ndarray = np.zeros(energy_allocated.shape[1])
        self.surplus_total = 0.0

    def settle(self) -> None:
        out = np.zeros(len(self.consumers[0].surplus))
        for c in self.consumers:
            out += np.asarray(c.surplus)
        self.surplus = out
        self.surplus_total = float(sum(c.surplus_total for c in self.consumers))

    def copy(self) -> _Iteration:
        new = _Iteration([c.copy() for c in self.consumers], self.energy_allocated)
        new.energy_allocated_percentage = self.energy_allocated_percentage
        new.consumption_total = self.consumption_total
        new.surplus = np.array(self.surplus, copy=True)
        new.surplus_total = self.surplus_total
        return new


def _egalitaire(it: _Iteration) -> _Iteration:
    share = 1.0 / len(it.consumers)
    profile = np.asarray(it.energy_allocated[0])
    for c in it.consumers:
        c.energy_allocated_percentage = share
        c.energy_allocated = profile * share
        c.settle()
    it.settle()
    return it


def _prorata_total(it: _Iteration) -> _Iteration:
    it.consumption_total = float(sum(np.sum(c.consumption) for c in it.consumers))
    profile = np.asarray(it.energy_allocated[0])
    if it.consumption_total == 0:
        first = it.consumers[0]
        first.energy_allocated_percentage = 1.0
        first.energy_allocated = profile * 1.0
        first.settle()
        for c in it.consumers[1:]:
            c.energy_allocated_percentage = 0.0
            c.energy_allocated = np.zeros_like(profile)
            c.settle()
    else:
        for c in it.consumers:
            share = float(np.sum(c.consumption)) / it.consumption_total
            c.energy_allocated_percentage = share
            c.energy_allocated = profile * share
            c.settle()
    it.settle()
    return it


def _prorata(it: _Iteration) -> _Iteration:
    consumers = it.consumers
    horizon = len(consumers[0].consumption)
    profile = np.asarray(it.energy_allocated[0])
    for c in consumers:
        c.energy_allocated_percentage = -1.0
        c.energy_allocated = np.zeros(horizon)
    for t in range(horizon):
        sum_consumption_time = sum(float(c.consumption[t]) for c in consumers)
        if sum_consumption_time > 0:
            for c in consumers:
                if float(c.consumption[t]) > 0:
                    share_t = float(c.consumption[t]) / sum_consumption_time
                    c.energy_allocated[t] = float(profile[t]) * share_t
        else:
            consumers[0].energy_allocated[t] = float(profile[t])
    for c in consumers:
        c.settle()
    it.settle()
    return it


_ALGOS: dict[str, Callable[[_Iteration], _Iteration]] = {
    "egalitaire": _egalitaire,
    "prorata_total": _prorata_total,
    "prorata": _prorata,
}


class _Node:
    def __init__(self, name: str, iteration: _Iteration) -> None:
        self.name = name
        self.iteration = iteration
        self.children: list[_Node] = []


def _reformat_excedents(shape: tuple[int, ...], surplus: np.ndarray) -> np.ndarray:
    new_va = np.zeros(shape)
    for i in range(shape[1]):
        new_va[:, i] = np.array([float(surplus[i])] * shape[0])
    return new_va


def _apply_first(root: _Iteration, pct: float, algo: str) -> _Node:
    it = root.copy()
    it.energy_allocated_percentage = pct
    it.energy_allocated = root.energy_allocated * pct
    return _Node(algo, _ALGOS[algo](it))


def _build_next(
    root: _Iteration, previous: _Iteration, production: np.ndarray, pct: float
) -> _Iteration:
    nxt = previous.copy()
    nxt.energy_allocated_percentage = pct
    nxt.energy_allocated = (root.energy_allocated * pct) + _reformat_excedents(
        production.shape, previous.surplus
    )
    for c in nxt.consumers:
        c.consumption = np.array(c.residual_volume, copy=True)
    return nxt


def _generate(consumption: np.ndarray, production: np.ndarray, iterations: int) -> list[_Node]:
    consumption = np.asarray(consumption, dtype=float)
    production = np.asarray(production, dtype=float)
    root = _Iteration(
        [_Consumer(np.array(consumption[i], dtype=float)) for i in range(len(consumption))],
        production,
    )
    results: list[_Node] = []
    if iterations == 1:
        for algo in _ALGO_NAMES:
            it = root.copy()
            it.energy_allocated_percentage = 1.0
            results.append(_Node(algo, _ALGOS[algo](it)))
    elif iterations == 2:
        for i in range(11):
            pct1 = i / 10
            pct2 = 1 - i / 10
            for algo1 in _ALGO_NAMES:
                node1 = _apply_first(root, pct1, algo1)
                base2 = _build_next(root, node1.iteration, production, pct2)
                for algo2 in _ALGO_NAMES:
                    node1.children.append(_Node(algo2, _ALGOS[algo2](base2.copy())))
                results.append(node1)
    else:
        for i in range(11):
            for j in range((10 - i) + 1):
                pct1 = i / 10
                pct2 = j / 10
                pct3_raw = 1 - pct1 - pct2
                pct3 = pct3_raw if pct3_raw > 0 else 0
                for algo1 in _ALGO_NAMES:
                    node1 = _apply_first(root, pct1, algo1)
                    base2 = _build_next(root, node1.iteration, production, pct2)
                    for algo2 in _ALGO_NAMES:
                        node2 = _Node(algo2, _ALGOS[algo2](base2.copy()))
                        base3 = _build_next(root, node2.iteration, production, pct3)
                        for algo3 in _ALGO_NAMES:
                            node2.children.append(_Node(algo3, _ALGOS[algo3](base3.copy())))
                        node1.children.append(node2)
                    results.append(node1)
    return results


def _iteration_result(it: _Iteration, number: int, names: list[str]) -> IterationResult:
    profile = np.asarray(it.energy_allocated[0])
    total_profile = float(np.sum(profile)) if profile.size else 0.0
    consumers: list[ConsumerResult] = []
    for j, c in enumerate(it.consumers):
        if c.energy_allocated_percentage >= 0:
            pct = float(c.energy_allocated_percentage)
        elif total_profile > 0:
            pct = float(np.sum(c.energy_allocated)) / total_profile
        else:
            pct = 0.0
        name = names[j] if j < len(names) and names[j] else f"Consumer {j}"
        consumers.append(ConsumerResult(name=name, energy_allocated_percentage=pct))
    return IterationResult(
        number=number,
        energy_allocated_percentage=float(it.energy_allocated_percentage),
        surplus_total=float(it.surplus_total),
        consumers=consumers,
    )


def _build_key(iterations: list[IterationResult], algo_names: list[str]) -> AllocationKeyResult:
    pct_strs = ",".join(f"{round(it.energy_allocated_percentage, 2)}" for it in iterations)
    final_surplus = round(iterations[-1].surplus_total, 2) if iterations else 0.0
    joined = " - ".join(algo_names)
    if len(iterations) == 1:
        name = f"{joined} / Pourcentage = ({pct_strs})"
    else:
        name = f"{joined} / Pourcentages = ({pct_strs}) / Surplus : {final_surplus}"
    return AllocationKeyResult(name=name, description=_DESCRIPTION, iterations=iterations)


def run_former(
    consumption: np.ndarray, production: np.ndarray, names: list[str], iterations: int
) -> AlgorithmResult:
    """The former ``BruteForceAlgorithm.run``, synchronous."""
    keys: list[AllocationKeyResult] = []

    def walk(node: _Node, path_iters: list[IterationResult], path_names: list[str]) -> None:
        new_iters = [*path_iters, _iteration_result(node.iteration, len(path_iters) + 1, names)]
        new_names = [*path_names, node.name]
        if not node.children:
            keys.append(_build_key(new_iters, new_names))
            return
        for child in node.children:
            walk(child, new_iters, new_names)

    for root in _generate(consumption, production, iterations):
        walk(root, [], [])
    keys.sort(key=lambda k: k.iterations[-1].surplus_total if k.iterations else 0.0)
    return AlgorithmResult(keys=keys)
