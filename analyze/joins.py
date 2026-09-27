"""Steps 3-5: find joinable pairs of profiled columns and score them.

3. Build a presence matrix: one row per column, one entry per distinct value.
4. Find candidate pairs: columns from different files that each have at least
   ``min_distinct`` distinct values and share at least one *informative* value,
   i.e. one found in at least ``min_df`` and at most
   ``max(max_df_floor, max_df_fraction * n_columns)`` columns.
5. Count each candidate pair's shared values exactly and score it:

       containment_a_in_b = weight of shared values / weight of a's values   (and the reverse)
       jaccard            = weight of shared values / weight of a's and b's values together
       score              = max(containment_a_in_b, containment_b_in_a)

   Every value weighs 1 by default. With ``idf=True`` values are weighted by how rare
   they are across datasets (N datasets, df(v) of them containing v):

       weight(v) = log((1 + N) / (1 + df(v))) + 1

   Datasets, not columns, are counted, so a dataset shipping the same table in several
   files doesn't make its values look common. IDF is off by default: on Toronto's data
   the most widespread values are ward names, i.e. real join keys, so it mostly lowered
   good joins (wards) and raised weak ones (dates).

No score threshold is applied: every candidate pair is kept, best first.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import sparse
from tqdm import tqdm

JOIN_FIELDS = ["id_a", "id_b", "shared", "containment_a_in_b", "containment_b_in_a", "jaccard", "score"]


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


def value_weights(matrix, packages, *, idf=True) -> np.ndarray:
    """IDF weight of each value (matrix column), counting datasets rather than columns."""
    if not idf:
        return np.ones(matrix.shape[1])
    ids, names = pd.factorize(pd.Series(packages))
    column_to_package = sparse.csr_matrix(
        (np.ones(len(ids)), (ids, np.arange(len(ids)))), shape=(len(names), len(ids))
    )
    df = np.asarray(((column_to_package @ matrix) > 0).sum(axis=0)).ravel()
    return np.log((1 + len(names)) / (1 + df)) + 1


def shared_values(matrix, weights, rows, cols, *, progress=False):
    """Number and total weight of the values shared by each (row, col) pair of the matrix."""
    counts, totals = np.empty(len(rows), dtype=np.int64), np.empty(len(rows))
    ptr, idx = matrix.indptr, matrix.indices
    for k in tqdm(range(len(rows)), desc="Counting shared values", unit="pair", disable=not progress):
        i, j = rows[k], cols[k]
        common = np.intersect1d(idx[ptr[i]:ptr[i + 1]], idx[ptr[j]:ptr[j + 1]], assume_unique=True)
        counts[k], totals[k] = len(common), weights[common].sum()
    return counts, totals


def find_joins(profiles, *, idf=False, min_distinct=8, min_df=2, max_df_fraction=0.005, max_df_floor=5,
               progress=False) -> pd.DataFrame:
    """Scored candidate pairs (columns: ``JOIN_FIELDS``), best first.

    ``id_a`` and ``id_b`` are indices into ``profiles`` with ``id_a < id_b``.
    """
    if len(profiles) < 2:
        return pd.DataFrame(columns=JOIN_FIELDS)

    presence = presence_matrix(profiles)
    informative = informative_values(presence, min_df=min_df, max_df_fraction=max_df_fraction,
                                     max_df_floor=max_df_floor)
    candidates = sparse.triu(informative @ informative.T, k=1).tocoo()
    a, b = candidates.row, candidates.col

    n = np.diff(presence.indptr)
    file_ids, _ = pd.factorize(pd.Series([f"{p.package}/{p.file}" for p in profiles]))
    keep = (file_ids[a] != file_ids[b]) & (np.minimum(n[a], n[b]) >= min_distinct)
    a, b = a[keep], b[keep]

    weights = value_weights(presence, [p.package for p in profiles], idf=idf)
    total = presence @ weights  # weight of each column's values
    shared, shared_weight = shared_values(presence, weights, a, b, progress=progress)
    joins = pd.DataFrame({
        "id_a": a.astype(np.int32),
        "id_b": b.astype(np.int32),
        "shared": shared.astype(np.int32),
        "containment_a_in_b": shared_weight / total[a],
        "containment_b_in_a": shared_weight / total[b],
        "jaccard": shared_weight / (total[a] + total[b] - shared_weight),
    })
    joins["score"] = joins[["containment_a_in_b", "containment_b_in_a"]].max(axis=1)
    return joins.sort_values(["score", "id_a", "id_b"], ascending=[False, True, True], ignore_index=True)
