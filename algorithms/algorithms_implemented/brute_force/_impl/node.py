from __future__ import annotations

from .iteration import Iteration


class Node:
    """Tree node for the brute-force result enumeration.

    Each node represents the application of one distribution function on
    one iteration. Children represent the next iteration's results.
    """

    def __init__(self, name: str, iteration: Iteration, surplus: float) -> None:
        self.name = name
        self.iteration = iteration
        self.surplus = surplus
        self.children: list[Node] = []

    def add_child(self, child: "Node") -> None:
        self.children.append(child)
