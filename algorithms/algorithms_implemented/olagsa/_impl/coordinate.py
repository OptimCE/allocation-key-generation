class Coordinate:
    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y

    def __repr__(self) -> str:
        return f"({self.x},{self.y})"


class Coordinate3D(Coordinate):
    def __init__(self, x: float, y: float, z: float) -> None:
        super().__init__(x, y)
        self.z = z

    def __repr__(self) -> str:
        return f"({self.x},{self.y},{self.z})"

    def divide(self, by: float) -> "Coordinate3D":
        self.x /= by
        self.y /= by
        self.z /= by
        return self


def sum_coordinates_3d(a: Coordinate3D, b: Coordinate3D) -> Coordinate3D:
    return Coordinate3D(a.x + b.x, a.y + b.y, a.z + b.z)
