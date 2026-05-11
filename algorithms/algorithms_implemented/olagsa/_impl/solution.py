import numpy as np

from .consumer import Consumer
from .iteration import Iteration


class Solution:
    """Output of a single call to the convex solver.

    Wraps the solver's return values so we can derive an ``Iteration``
    from them and chain solver calls across iterations of the key.
    """

    def __init__(
        self,
        status: str,
        VAP: np.ndarray,
        VA: np.ndarray,
        C: np.ndarray,
        VAPA: np.ndarray,
        E: np.ndarray,
    ) -> None:
        self.status = status
        self.VAP = VAP
        self.VA = VA
        self.C = C
        self.VAPA = VAPA
        self.E = E

    def _compute_consumer_data(self, iteration: Iteration) -> Iteration:
        for consumer in iteration.consumers:
            consumer.consumption_total = float(np.sum(consumer.consumption))
            consumer.energy_allocated = (
                iteration.energy_allocated * consumer.energy_allocated_percentage
            )[0]
            consumer.energy_allocated_total = float(np.sum(consumer.energy_allocated))
            consumer.compute_energy_allocated_consumed()
            consumer.compute_residual_volume_and_surplus()
        return iteration

    def to_iteration(
        self,
        allocation_iteration: float,
        money_committed: np.ndarray | None = None,
    ) -> Iteration:
        if money_committed is None:
            money_committed = np.zeros(len(self.C))

        vap_diag = self.VAP.diagonal()
        consumers = [
            Consumer(
                consumption=self.C[i],
                energy_allocated_percentage=float(vap_diag[i]),
                money_committed=float(money_committed[i]),
            )
            for i in range(self.C.shape[0])
        ]

        iteration = Iteration(consumers, self.VA)
        iteration = self._compute_consumer_data(iteration)
        iteration.energy_allocated_percentage = allocation_iteration
        iteration.compute_surplus_total()
        return iteration
