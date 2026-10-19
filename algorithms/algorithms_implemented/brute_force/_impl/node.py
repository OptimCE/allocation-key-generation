from __future__ import annotations

from .iteration import IterationSummary


class Node:
    """Tree node for the brute-force result enumeration.

    Each node represents the application of one distribution function on
    one iteration. Children represent the next iteration's results.

    A node keeps the iteration's summary, not the iteration: the time series
    are only needed to build the next iteration. Keeping them in every node
    made memory grow with the number of candidate keys (2,574 nodes with 3
    iterations).
    """

    def __init__(self, name: str, summary: IterationSummary) -> None:
        self.name = name
        self.summary = summary
        self.children: list[Node] = []

    def add_child(self, child: Node) -> None:
        self.children.append(child)
