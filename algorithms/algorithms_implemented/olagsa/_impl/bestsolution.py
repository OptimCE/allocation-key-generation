from typing import Optional

from .individual import Individual
from .key import Key


class BestSolution:
    def __init__(
        self,
        individual: Individual,
        fitness: float,
        key: Optional[Key] = None,
    ) -> None:
        self.individual = individual
        self.fitness = fitness
        self.key = key
