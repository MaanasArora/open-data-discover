"""Step 2: read the CSV files of the selected packages and profile their columns.

A column's profile is its set of distinct values after ``strip().lower()``,
plus a few statistics and sample values. Columns of numbers or dates also get the
density of each value (see ordered.py), which joins.py uses as part of the null.
Columns whose values are short on average (counts, small ids, codes) are skipped
to keep the number of pairs manageable; they would mostly score low anyway.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

from ordered import Order, order

log = logging.getLogger(__name__)

EXCLUDED_COLUMNS = {"_id"}  # row numbers added by the CKAN datastore


@dataclass(frozen=True)
class CSVFile:
    package: str
    resource_id: str
    resource: str  # CKAN resource name
    path: Path


@dataclass
class ColumnProfile:
    package: str
    resource_id: str
    resource: str
    file: str  # file name inside the package folder
    column: str
    values: np.ndarray  # distinct normalized values
    n_values: int  # non-empty cells
    avg_length: float  # average length of the raw values
    samples: list  # a few distinct values, for display
    order: Order | None = None  # for numbers and dates; see ordered.py
    density: np.ndarray | None = None  # for numbers and dates: the density at each value

    @property
    def kind(self) -> str:
        """"number", "date", or "" for other values."""
        return self.order.kind if self.order else ""

    @property
    def key(self) -> str:
        """Stable identifier: resource ids survive renames upstream."""
        return f"{self.resource_id}::{self.column}"


def csv_files(packages) -> list:
    """Every downloaded CSV file of the given packages."""
    return [
        CSVFile(package.name, resource["id"], resource.get("name") or path.stem, path)
        for package in packages
        for resource, path in package.resources()
        if path.suffix.lower() == ".csv"
    ]


def read_csv(path, *, max_rows=None) -> pd.DataFrame:
    """Read a CSV as text (keeps values like ``00123``), skipping bad lines.
    Falls back to Latin-1 for files that are not UTF-8."""
    options = dict(dtype=str, nrows=max_rows, on_bad_lines="skip", low_memory=False)
    try:
        return pd.read_csv(path, encoding="utf-8-sig", **options)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin-1", **options)


def normalize(values: pd.Series) -> pd.Series:
    return values.dropna().str.strip().str.lower()


def profile_columns(files, *, min_avg_length=6, max_rows=None, n_samples=5, seed=42, progress=False) -> list:
    """Profile every column whose average value length exceeds ``min_avg_length``.

    Each file is read once; only the distinct values are kept in memory.
    """
    rng = np.random.default_rng(seed)
    profiles = []
    for file in tqdm(files, desc="Profiling columns", unit="file", disable=not progress):
        try:
            table = read_csv(file.path, max_rows=max_rows)
        except Exception as e:  # a malformed file shouldn't stop the run
            log.warning("Skipping %s: %s", file.path, e)
            continue
        for column in table.columns:
            values = table[column].dropna()
            if column in EXCLUDED_COLUMNS or values.empty:
                continue
            avg_length = values.str.len().mean()
            if avg_length <= min_avg_length:
                continue
            distinct = normalize(values).unique()
            samples = rng.choice(distinct, size=min(n_samples, len(distinct)), replace=False).tolist()
            ordered = order(distinct)
            density = ordered.density(ordered.positions, own=True) if ordered else None
            profiles.append(ColumnProfile(file.package, file.resource_id, file.resource, file.path.name,
                                          str(column), distinct, len(values), float(avg_length), samples,
                                          ordered, density))
    return profiles
