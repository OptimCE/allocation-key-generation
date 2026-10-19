from typing import cast

import cvxpy as cp
import numpy as np

from .iteration import Iteration
from .key import Key
from .solution import Solution


class WarmStartSolver:
    """Convex solver for a single OLAGSA iteration.

    Minimises the total surplus ``sum(E)`` where ``E = max(VAPA - C, 0)``,
    subject to ``sum(p) == 1`` and ``p[i] >= 0``, with
    ``VAPA[i, t] = p[i] * va[t]``. ``VA`` is always one production profile
    tiled across the consumer rows (``shared.data_loading`` builds it with
    ``np.tile``, and so does the surplus carry below), so the allocation is a
    vector times a single ``(1, T)`` row rather than a diagonal matrix times
    the whole ``(n, T)`` tile. ``_solve`` refuses a ``VA`` that is not tiled.

    The problem is built once and re-solved with new ``VA`` / ``C`` values,
    but compiled with ``ignore_dpp=True``. Under DPP, cvxpy's compile cost
    grows with (parameter entries x variables), and ``C`` alone holds n*T
    entries: 20 meters over 92 days of quarter-hours reserved ~130 GB of
    address space and got the solver process OOM-killed during the first
    solve. A plain recompile per solve stays linear in n*T. The LP handed to
    the solver is the same as before: allocations differ from the former
    ``diag(VAP) @ VA`` formulation only by float rounding (1e-14 to 3e-10
    measured), inside the solver's own 1e-8 tolerance.
    """

    def __init__(self, c_shape: tuple[int, ...], va_shape: tuple[int, ...], max_iter: int) -> None:
        num_consumers, horizon = c_shape
        if tuple(va_shape) != (num_consumers, horizon):
            raise ValueError(f"VA shape {va_shape} does not match C shape {c_shape}")
        self.max_iter = max_iter
        self.p = cp.Variable(num_consumers)
        self.va = cp.Parameter((1, horizon), nonneg=True)
        self.C = cp.Parameter((num_consumers, horizon), nonneg=True)
        self.VAPA = cp.reshape(self.p, (num_consumers, 1), order="F") @ self.va

        constraints = [cp.sum(self.p) == 1]
        for i in range(num_consumers):
            constraints.append(self.p[i] >= 0)

        self.E = ((self.VAPA - self.C) + cp.abs(self.VAPA - self.C)) / 2
        self.problem = cp.Problem(cp.Minimize(cp.sum(self.E)), constraints)

    def _solve(self, VA: np.ndarray, C: np.ndarray) -> Solution:
        if not np.array_equal(VA, np.broadcast_to(VA[:1], VA.shape)):
            raise ValueError("VA must repeat one production profile on every consumer row")
        self.va.value = VA[:1]
        self.C.value = C
        self.problem.solve(max_iter=self.max_iter, warm_start=True, ignore_dpp=True)
        # cvxpy declares .value as ndarray | None; populated after a feasible
        # solve. Callers gate further use on Solution.status == "optimal".
        p = self.p.value
        return Solution(
            self.problem.status,
            cast(np.ndarray, None if p is None else np.diag(p)),
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
