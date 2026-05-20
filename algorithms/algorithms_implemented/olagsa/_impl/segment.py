import math

from .coordinate import Coordinate, Coordinate3D


class Segment:
    def __init__(self, point_a: Coordinate, point_b: Coordinate) -> None:
        self.point_a = point_a
        self.point_b = point_b

    def point_within(self, c: Coordinate) -> bool:
        crossproduct = (c.y - self.point_a.y) * (self.point_b.x - self.point_a.x) - (
            c.x - self.point_a.x
        ) * (self.point_b.y - self.point_a.y)
        if abs(crossproduct) > 0.01:
            return False

        dotproduct = (c.x - self.point_a.x) * (self.point_b.x - self.point_a.x) + (
            c.y - self.point_a.y
        ) * (self.point_b.y - self.point_a.y)
        if dotproduct < 0:
            return False

        squared_length = (self.point_b.x - self.point_a.x) ** 2 + (
            self.point_b.y - self.point_a.y
        ) ** 2
        return dotproduct <= squared_length


def _distance_3d(a: Coordinate3D, b: Coordinate3D) -> float:
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


class Triangle(Segment):
    def __init__(self, point_a: Coordinate3D, point_b: Coordinate3D, point_c: Coordinate3D) -> None:
        super().__init__(point_a, point_b)
        # Re-bind point_a/point_b with the narrower Coordinate3D type so
        # _distance_3d and Coordinate3D.z accesses type-check. Segment
        # stores them as Coordinate.
        self.point_a: Coordinate3D = point_a
        self.point_b: Coordinate3D = point_b
        self.point_c = point_c
        self.side_length = _distance_3d(point_a, point_b)

    def point_within(self, point: Coordinate3D) -> bool:  # type: ignore[override]
        return (
            _distance_3d(self.point_a, point) <= self.side_length
            and _distance_3d(self.point_b, point) <= self.side_length
            and _distance_3d(self.point_c, point) <= self.side_length
        )
