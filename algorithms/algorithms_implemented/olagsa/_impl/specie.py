from typing import Callable, Optional

from .coordinate import Coordinate
from .individual import Individual


class Specie:
    def __init__(self, specie_id: int, individuals: list[Individual]) -> None:
        self.id = specie_id
        self.individuals = individuals
        self.iteration_without_improvement = 0
        self.best_fitness: float = 0.0
        self.is_same_specie: Optional[Callable[[Coordinate], bool]] = None
        self.centroid: Optional[Coordinate] = None
