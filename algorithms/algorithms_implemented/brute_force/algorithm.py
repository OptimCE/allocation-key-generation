"""Brute-force algorithm implementation — worker entry point.

Imported only when ``autodiscover(load_implementations=True)`` is called
(i.e. from the worker process). Pulls in numpy via ``_impl`` modules.
The API process never imports this file.
"""

from __future__ import annotations

from ...base import (
    Algorithm,
    AlgorithmRawData,
    AlgorithmResult,
    AllocationKeyResult,
    ConsumerResult,
    IterationResult,
)
from ...registry import registry
from ._impl.generator import BruteForceGenerator
from ._impl.iteration import IterationSummary
from ._impl.node import Node
from .inputs import BruteForceInput
from .metadata import BRUTE_FORCE_METADATA


class BruteForceAlgorithm(Algorithm[BruteForceInput]):
    metadata = BRUTE_FORCE_METADATA

    async def run(
        self,
        inputs: BruteForceInput,
        raw_data: AlgorithmRawData,
    ) -> AlgorithmResult:
        generator = BruteForceGenerator(nb_iterations=inputs.iterations)
        root_nodes = generator.generate(raw_data.C, raw_data.VA)

        keys: list[AllocationKeyResult] = []
        for root in root_nodes:
            keys.extend(_flatten_node(root, raw_data.consumer_names))

        keys.sort(key=lambda k: k.iterations[-1].surplus_total if k.iterations else 0.0)
        return AlgorithmResult(keys=keys)


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------
_DESCRIPTION = "Clé obtenue grâce à l'algorithme de brute force"


def _consumer_results(summary: IterationSummary, consumer_names: list[str]) -> list[ConsumerResult]:
    """Convert an iteration's consumer shares into the pure ConsumerResult schema."""
    results: list[ConsumerResult] = []
    for j, pct in enumerate(summary.consumer_percentages):
        name = (
            consumer_names[j] if j < len(consumer_names) and consumer_names[j] else f"Consumer {j}"
        )
        results.append(ConsumerResult(name=name, energy_allocated_percentage=pct))
    return results


def _iteration_result(
    summary: IterationSummary, number: int, consumer_names: list[str]
) -> IterationResult:
    return IterationResult(
        number=number,
        energy_allocated_percentage=summary.energy_allocated_percentage,
        surplus_total=summary.surplus_total,
        consumers=_consumer_results(summary, consumer_names),
    )


def _flatten_node(root: Node, consumer_names: list[str]) -> list[AllocationKeyResult]:
    """Walk the result tree and emit one AllocationKeyResult per leaf path."""
    keys: list[AllocationKeyResult] = []

    def walk(node: Node, path_iters: list[IterationResult], path_names: list[str]) -> None:
        iter_res = _iteration_result(node.summary, len(path_iters) + 1, consumer_names)
        new_iters = [*path_iters, iter_res]
        new_names = [*path_names, node.name]

        if not node.children:
            keys.append(_build_key(new_iters, new_names))
            return
        for child in node.children:
            walk(child, new_iters, new_names)

    walk(root, [], [])
    return keys


def _build_key(iterations: list[IterationResult], algo_names: list[str]) -> AllocationKeyResult:
    pct_strs = ",".join(f"{round(it.energy_allocated_percentage, 2)}" for it in iterations)
    final_surplus = round(iterations[-1].surplus_total, 2) if iterations else 0.0
    joined = " - ".join(algo_names)

    if len(iterations) == 1:
        name = f"{joined} / Pourcentage = ({pct_strs})"
    else:
        name = f"{joined} / Pourcentages = ({pct_strs}) / Surplus : {final_surplus}"

    return AllocationKeyResult(
        name=name,
        description=_DESCRIPTION,
        iterations=iterations,
    )


# Worker-side registration. ``register_implementation`` is idempotent for
# metadata and indexes the implementation class.
registry.register_implementation(BruteForceAlgorithm)
