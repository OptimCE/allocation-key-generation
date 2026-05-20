"""OLAGSA algorithm implementation — worker entry point.

Imported only when ``autodiscover(load_implementations=True)`` is called
(i.e. from the worker process). Pulls in numpy / cvxpy transitively via
``_impl`` modules. The API process never imports this file.
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
from ._impl.bestsolution import BestSolution
from ._impl.ga import OlagsaGA
from .inputs import OlagsaInput
from .metadata import OLAGSA_METADATA


class OlagsaAlgorithm(Algorithm[OlagsaInput]):
    metadata = OLAGSA_METADATA

    async def run(
        self,
        inputs: OlagsaInput,
        raw_data: AlgorithmRawData,
    ) -> AlgorithmResult:
        ga = OlagsaGA(
            num_key_iteration=inputs.iterations,
            num_generation=inputs.generations,
            population_size=inputs.population_size,
            crossover_rate=inputs.crossover_rate,
            crossover_rate_inter_species=inputs.inter_species_crossover_rate,
            mutation_rate=inputs.mutation_rate,
            species_waited=inputs.expected_species_count,
            max_iteration_without_improvement=inputs.max_generations_without_improvement,
        )

        best_solutions = ga.run(raw_data.C, raw_data.VA)
        best_solutions.sort(
            key=lambda bs: bs.key.iterations[-1].surplus_total
            if bs.key and bs.key.iterations
            else 0.0
        )

        keys = [
            _to_allocation_key_result(bs, raw_data.consumer_names)
            for bs in best_solutions
            if bs.key is not None and bs.key.iterations
        ]
        return AlgorithmResult(keys=keys)


def _to_allocation_key_result(best: BestSolution, consumer_names: list[str]) -> AllocationKeyResult:
    """Convert the GA's native ``BestSolution`` into the pure result schema."""
    if best.key is None:
        raise ValueError("BestSolution.key must be set; caller is expected to filter None keys")
    iterations: list[IterationResult] = []
    for idx, iteration in enumerate(best.key.iterations):
        consumers = [
            ConsumerResult(
                name=(
                    consumer_names[j]
                    if j < len(consumer_names) and consumer_names[j]
                    else f"Consumer {j}"
                ),
                energy_allocated_percentage=float(participant.energy_allocated_percentage),
            )
            for j, participant in enumerate(iteration.consumers)
        ]
        iterations.append(
            IterationResult(
                number=idx + 1,
                energy_allocated_percentage=float(iteration.energy_allocated_percentage),
                surplus_total=float(iteration.surplus_total),
                consumers=consumers,
            )
        )

    percentages = ",".join(f"{round(it.energy_allocated_percentage, 2)}" for it in iterations)
    final_surplus = round(iterations[-1].surplus_total, 2) if iterations else 0.0
    name = (
        f"Fitness = {round(best.fitness, 2)} / Pourcentage = ({percentages})"
        f" - Surplus à la fin : {final_surplus}"
    )

    return AllocationKeyResult(
        name=name,
        description="Clé obtenue grâce à l'algorithme OLAGSA",
        iterations=iterations,
    )


# Worker-side registration. Safe to call even if metadata was already
# registered by the package's __init__.py — register_implementation is
# idempotent for metadata and indexes the impl class.
registry.register_implementation(OlagsaAlgorithm)
