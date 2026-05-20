import numpy as np


class Consumer:
    """Per-iteration state for a single consumer.

    Tracks consumption, allocated energy, residual volume, and surplus
    through the course of one iteration of the allocation key.
    """

    def __init__(
        self,
        consumption: np.ndarray,
        energy_allocated_percentage: float,
        money_committed: float = 0.0,
    ) -> None:
        self.consumption = consumption
        self.energy_allocated_percentage = energy_allocated_percentage
        self.money_committed = money_committed

        self.consumption_total: float = 0.0
        self.energy_allocated: np.ndarray = np.zeros_like(consumption)
        self.energy_allocated_total: float = 0.0
        self.energy_allocated_consumed: np.ndarray = np.zeros_like(consumption)
        self.energy_allocated_consumed_total: float = 0.0

        self.effective_consumption = consumption
        self.residual_volume: np.ndarray = np.zeros_like(consumption)
        self.residual_volume_total: float = 0.0
        self.surplus: np.ndarray = np.zeros_like(consumption)
        self.surplus_total: float = 0.0
        self.name: str | None = None

    def compute_residual_volume_and_surplus(self) -> None:
        diff = self.consumption - self.energy_allocated
        self.residual_volume = (diff + np.abs(diff)) / 2
        self.surplus = (-diff + np.abs(diff)) / 2
        self.residual_volume_total = float(np.sum(self.residual_volume))
        self.surplus_total = float(np.sum(self.surplus))

    def compute_energy_allocated_consumed(self) -> None:
        self.energy_allocated_consumed = np.minimum(self.consumption, self.energy_allocated)
        self.energy_allocated_consumed_total = float(np.sum(self.energy_allocated_consumed))
