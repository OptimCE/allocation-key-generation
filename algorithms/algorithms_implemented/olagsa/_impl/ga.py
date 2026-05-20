"""OLAGSA genetic algorithm.

Ported from the former OLAGSAGeneratorMS project (``generator/OLAGSA/ga.py``)
with the following intentional changes:

- Bug fix: species id assignment now uses a properly-incremented counter
  (the former code used the Python expression ``++self.last_id_specie``
  which is a no-op, so all species received id 0 when num_key_iteration == 2).
- Class naming and API adapted to the new algorithms package conventions.
- Decoupled from the old file-loading path: takes pre-parsed ``C``, ``VA``,
  and ``consumer_names`` as inputs.
- Produces a list of ``BestSolution`` objects; conversion to the pure
  ``AlgorithmResult`` schema lives in ``algorithm.py``, keeping this
  module focused on the GA math.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable

import numpy as np

from .bestsolution import BestSolution
from .coordinate import Coordinate, Coordinate3D, sum_coordinates_3d
from .individual import Individual
from .segment import Segment, Triangle
from .specie import Specie
from .warm_start_solver import WarmStartSolver


class OlagsaGA:
    """OLAGSA speciated genetic algorithm."""

    # Penalty applied to the fitness of infeasible candidates (solver
    # status != "optimal"). Large enough to dominate any feasible score.
    PONDERATION_EXCEDENT = 1000.0
    SOLVER_MAX_ITER = 200

    def __init__(
        self,
        *,
        num_key_iteration: int,
        num_generation: int,
        population_size: int,
        crossover_rate: float,
        crossover_rate_inter_species: float,
        mutation_rate: float,
        species_waited: int,
        max_iteration_without_improvement: int,
    ) -> None:
        self.num_key_iteration = num_key_iteration
        self.num_generation = num_generation
        self.population_size = population_size
        self.crossover_rate = crossover_rate
        self.crossover_rate_inter_species = crossover_rate_inter_species
        self.mutation_rate = mutation_rate
        self.species_waited = species_waited
        self.max_iteration_without_improvement = max_iteration_without_improvement

        self._last_specie_id = 0
        self._C: np.ndarray | None = None
        self._VA: np.ndarray | None = None
        self._solver: WarmStartSolver | None = None

    # ---- Species-space construction ---------------------------------------
    def _next_specie_id(self) -> int:
        """Return a fresh, monotonically-increasing species id.

        Replaces the former ``++self.last_id_specie`` expression, which in
        Python evaluates to ``+(+self.last_id_specie)`` — a no-op that
        caused every species to get id 0 in the 2D case.
        """
        sid = self._last_specie_id
        self._last_specie_id += 1
        return sid

    @staticmethod
    def _line_func(x: float) -> float:
        return -x + 1

    def _find_midpoints(self, depth: int) -> list[Coordinate]:
        mids: list[Coordinate] = []
        base_value = 1 / depth
        value = base_value
        for _ in range(depth - 1):
            mids.append(Coordinate(value, self._line_func(value)))
            value += base_value
        return mids

    @staticmethod
    def _build_segments(mids: list[Coordinate]) -> list[Segment]:
        start = Coordinate(0, 1)
        end = Coordinate(1, 0)
        if not mids:
            return [Segment(start, end)]
        segments: list[Segment] = []
        for mid in mids:
            segments.append(Segment(start, mid))
            start = mid
        segments.append(Segment(start, end))
        return segments

    def _divide_triangle(self, vertices: list[Coordinate3D], depth: int) -> list[Triangle]:
        a, b, c = vertices
        if depth == 0:
            return [Triangle(a, b, c)]
        ab_mid = sum_coordinates_3d(a, b).divide(2)
        ac_mid = sum_coordinates_3d(a, c).divide(2)
        bc_mid = sum_coordinates_3d(b, c).divide(2)

        result: list[Triangle] = []
        result.extend(self._divide_triangle([a, ab_mid, ac_mid], depth - 1))
        result.extend(self._divide_triangle([b, ab_mid, bc_mid], depth - 1))
        result.extend(self._divide_triangle([c, ac_mid, bc_mid], depth - 1))
        result.extend(self._divide_triangle([ab_mid, ac_mid, bc_mid], depth - 1))
        return result

    def _initialize_species(self) -> list[Specie]:
        species: list[Specie] = []

        if self.num_key_iteration == 1:
            # Trivial case: single iteration, single species, single individual.
            self.num_generation = 1
            self.species_waited = 1
            self.population_size = 1
            s = Specie(self._next_specie_id(), [])
            s.is_same_specie = lambda _x: True
            species.append(s)
            return species

        if self.num_key_iteration == 2:
            segments = self._build_segments(self._find_midpoints(self.species_waited))
            for seg in segments:
                s = Specie(self._next_specie_id(), [])
                s.is_same_specie = self._segment_contains(seg)
                s.centroid = Coordinate(
                    (seg.point_a.x + seg.point_b.x) / 2,
                    (seg.point_a.y + seg.point_b.y) / 2,
                )
                species.append(s)
            return species

        if self.num_key_iteration == 3:
            a = Coordinate3D(1, 0, 0)
            b = Coordinate3D(0, 1, 0)
            c = Coordinate3D(0, 0, 1)
            triangles = self._divide_triangle([a, b, c], self.species_waited)
            for tri in triangles:
                s = Specie(self._next_specie_id(), [])
                s.is_same_specie = self._triangle_contains(tri)
                s.centroid = Coordinate3D(
                    (tri.point_a.x + tri.point_b.x + tri.point_c.x) / 3,
                    (tri.point_a.y + tri.point_b.y + tri.point_c.y) / 3,
                    (tri.point_a.z + tri.point_b.z + tri.point_c.z) / 3,
                )
                species.append(s)
            self.species_waited = len(triangles)
            return species

        raise ValueError(
            f"Unsupported num_key_iteration={self.num_key_iteration} "
            "(only 1, 2, or 3 iterations are supported)."
        )

    # Helpers avoid late-binding closure gotcha without relying on default args.
    @staticmethod
    def _segment_contains(seg: Segment) -> Callable[[Coordinate], bool]:
        return lambda x: seg.point_within(x)

    @staticmethod
    def _triangle_contains(tri: Triangle) -> Callable[[Coordinate], bool]:
        # Specie.is_same_specie is typed Callable[[Coordinate], bool] for both
        # 2D and 3D cases; the 3D path only runs with Coordinate3D inputs at
        # runtime (see _chromosome_to_coordinate), so the narrower lambda is
        # safe to widen here.
        return lambda x: tri.point_within(x)  # type: ignore[arg-type]

    # ---- GA primitives ----------------------------------------------------
    def _random_allocation(self) -> list[float]:
        allocation = [random.random() for _ in range(self.num_key_iteration)]
        total = sum(allocation)
        return [x / total for x in allocation]

    def _mutate(self, individual: Individual) -> Individual:
        if random.random() >= self.mutation_rate:
            return Individual(list(individual.chromosomes), 0.0)
        mutated = list(individual.chromosomes)
        index = random.randint(0, len(mutated) - 1)
        mutated[index] = random.random()
        total = sum(mutated)
        mutated = [x / total for x in mutated]
        return Individual(mutated, 0.0)

    def _initialize_population(self) -> list[Individual]:
        return [Individual(self._random_allocation(), 0.0) for _ in range(self.population_size)]

    def _evaluate(self, individual: Individual) -> Individual:
        if self._solver is None or self._C is None or self._VA is None:
            raise RuntimeError("solver not initialized — call setup() before _evaluate")
        solution = self._solver.solve_genetic(
            self.num_key_iteration, self._C, self._VA, individual.chromosomes
        )
        if solution is None or solution.status != "optimal":
            individual.fitness = 100 * self.PONDERATION_EXCEDENT
        else:
            individual.fitness = (1 / (np.sum(solution.E) + 1)) * self.PONDERATION_EXCEDENT
        return individual

    @staticmethod
    def _roulette_wheel(
        population: list[Individual], fitness_values: list[float], n: int
    ) -> list[Individual]:
        fitness_arr = np.nan_to_num(np.array(fitness_values, dtype=np.float64))
        total = float(np.sum(fitness_arr))
        probabilities = fitness_arr / total
        indices = np.random.choice(len(population), size=n, p=probabilities)
        return [population[i] for i in indices]

    def _chromosome_to_coordinate(self, chromosome: list[float]) -> Coordinate:
        if len(chromosome) == 2:
            return Coordinate(chromosome[0], chromosome[1])
        return Coordinate3D(chromosome[0], chromosome[1], chromosome[2])

    def _assign_species(self, population: list[Individual], species: list[Specie]) -> list[Specie]:
        for individual in population:
            point = self._chromosome_to_coordinate(individual.chromosomes)
            for specie in species:
                if specie.is_same_specie is not None and specie.is_same_specie(point):
                    specie.individuals.append(individual)
                    break
            else:
                # Lies exactly on a species boundary — this is genuinely
                # unreachable for a randomised allocation on a partitioned
                # simplex, but we surface it explicitly rather than
                # silently losing the individual.
                raise RuntimeError("Individual could not be assigned to any species")
        return species

    @staticmethod
    def _apply_adjusted_fitness(specie: Specie) -> Specie:
        size = len(specie.individuals)
        for individual in specie.individuals:
            individual.adjusted_fitness = individual.fitness / size
        return specie

    @staticmethod
    def _cull_weakest(specie: Specie) -> Specie:
        if len(specie.individuals) == 1:
            return specie
        specie.individuals.sort(key=lambda x: x.fitness, reverse=True)
        specie.individuals = specie.individuals[: len(specie.individuals) // 2]
        return specie

    def _crossover_chromosomes(self, parent_a: Individual, parent_b: Individual) -> list[float]:
        point = random.randint(0, len(parent_a.chromosomes) - 1)
        if random.randint(0, 1) == 0:
            offspring = parent_a.chromosomes[:point] + parent_b.chromosomes[point:]
        else:
            offspring = parent_b.chromosomes[:point] + parent_a.chromosomes[point:]
        total = sum(offspring)
        return [gene / total for gene in offspring]

    def _breed_intra(self, specie: Specie) -> Individual:
        parent_a, parent_b = self._roulette_wheel(
            specie.individuals,
            [ind.fitness for ind in specie.individuals],
            2,
        )
        if random.randint(0, 1) < self.crossover_rate:
            offspring = self._crossover_chromosomes(parent_a, parent_b)
        else:
            offspring = list(
                parent_a.chromosomes if random.randint(0, 1) == 0 else parent_b.chromosomes
            )
        return Individual(offspring, 0.0)

    def _breed_inter(self, specie: Specie, species: list[Specie]) -> Individual:
        # Pick the nearest non-empty species by centroid distance.
        nearest: Specie | None = None
        min_distance = math.inf
        for other in species:
            if other.id == specie.id or not other.individuals or other.centroid is None:
                continue
            if specie.centroid is None:
                continue
            dist = math.sqrt(
                (specie.centroid.x - other.centroid.x) ** 2
                + (specie.centroid.y - other.centroid.y) ** 2
                + (getattr(specie.centroid, "z", 0) - getattr(other.centroid, "z", 0)) ** 2
            )
            if dist < min_distance:
                min_distance = dist
                nearest = other

        if nearest is None:
            # Fall back to intra-species if no partner is available.
            return self._breed_intra(specie)

        parent_a = self._roulette_wheel(
            specie.individuals, [ind.fitness for ind in specie.individuals], 1
        )[0]
        parent_b = self._roulette_wheel(
            nearest.individuals, [ind.fitness for ind in nearest.individuals], 1
        )[0]
        return Individual(self._crossover_chromosomes(parent_a, parent_b), 0.0)

    # ---- Main loop --------------------------------------------------------
    def run(self, C: np.ndarray, VA: np.ndarray) -> list[BestSolution]:
        """Execute the GA and return the best individual of each species.

        Parameters
        ----------
        C : ``(num_consumers, T)`` consumption matrix.
        VA : ``(num_consumers, T)`` production matrix broadcast across consumers.
        """
        self._C = C
        self._VA = VA
        self._solver = WarmStartSolver(C.shape, VA.shape, self.SOLVER_MAX_ITER)

        population = [self._evaluate(ind) for ind in self._initialize_population()]
        species = self._assign_species(population, self._initialize_species())

        for specie in species:
            if not specie.individuals:
                continue
            specie.individuals.sort(key=lambda x: x.fitness, reverse=True)
            specie.best_fitness = specie.individuals[0].fitness

        for _gen in range(self.num_generation):
            no_improvement = [False] * len(species)
            for i, specie in enumerate(species):
                if not specie.individuals:
                    continue
                specie.individuals.sort(key=lambda x: x.fitness, reverse=True)
                if specie.individuals[0].fitness >= specie.best_fitness:
                    specie.iteration_without_improvement += 1
                else:
                    specie.best_fitness = specie.individuals[0].fitness
                    specie.iteration_without_improvement = 0
                if specie.iteration_without_improvement >= self.max_iteration_without_improvement:
                    no_improvement[i] = True

            if all(no_improvement):
                break

            species = [self._apply_adjusted_fitness(s) for s in species if s.individuals]
            species = [self._cull_weakest(s) for s in species]

            total_adjusted = sum(ind.adjusted_fitness for s in species for ind in s.individuals)
            if total_adjusted == 0:
                break

            num_children: list[int] = []
            for specie in species:
                if not specie.individuals:
                    num_children.append(0)
                    continue
                ratio = specie.individuals[0].adjusted_fitness / total_adjusted
                num_children.append(int(ratio * self.population_size))

            new_children: list[Individual] = []
            for specie, count in zip(species, num_children, strict=False):
                for _ in range(count):
                    if (
                        self.species_waited > 1
                        and random.random() < self.crossover_rate_inter_species
                    ):
                        child = self._breed_inter(specie, species)
                    else:
                        child = self._breed_intra(specie)
                    if self.mutation_rate > 0.0:
                        child = self._mutate(child)
                    new_children.append(self._evaluate(child))

            species = self._assign_species(new_children, species)

        # Collect the best individual from each species and compute its
        # full key via a dedicated solver pass.
        results: list[BestSolution] = []
        for specie in species:
            if not specie.individuals:
                continue
            specie.individuals.sort(key=lambda x: x.fitness, reverse=True)
            best = specie.individuals[0]
            key = self._solver.solve_key(
                self.num_key_iteration, self._C, self._VA, best.chromosomes
            )
            results.append(BestSolution(best, best.fitness, key))
        return results
