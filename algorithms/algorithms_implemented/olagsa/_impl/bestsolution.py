from .individual import Individual
from .key import Key


class BestSolution:
    def __init__(
        self,
        individual: Individual,
        fitness: float,
        key: Key | None = None,
    ) -> None:
        self.individual = individual
        self.fitness = fitness
        self.key = key
