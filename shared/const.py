from enum import Enum, IntEnum


class GenerationStatus(IntEnum):
    PENDING = 0
    SUCCESS = 1
    FAILED = 2


class FeatureName(str, Enum):
    ALGORITHM = "algorithm"
    # SIMULATION = "simulation"  # future
