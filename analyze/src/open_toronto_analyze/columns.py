"""Step 2: read the CSV files of the selected packages and profile their columns.

A column's profile is its set of distinct values after ``strip().lower()``.
Columns whose values are short on average (IDs, counts, codes) are skipped,
since they overlap with almost everything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd
from tqdm import tqdm

log = logging.getLogger(__name__)

EXCLUDED_COLUMNS = {"_id"}  # row numbers added by the CKAN datastore


class ColumnRef(NamedTuple):
    package: str
    resource: str  # CKAN resource name
    file: str  # file name inside the package folder
    column: str

    @property
    def id(self) -> str:
        return f"{self.package}/{self.file}::{self.column}"


@dataclass
class ColumnProfile:
    ref: ColumnRef
    values: np.ndarray  # distinct normalized values


@dataclass(frozen=True)
class CSVFile:
    package: str
    resource: str
    path: Path


def csv_files(packages) -> list:
    """Every downloaded CSV file of the given packages."""
    return [
        CSVFile(package.name, resource.get("name") or path.stem, path)
        for package in packages
        for resource, path in package.resources()
        if path.suffix.lower() == ".csv"
    ]


def read_csv(path, *, usecols=None, max_rows=None) -> pd.DataFrame:
    """Read a CSV as text (keeps values like ``00123``), skipping bad lines.
    Falls back to Latin-1 for files that are not UTF-8."""
    options = dict(dtype=str, usecols=usecols, nrows=max_rows, on_bad_lines="skip", low_memory=False)
    try:
        return pd.read_csv(path, encoding="utf-8-sig", **options)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin-1", **options)


def normalize(values: pd.Series) -> pd.Series:
    return values.dropna().str.strip().str.lower()


def profile_columns(files, *, min_avg_length=8, max_rows=None, progress=False) -> list:
    """Profile every column whose average value length exceeds ``min_avg_length``.

    Each file is read once; only the distinct values are kept in memory.
    """
    profiles = []
    for file in tqdm(files, desc="Profiling columns", unit="file", disable=not progress):
        try:
            table = read_csv(file.path, max_rows=max_rows)
        except Exception as e:  # a malformed file shouldn't stop the run
            log.warning("Skipping %s: %s", file.path, e)
            continue
        for column in table.columns:
            values = table[column].dropna()
            if column in EXCLUDED_COLUMNS or values.empty or values.str.len().mean() <= min_avg_length:
                continue
            ref = ColumnRef(file.package, file.resource, file.path.name, str(column))
            profiles.append(ColumnProfile(ref, normalize(values).unique()))
    return profiles
