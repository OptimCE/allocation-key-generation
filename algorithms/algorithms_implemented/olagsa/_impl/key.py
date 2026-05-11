from .iteration import Iteration


class Key:
    def __init__(self, iterations: list[Iteration]) -> None:
        self.iterations = iterations
