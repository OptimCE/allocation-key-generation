from enum import IntEnum, StrEnum


class GenerationStatus(IntEnum):
    PENDING = 0
    SUCCESS = 1
    FAILED = 2


class FeatureName(StrEnum):
    ALGORITHM = "algorithm"
    # SIMULATION = "simulation"  # future


class DataSource(IntEnum):
    """Where a generation's input timeseries comes from.

    FILE is the historical path (a CSV/XLSX uploaded by the manager). CRM
    reads the same numbers straight out of the core database's
    ``meter_consumption`` for one sharing operation over a date range.
    """

    FILE = 1
    CRM = 2
