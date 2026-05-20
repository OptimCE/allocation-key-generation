from enum import IntEnum, StrEnum


class GenerationStatus(IntEnum):
    PENDING = 0
    SUCCESS = 1
    FAILED = 2


class FeatureName(StrEnum):
    ALGORITHM = "algorithm"
    # SIMULATION = "simulation"  # future
