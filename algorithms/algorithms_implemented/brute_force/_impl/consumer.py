import numpy as np


class Consumer:
    """Per-iteration state for a single consumer in the brute-force algorithm.

    Tracks consumption, allocated energy, residual volume and surplus.
    Distinct from olagsa's Consumer because brute-force needs a per-time-step
    percentage array (used by the ``prorata`` distribution function), and
    because we explicitly reuse this state across nested iterations.
    """

    def __init__(
        self,
        consumption: np.ndarray,
        energy_allocated_percentage: float = 0.0,
    ) -> None:
        self.consumption = consumption
        # Scalar share, or -1.0 to mark a time-varying allocation (prorata).
        self.energy_allocated_percentage: float = energy_allocated_percentage
        self.energy_allocated_percentage_time: np.ndarray = np.zeros_like(consumption)

        self.energy_allocated: np.ndarray = np.zeros_like(consumption)
        self.energy_allocated_consumed: np.ndarray = np.zeros_like(consumption)
        self.energy_allocated_consumed_total: float = 0.0

        self.residual_volume: np.ndarray = np.zeros_like(consumption)
        self.residual_volume_total: float = 0.0
        self.surplus: np.ndarray = np.zeros_like(consumption)
        self.surplus_total: float = 0.0

    def compute_residual_volume_and_surplus(self) -> None:
        diff = np.asarray(self.consumption) - np.asarray(self.energy_allocated)
        self.residual_volume = np.maximum(0.0, diff)
        self.surplus = np.maximum(0.0, -diff)
        self.residual_volume_total = float(np.sum(self.residual_volume))
        self.surplus_total = float(np.sum(self.surplus))

    def compute_energy_allocated_consumed(self) -> None:
        self.energy_allocated_consumed = np.minimum(
            np.asarray(self.consumption), np.asarray(self.energy_allocated)
        )
        self.energy_allocated_consumed_total = float(np.sum(self.energy_allocated_consumed))

    def copy(self) -> "Consumer":
        new = Consumer(
            consumption=np.array(self.consumption, copy=True),
            energy_allocated_percentage=self.energy_allocated_percentage,
        )
        new.energy_allocated_percentage_time = np.array(
            self.energy_allocated_percentage_time, copy=True
        )
        new.energy_allocated = np.array(self.energy_allocated, copy=True)
        new.energy_allocated_consumed = np.array(self.energy_allocated_consumed, copy=True)
        new.energy_allocated_consumed_total = self.energy_allocated_consumed_total
        new.residual_volume = np.array(self.residual_volume, copy=True)
        new.residual_volume_total = self.residual_volume_total
        new.surplus = np.array(self.surplus, copy=True)
        new.surplus_total = self.surplus_total
        return new
