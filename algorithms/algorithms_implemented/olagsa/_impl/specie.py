from collections.abc import Callable

from .coordinate import Coordinate
from .individual import Individual


class Specie:
    def __init__(self, specie_id: int, individuals: list[Individual]) -> None:
        self.id = specie_id
        self.individuals = individuals
        self.iteration_without_improvement = 0
        self.best_fitness: float = 0.0
        self.is_same_specie: Callable[[Coordinate], bool] | None = None
        self.centroid: Coordinate | None = None
