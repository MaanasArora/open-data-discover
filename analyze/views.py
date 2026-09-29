"""Readable views over stored results: the join table, a column's joins, and column lookup."""

from __future__ import annotations

import pandas as pd

from results import Results

NAME_FIELDS = ["package", "resource", "file", "column"]


def label(column: pd.Series) -> str:
    """Human-readable unique name of a column row: ``package/file::column``."""
    return f"{column['package']}/{column['file']}::{column['column']}"


def join_table(results: Results) -> pd.DataFrame:
    """Every join with both columns' names instead of ids, best first."""
    joins, names = results.joins, results.columns[NAME_FIELDS]
    side_a = names.iloc[joins["id_a"]].reset_index(drop=True).add_suffix("_a")
    side_b = names.iloc[joins["id_b"]].reset_index(drop=True).add_suffix("_b")
    distinct = pd.DataFrame({
        "distinct_a": results.columns["n_distinct"].iloc[joins["id_a"]].to_numpy(),
        "distinct_b": results.columns["n_distinct"].iloc[joins["id_b"]].to_numpy(),
    })
    stats = joins.drop(columns=["id_a", "id_b"])
    return pd.concat([side_a, side_b, distinct, stats], axis=1)


def joins_of(results: Results, column_id: int) -> pd.DataFrame:
    """The columns that join with one column, best first: each other column's row
    from ``columns`` plus ``shared``, ``score``, ``jaccard``, ``contained`` (the share
    of the chosen column's values found in the other column), ``expected`` (the share
    expected by chance) and ``evidence`` (that the other column contains them)."""
    joins = results.joins.drop(columns="evidence")  # replaced by this column's direction below
    as_a, as_b = joins[joins["id_a"] == column_id], joins[joins["id_b"] == column_id]
    matches = pd.concat([
        as_a.rename(columns={"id_b": "id", "containment_a_in_b": "contained", "expected_a_in_b": "expected",
                             "evidence_a_in_b": "evidence"}),
        as_b.rename(columns={"id_a": "id", "containment_b_in_a": "contained", "expected_b_in_a": "expected",
                             "evidence_b_in_a": "evidence"}),
    ])[["id", "contained", "expected", "evidence", "shared", "jaccard", "score"]]
    others = matches.merge(results.columns, on="id")
    return others.sort_values("score", ascending=False, kind="stable", ignore_index=True)


def find_columns(columns: pd.DataFrame, query: str) -> pd.DataFrame:
    """Rows of ``columns`` matching a query, which is one of:

    * a key (``RESOURCE_ID::COLUMN``) or label (``PACKAGE/FILE::COLUMN``), matched exactly;
    * ``[PACKAGE/]RESOURCE::COLUMN`` where RESOURCE is the resource or file name;
    * just ``COLUMN``.

    Name matching ignores case.
    """
    labels = columns.apply(label, axis=1)
    exact = columns[(columns["key"] == query) | (labels == query)]
    if len(exact):
        return exact

    where, _, column = query.lower().rpartition("::")
    package, _, resource = where.rpartition("/")
    lower = {f: columns[f].str.lower() for f in NAME_FIELDS}
    match = lower["column"] == column
    if package:
        match &= lower["package"] == package
    if resource:
        match &= (lower["resource"] == resource) | (lower["file"] == resource)
    return columns[match]
