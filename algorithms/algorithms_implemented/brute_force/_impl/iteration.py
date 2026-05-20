import numpy as np

from .consumer import Consumer


class Iteration:
    """One iteration of an allocation key in the brute-force algorithm.

    Holds the per-consumer state and the aggregated totals (consumption,
    surplus). ``energy_allocated`` is the per-time-step production matrix
    available to this iteration; it has shape ``(num_consumers, T)`` to
    match the legacy implementation, but only row 0 carries the production
    profile (rows are duplicated for broadcasting convenience).
    """

    def __init__(self, consumers: list[Consumer], energy_allocated: np.ndarray) -> None:
        self.consumers = consumers
        self.energy_allocated = energy_allocated
        self.energy_allocated_percentage: float = 0.0

        self.consumption_total: float = 0.0
        self.surplus: np.ndarray = np.zeros(energy_allocated.shape[1])
        self.surplus_total: float = 0.0

    def compute_consumption_total(self) -> None:
        self.consumption_total = float(sum(np.sum(c.consumption) for c in self.consumers))

    def compute_surplus(self) -> None:
        T = len(self.consumers[0].surplus)
        out = np.zeros(T)
        for c in self.consumers:
            out += np.asarray(c.surplus)
        self.surplus = out

    def compute_surplus_total(self) -> None:
        self.surplus_total = float(sum(c.surplus_total for c in self.consumers))

    def copy(self) -> "Iteration":
        new = Iteration(
            consumers=[c.copy() for c in self.consumers],
            energy_allocated=self.energy_allocated,
        )
        new.energy_allocated_percentage = self.energy_allocated_percentage
        new.consumption_total = self.consumption_total
        new.surplus = np.array(self.surplus, copy=True)
        new.surplus_total = self.surplus_total
        return new
