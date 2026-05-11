import numpy as np

from .consumer import Consumer


class Iteration:
    """One iteration of an allocation key.

    Holds the per-consumer state and the aggregated totals for this
    iteration (consumption, allocated, surplus, residual volume).
    """

    def __init__(self, consumers: list[Consumer], energy_allocated: np.ndarray) -> None:
        self.consumers = consumers
        self.energy_allocated = energy_allocated
        self.energy_allocated_percentage: float = 0.0

        self.energy_allocated_total: float = 0.0
        self.consumption_total: float = 0.0
        self.surplus_total: float = 0.0

    def compute_surplus_total(self) -> None:
        self.surplus_total = sum(c.surplus_total for c in self.consumers)
