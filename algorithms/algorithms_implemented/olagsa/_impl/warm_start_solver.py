from typing import cast

import cvxpy as cp
import numpy as np

from .iteration import Iteration
from .key import Key
from .solution import Solution


class WarmStartSolver:
    """Warm-started convex solver for a single OLAGSA iteration.

    Minimises the total surplus ``sum(E)`` where ``E = max(VAPA - C, 0)``,
    subject to ``trace(VAP) == 1`` and ``VAP[i,i] >= 0``. The problem is
    built once and re-solved with updated ``VA`` / ``C`` parameters,
    using CVXPY warm-start to cut iteration cost.
    """

    def __init__(self, c_shape: tuple[int, ...], va_shape: tuple[int, ...], max_iter: int) -> None:
        self.max_iter = max_iter
        self.VAP = cp.Variable((c_shape[0], c_shape[0]), diag=True)
        self.VA = cp.Parameter((va_shape[0], va_shape[1]), nonneg=True)
        self.C = cp.Parameter((c_shape[0], c_shape[1]), nonneg=True)
        self.VAPA = self.VAP @ self.VA

        constraints = [cp.trace(self.VAP) == 1]
        for i in range(c_shape[0]):
            constraints.append(self.VAP[i, i] >= 0)

        self.E = ((self.VAPA - self.C) + cp.abs(self.VAPA - self.C)) / 2
        self.problem = cp.Problem(cp.Minimize(cp.sum(self.E)), constraints)

    def _solve(self, VA: np.ndarray, C: np.ndarray) -> Solution:
        self.VA.value = VA
        self.C.value = C
        self.problem.solve(max_iter=self.max_iter, warm_start=True)
        # cvxpy declares .value as ndarray | None; populated after a feasible
        # solve. Callers gate further use on Solution.status == "optimal".
        return Solution(
            self.problem.status,
            cast(np.ndarray, self.VAP.value),
            VA,
            C,
            cast(np.ndarray, self.VAPA.value),
            cast(np.ndarray, self.E.value),
        )

    @staticmethod
    def _surplus_from_previous_iteration(consumers) -> np.ndarray:
        """Sum the previous iteration's positive surplus across consumers,
        then broadcast it so it can be added back to ``VA`` for the next
        iteration (the surplus becomes shareable energy)."""
        num_consumers = len(consumers)
        horizon = len(consumers[0].surplus)
        prev = np.zeros(horizon)
        for t in range(horizon):
            for c in consumers:
                if c.surplus[t] > 0.0:
                    prev[t] += c.surplus[t]
        return np.tile(prev, (num_consumers, 1))

    def solve_genetic(
        self,
        num_iterations: int,
        C: np.ndarray,
        VA: np.ndarray,
        allocation_iteration: list[float],
    ) -> Solution | None:
        """Solve successive iterations for a single GA candidate, returning
        only the final ``Solution`` (used to compute the fitness score)."""
        current_C = C
        current: Solution | None = None
        for i in range(num_iterations):
            if i == 0:
                current = self._solve(VA * allocation_iteration[i], current_C)
            else:
                if current is None:
                    raise RuntimeError("current must be populated by the i == 0 branch")
                prev_iter = current.to_iteration(allocation_iteration[i])
                surplus_carry = self._surplus_from_previous_iteration(prev_iter.consumers)
                va_effective = VA * allocation_iteration[i] + surplus_carry
                current_C = (current_C - current.VAPA + np.abs(current_C - current.VAPA)) / 2
                current = self._solve(va_effective, current_C)
            if current.status != "optimal":
                break
        return current

    def solve_key(
        self,
        num_iterations: int,
        C: np.ndarray,
        VA: np.ndarray,
        allocation_iteration: list[float],
    ) -> Key:
        """Full solve that reifies every iteration into an ``Iteration``,
        returning the complete ``Key``. Used once per GA solution after
        the genetic search is done."""
        current_C = C
        current: Solution | None = None
        iterations: list[Iteration] = []
        for i in range(num_iterations):
            if i == 0:
                current = self._solve(VA * allocation_iteration[i], current_C)
                if current.status != "optimal":
                    break
            else:
                if current is None:
                    raise RuntimeError("current must be populated by the i == 0 branch")
                prev_iter = current.to_iteration(allocation_iteration[i])
                surplus_carry = self._surplus_from_previous_iteration(prev_iter.consumers)
                va_effective = VA * allocation_iteration[i] + surplus_carry
                current_C = (current_C - current.VAPA + np.abs(current_C - current.VAPA)) / 2
                current = self._solve(va_effective, current_C)
                if current.status != "optimal":
                    break
            iterations.append(current.to_iteration(allocation_iteration[i]))
        return Key(iterations)
