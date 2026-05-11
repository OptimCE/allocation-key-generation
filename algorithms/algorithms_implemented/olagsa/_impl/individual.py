class Individual:
    def __init__(self, chromosomes: list[float], fitness: float) -> None:
        self.chromosomes = chromosomes
        self.fitness = fitness
        self.adjusted_fitness: float = 0.0
