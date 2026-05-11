"""Brute-force allocation key generator.

Exhaustively enumerates allocation keys by combining the three
distribution functions (``egalitaire``, ``prorata_total``, ``prorata``)
across 1, 2 or 3 nested iterations, sweeping the per-iteration energy
share in 10% increments.
"""

from __future__ import annotations

import numpy as np

from .functions import ALGO_NAMES, ALGOS
from .iteration import Iteration
from .consumer import Consumer
from .node import Node


def _reformat_excedents(VA_shape: tuple[int, int], surplus: np.ndarray) -> np.ndarray:
    """Broadcast a per-time-step surplus vector back into a (num_consumers, T) matrix.

    Mirrors the legacy ``BruteForceGenerator.reformat_excedents`` helper:
    each row is a copy of ``surplus`` so the algorithm can keep treating
    ``energy_allocated`` as a 2D matrix.
    """
    new_va = np.zeros(VA_shape)
    for i in range(VA_shape[1]):
        new_va[:, i] = np.array([float(surplus[i])] * VA_shape[0])
    return new_va


def _make_root_iteration(C: np.ndarray, VA: np.ndarray) -> Iteration:
    consumers = [
        Consumer(consumption=np.array(C[i], dtype=float)) for i in range(len(C))
    ]
    return Iteration(consumers, VA)


class BruteForceGenerator:
    def __init__(self, nb_iterations: int) -> None:
        if nb_iterations not in (1, 2, 3):
            raise ValueError(f"nb_iterations must be 1, 2 or 3 (got {nb_iterations})")
        self.nb_iterations = nb_iterations

    def generate(self, C: np.ndarray, VA: np.ndarray) -> list[Node]:
        """Run the enumeration. Returns a list of root :class:`Node` trees."""
        C = np.asarray(C, dtype=float)
        VA = np.asarray(VA, dtype=float)
        root = _make_root_iteration(C, VA)

        if self.nb_iterations == 1:
            return self._one_iteration(root)
        if self.nb_iterations == 2:
            return self._two_iterations(root, VA)
        return self._three_iterations(root, VA)

    # ------------------------------------------------------------------
    # 1 iteration
    # ------------------------------------------------------------------
    def _one_iteration(self, root: Iteration) -> list[Node]:
        results: list[Node] = []
        for algo in ALGO_NAMES:
            it = root.copy()
            it.energy_allocated_percentage = 1.0
            node, _ = ALGOS[algo](it)
            results.append(node)
        return results

    # ------------------------------------------------------------------
    # 2 iterations
    # ------------------------------------------------------------------
    def _two_iterations(self, root: Iteration, VA: np.ndarray) -> list[Node]:
        results: list[Node] = []
        for i in range(11):
            pct1 = i / 10
            pct2 = 1 - i / 10
            for algo1 in ALGO_NAMES:
                node1 = self._apply_first(root, pct1, algo1)
                iter1_post = node1.iteration

                base_iter2 = self._build_next_iteration(root, iter1_post, VA, pct2)
                for algo2 in ALGO_NAMES:
                    iter2_in = base_iter2.copy()
                    node2, _ = ALGOS[algo2](iter2_in)
                    node1.add_child(node2)
                results.append(node1)
        return results

    # ------------------------------------------------------------------
    # 3 iterations
    # ------------------------------------------------------------------
    def _three_iterations(self, root: Iteration, VA: np.ndarray) -> list[Node]:
        results: list[Node] = []
        for i in range(11):
            for j in range(0, (10 - i) + 1):
                pct1 = i / 10
                pct2 = j / 10
                pct3_raw = 1 - pct1 - pct2
                pct3 = pct3_raw if pct3_raw > 0 else 0
                for algo1 in ALGO_NAMES:
                    node1 = self._apply_first(root, pct1, algo1)
                    iter1_post = node1.iteration

                    base_iter2 = self._build_next_iteration(root, iter1_post, VA, pct2)
                    for algo2 in ALGO_NAMES:
                        iter2_in = base_iter2.copy()
                        node2, iter2_post = ALGOS[algo2](iter2_in)

                        base_iter3 = self._build_next_iteration(
                            root, iter2_post, VA, pct3
                        )
                        for algo3 in ALGO_NAMES:
                            iter3_in = base_iter3.copy()
                            node3, _ = ALGOS[algo3](iter3_in)
                            node2.add_child(node3)
                        node1.add_child(node2)
                    results.append(node1)
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _apply_first(self, root: Iteration, pct: float, algo: str) -> Node:
        """Apply the first iteration's distribution function on a fresh copy of the root."""
        it = root.copy()
        it.energy_allocated_percentage = pct
        # Scale the production matrix by the percentage assigned to this iteration.
        it.energy_allocated = root.energy_allocated * pct
        node, _ = ALGOS[algo](it)
        return node

    def _build_next_iteration(
        self,
        root: Iteration,
        previous_post: Iteration,
        VA: np.ndarray,
        pct: float,
    ) -> Iteration:
        """Build the next iteration from the post-state of the previous one.

        - Carries over consumer state (so the consumption rolls down to
          ``residual_volume``).
        - Sets the next iteration's ``energy_allocated`` to
          ``root_VA * pct + reformat_excedents(prev.surplus)``.
        - Resets each consumer's consumption to its residual volume from
          the previous iteration.
        """
        next_iter = previous_post.copy()
        next_iter.energy_allocated_percentage = pct
        next_iter.energy_allocated = (
            root.energy_allocated * pct
        ) + _reformat_excedents(VA.shape, previous_post.surplus)
        for c in next_iter.consumers:
            c.consumption = np.array(c.residual_volume, copy=True)
        return next_iter
