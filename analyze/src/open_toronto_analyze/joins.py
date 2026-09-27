"""Steps 3-5: score how well each pair of profiled columns joins.

3. Build a presence matrix: one row per column, one entry per distinct value.
4. Find candidate pairs: columns that share at least one *informative* value,
   i.e. one found in at least ``min_df`` and at most
   ``max(max_df_floor, max_df_fraction * n_columns)`` columns.
5. Count the shared values of each candidate pair exactly and score it:
   ``max(shared / distinct_1, shared / distinct_2)``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import sparse
from tqdm import tqdm

from .columns import ColumnRef

REF_FIELDS = list(ColumnRef._fields)  # package, resource, file, column
JOIN_COLUMNS = [
    *(f"{f}_1" for f in REF_FIELDS),
    *(f"{f}_2" for f in REF_FIELDS),
    "intersection", "distinct_1", "distinct_2",
    "containment_1_in_2", "containment_2_in_1", "score",
]


def presence_matrix(profiles) -> sparse.csr_matrix:
    """Sparse 0/1 matrix with one row per column profile and one column per distinct value."""
    codes, vocabulary = pd.factorize(np.concatenate([p.values for p in profiles]))
    indptr = np.concatenate([[0], np.cumsum([len(p.values) for p in profiles])])
    matrix = sparse.csr_matrix(
        (np.ones(len(codes), dtype=np.int32), codes, indptr),
        shape=(len(profiles), len(vocabulary)),
    )
    matrix.sort_indices()
    return matrix


def informative_values(matrix, *, min_df=2, max_df_fraction=0.005, max_df_floor=5) -> sparse.csr_matrix:
    """Keep only values that appear in neither too few nor too many columns."""
    max_df = max(max_df_floor, int(max_df_fraction * matrix.shape[0]))
    df = np.asarray(matrix.sum(axis=0)).ravel()
    return matrix[:, (df >= min_df) & (df <= max_df)]


def shared_counts(matrix, rows, cols, *, progress=False) -> np.ndarray:
    """Exact number of values shared by each (row, col) pair of the matrix."""
    counts = np.empty(len(rows), dtype=np.int64)
    ptr, idx = matrix.indptr, matrix.indices
    for k in tqdm(range(len(rows)), desc="Counting overlaps", unit="pair", disable=not progress):
        i, j = rows[k], cols[k]
        counts[k] = len(np.intersect1d(idx[ptr[i]:ptr[i + 1]], idx[ptr[j]:ptr[j + 1]], assume_unique=True))
    return counts


def find_joins(profiles, *, threshold=0.1, min_distinct=8, min_df=2, max_df_fraction=0.005,
               max_df_floor=5, progress=False) -> pd.DataFrame:
    """Joinable column pairs from different files, best first (columns: ``JOIN_COLUMNS``)."""
    if len(profiles) < 2:
        return pd.DataFrame(columns=JOIN_COLUMNS)

    presence = presence_matrix(profiles)
    sizes = np.diff(presence.indptr)
    informative = informative_values(presence, min_df=min_df, max_df_fraction=max_df_fraction,
                                     max_df_floor=max_df_floor)
    candidates = sparse.triu(informative @ informative.T, k=1).tocoo()
    rows, cols = candidates.row, candidates.col

    shared = shared_counts(presence, rows, cols, progress=progress)
    c12, c21 = shared / sizes[rows], shared / sizes[cols]
    score = np.maximum(c12, c21)
    keep = (score > threshold) & (np.minimum(sizes[rows], sizes[cols]) >= min_distinct)

    records = []
    for i, j, n, a, b, s in zip(rows[keep], cols[keep], shared[keep], c12[keep], c21[keep], score[keep], strict=True):
        ref1, ref2 = profiles[i].ref, profiles[j].ref
        if (ref1.package, ref1.file) != (ref2.package, ref2.file):
            records.append((*ref1, *ref2, n, sizes[i], sizes[j], a, b, s))

    joins = pd.DataFrame.from_records(records, columns=JOIN_COLUMNS)
    return joins.sort_values("score", ascending=False, kind="stable", ignore_index=True)
