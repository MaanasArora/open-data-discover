"""Use a saved join table: resolve a column, list what it joins with, sample its values."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from columns import ColumnRef, normalize, read_csv
from joins import JOIN_COLUMNS, REF_FIELDS


def load_joins(path) -> pd.DataFrame:
    text_columns = [c for c in JOIN_COLUMNS if c.rsplit("_", 1)[0] in REF_FIELDS]
    return pd.read_csv(path, dtype={c: str for c in text_columns})


def columns_in(joins: pd.DataFrame) -> set:
    refs = set()
    for side in ("1", "2"):
        side_columns = [f"{f}_{side}" for f in REF_FIELDS]
        refs.update(ColumnRef(*row) for row in joins[side_columns].itertuples(index=False))
    return refs


def find_columns(joins: pd.DataFrame, query: str) -> list:
    """Resolve ``[PACKAGE/]RESOURCE::COLUMN``, ``COLUMN`` or an exact column id.

    RESOURCE matches the resource name or the file name; matching ignores case.
    """
    refs = columns_in(joins)
    exact = [r for r in refs if r.id == query]
    if exact:
        return exact

    query = query.lower()
    where, _, column = query.rpartition("::")
    package, _, resource = where.rpartition("/")

    def matches(ref: ColumnRef) -> bool:
        return (
            ref.column.lower() == column
            and package in ("", ref.package.lower())
            and resource in ("", ref.resource.lower(), ref.file.lower())
        )

    return sorted((r for r in refs if matches(r)), key=lambda r: r.id)


def joins_for(joins: pd.DataFrame, ref: ColumnRef) -> pd.DataFrame:
    """The columns joinable with ``ref``: its ref fields plus score and intersection, best first."""
    out_columns = REF_FIELDS + ["score", "intersection"]
    parts = []
    for me, other in (("1", "2"), ("2", "1")):
        mine = (
            (joins[f"package_{me}"] == ref.package)
            & (joins[f"file_{me}"] == ref.file)
            & (joins[f"column_{me}"] == ref.column)
        )
        part = joins.loc[mine, [f"{f}_{other}" for f in REF_FIELDS] + ["score", "intersection"]]
        parts.append(part.set_axis(out_columns, axis=1))
    return pd.concat(parts).sort_values("score", ascending=False, kind="stable", ignore_index=True)


def sample_values(root, ref: ColumnRef, *, n=3, seed=42, max_rows=None) -> list:
    """A few distinct normalized values of a column, read from the data folder."""
    try:
        table = read_csv(Path(root) / ref.package / ref.file, usecols=[ref.column], max_rows=max_rows)
    except (OSError, ValueError):
        return []
    values = normalize(table[ref.column]).unique()
    rng = np.random.default_rng(seed)
    return [str(v) for v in rng.choice(values, size=min(n, len(values)), replace=False)]
