"""Shared data loading for allocation-key algorithms.

All algorithms consume the same file format — a tabular file (CSV or
XLSX) with one column per consumer plus one "injection" column
containing the shared production profile. This module converts the raw
file bytes into the ``(C, VA, consumer_names)`` triple expected by
``AlgorithmRawData``.

Kept under ``shared/`` so algorithms can stay pure: the worker loads
the file once and passes matrices to whichever algorithm is selected.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import numpy as np
import pandas as pd

from algorithms.base import AlgorithmRawData


class InvalidInjectionColumnError(ValueError):
    """Raised when the configured production column is absent from the file."""


class UnsupportedFileFormatError(ValueError):
    """Raised when the file extension is not one of csv / xlsx / xls."""


@dataclass(frozen=True)
class LoadedDataFile:
    """Lightly-typed wrapper around the parsed dataframe + injection column."""

    dataframe: pd.DataFrame
    injection_name: str


def parse_file(content: bytes, file_name: str) -> pd.DataFrame:
    """Parse raw file bytes into a pandas DataFrame.

    Supports CSV and Excel (xlsx / xls). The file name is used to select
    the parser via its extension.
    """
    extension = file_name.rsplit(".", 1)[-1].lower()
    if extension == "csv":
        return pd.read_csv(BytesIO(content))
    if extension in ("xlsx", "xls"):
        return pd.read_excel(BytesIO(content), engine="openpyxl")
    raise UnsupportedFileFormatError(f"Unsupported file extension: {extension!r}")


def to_algorithm_raw_data(dataframe: pd.DataFrame, injection_name: str) -> AlgorithmRawData:
    """Convert a parsed DataFrame into an ``AlgorithmRawData``.

    Layout:
      - ``C`` — consumption matrix, shape ``(num_consumers, T)``. Each
        row is one consumer's time series.
      - ``VA`` — production matrix of the same shape, with the injection
        column broadcast across every consumer row at each timestep.
      - ``consumer_names`` — column names excluding the injection column.
    """
    if injection_name not in dataframe.columns:
        raise InvalidInjectionColumnError(f"Injection column {injection_name!r} not found in file")

    consumer_columns = [c for c in dataframe.columns if c != injection_name]
    consumption = dataframe[consumer_columns].to_numpy().transpose()
    production_series = dataframe[injection_name].to_numpy()

    VA = np.tile(production_series, (consumption.shape[0], 1))
    return AlgorithmRawData(
        C=consumption,
        VA=VA,
        consumer_names=[str(c) for c in consumer_columns],
    )


def load(content: bytes, file_name: str, injection_name: str) -> AlgorithmRawData:
    """One-shot helper: parse bytes and convert to ``AlgorithmRawData``."""
    dataframe = parse_file(content, file_name)
    return to_algorithm_raw_data(dataframe, injection_name)
