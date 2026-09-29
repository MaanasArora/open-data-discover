"""Steps 3-5: score how strongly each pair of columns looks joinable.

Two columns are a candidate pair when they share at least one value and are in
different files. Each pair gets:

- ``score``, between 0 and 1: the share of one column's values found in the other,
  beyond what chance explains, ``(containment - expected) / (1 - expected)``, for the
  direction where it is larger. 100% means every value is found; 0% means no more are
  found than chance would put there.
- ``evidence``, in nats: how sure that overlap is not chance (below). Pairs with less
  than ``min_evidence`` are dropped, so a score is only reported when it is trustworthy;
  a few shared values between small columns can score high but carry little evidence.

Evidence that B contains A's values (``evidence_a_in_b``)
---------------------------------------------------------
Each distinct value v of A is a trial: is v also in B? Under the null, B contains v
with probability p0(v). If the columns are linked, B contains A's values at one rate c,
estimated as the containment k / n (k of A's n distinct values are in B). The evidence
is the log-likelihood ratio of the two explanations:

    evidence = sum over the k shared values:   ln(c / p0(v))
             + sum over the n - k others:      ln((1 - c) / (1 - p0(v)))

Shared values that B was unlikely to hold count for a lot; values B was likely to hold
but lacks count against. The evidence is 0 unless the containment beats the average p0
of A's values (``expected_a_in_b``, the containment expected by chance). It grows with
the number of values, so large columns that match well score highest.

The null: how B could contain v anyway
--------------------------------------
p0(v) is the larger of two estimates:

- Co-occurrence: the share of the other columns that contain v, counting only columns
  unrelated to A. Column X is related to A as far as the two overlap apart from v
  (shared distinct values over the smaller column's count), and weighs 1 - overlap. So
  a ward name found in 30 ward columns stays rare, since those columns hold the other
  wards too, while "toronto", found in 30 columns with little else in common, is common.
  A and B are left out, and one pseudo-column keeps p0 above 0:

      p0(v) = (weight of the other columns containing v + 1) / (weight of all other columns + 1)

- Density, when B holds numbers or dates (see ordered.py): the share of the lattice
  points around v that B fills. A column with every id from 1 to 800, or every day of
  2023, holds any value in its range, so sharing one of them is no evidence. It is used
  for the shared values only: for the values B lacks, p0 is the co-occurrence estimate,
  which can only lower the evidence.

A pair's evidence is the larger of its two directions. At 10 nats the overlap is e**10,
about 22,000 times, likelier if the columns are linked than under the null.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import sparse
from tqdm import tqdm

JOIN_FIELDS = ["id_a", "id_b", "shared", "containment_a_in_b", "containment_b_in_a", "jaccard",
               "expected_a_in_b", "expected_b_in_a", "evidence_a_in_b", "evidence_b_in_a", "evidence", "score"]
CHUNK = 4_000_000  # (column, column, value) triples processed at a time
MAX_P0 = 1 - 1e-9  # keeps ln(1 - p0) finite


def presence_matrix(profiles) -> tuple[sparse.csr_matrix, np.ndarray]:
    """Sparse 0/1 matrix with one row per column profile and one column per distinct value.

    Also returns, for each stored entry, the index of its value in the concatenated
    ``values`` of all profiles, to line up per-value arrays with the entries.
    """
    codes, vocabulary = pd.factorize(np.concatenate([p.values for p in profiles]))
    lengths = np.array([len(p.values) for p in profiles])
    rows = np.repeat(np.arange(len(profiles)), lengths)
    entry = np.lexsort((codes, rows))  # entries sorted by row, then value
    indptr = np.concatenate([[0], np.cumsum(lengths)])
    matrix = sparse.csr_matrix((np.ones(len(codes), dtype=np.int32), codes[entry], indptr),
                               shape=(len(profiles), len(vocabulary)))
    return matrix, entry


def ranges(starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    """``concatenate([arange(s, e) for s, e in zip(starts, ends)])``, vectorized."""
    lengths = ends - starts
    return np.repeat(starts - np.cumsum(lengths) + lengths, lengths) + np.arange(lengths.sum())


def triples(presence: sparse.csr_matrix, chunk: int = CHUNK):
    """Yield ``(ea, eb)``, entry indices of (A, v) and (B, v) for every value v and every
    ordered pair of distinct columns A, B that contain it, some values at a time."""
    by_value = sparse.csr_matrix((np.arange(presence.nnz), presence.indices, presence.indptr),
                                 shape=presence.shape).tocsc()
    ptr, entries = by_value.indptr, by_value.data
    df = np.diff(ptr).astype(np.int64)
    values = np.flatnonzero(df >= 2)
    if not len(values):
        return
    work = np.cumsum(df[values] ** 2)
    cuts = np.searchsorted(work, np.arange(chunk, work[-1], chunk))
    for group in np.split(values, np.unique(cuts)):
        if not len(group):
            continue
        pos = ranges(ptr[group], ptr[group + 1])  # each value's entries ...
        size = np.repeat(df[group], df[group])
        a = np.repeat(pos, size)  # ... each paired with every entry of the same value
        b = ranges(np.repeat(ptr[group], df[group]), np.repeat(ptr[group + 1], df[group]))
        keep = a != b
        yield entries[a[keep]], entries[b[keep]]


def find_joins(profiles, *, min_evidence=10.0, progress=False) -> pd.DataFrame:
    """Scored pairs of columns (columns: ``JOIN_FIELDS``), best first.

    ``id_a`` and ``id_b`` are indices into ``profiles`` with ``id_a < id_b``.
    """
    if len(profiles) < 2:
        return pd.DataFrame(columns=JOIN_FIELDS)

    presence, entry = presence_matrix(profiles)
    n_cols = presence.shape[0]
    n = np.diff(presence.indptr)  # distinct values per column
    row = np.repeat(np.arange(n_cols), n)  # the column of each entry
    df = np.bincount(presence.indices, minlength=presence.shape[1])[presence.indices]  # per entry
    density = np.concatenate([np.zeros(len(p.values)) if p.density is None else p.density
                              for p in profiles])[entry]  # B's density at each of its values

    # Every pair of columns sharing a value (A = a, B = b), and how much they overlap.
    shared = (presence @ presence.T).tocsr()
    shared.sort_indices()
    a, b = np.repeat(np.arange(n_cols), np.diff(shared.indptr)), shared.indices
    key = a.astype(np.int64) * n_cols + b
    smaller = np.minimum(n[a], n[b])
    overlap = shared.data / smaller
    overlap_without = np.where(smaller > 1, (shared.data - 1) / np.maximum(smaller - 1, 1), 0.0)  # less one value

    def pair_of(ea, eb):
        return np.searchsorted(key, row[ea].astype(np.int64) * n_cols + row[eb])

    # Weight of the columns other than A (unrelated ones weigh 1), ...
    other = a != b
    total = (n_cols - 1) - np.bincount(a[other], weights=overlap[other], minlength=n_cols)
    # ... and of those containing v, from A's point of view.
    related = np.zeros(presence.nnz)
    for ea, eb in tqdm(triples(presence), desc="Background", unit="chunk", disable=not progress):
        related += np.bincount(ea, weights=overlap_without[pair_of(ea, eb)], minlength=presence.nnz)
    containing = (df - 1) - related
    p0_all = np.minimum((containing + 1) / (total[row] + 1), MAX_P0)  # before leaving B out

    # Sums over each pair's shared values; the null leaves B out and adds B's density.
    hit_log, hit_p0 = np.zeros(shared.nnz), np.zeros(shared.nnz)
    hit_log_all, hit_p0_all = np.zeros(shared.nnz), np.zeros(shared.nnz)
    for ea, eb in tqdm(triples(presence), desc="Evidence", unit="chunk", disable=not progress):
        pair = pair_of(ea, eb)
        p0 = (containing[ea] - (1 - overlap_without[pair]) + 1) / (total[row[ea]] - (1 - overlap[pair]) + 1)
        p0 = np.clip(np.maximum(p0, density[eb]), 1e-12, MAX_P0)
        hit_log += np.bincount(pair, weights=np.log(p0), minlength=shared.nnz)
        hit_p0 += np.bincount(pair, weights=p0, minlength=shared.nnz)
        hit_log_all += np.bincount(pair, weights=np.log1p(-p0_all[ea]), minlength=shared.nnz)
        hit_p0_all += np.bincount(pair, weights=p0_all[ea], minlength=shared.nnz)

    # Sums over the values A has and B lacks: all of A's values less the shared ones.
    miss_log = np.bincount(row, weights=np.log1p(-p0_all), minlength=n_cols)[a] - hit_log_all
    miss_p0 = np.bincount(row, weights=p0_all, minlength=n_cols)[a] - hit_p0_all

    k, size = shared.data.astype(float), n[a].astype(float)
    c = k / size
    with np.errstate(divide="ignore", invalid="ignore"):
        evidence = k * np.log(c) - hit_log + np.where(k < size, (size - k) * np.log1p(-c), 0.0) - miss_log
    expected = (hit_p0 + miss_p0) / size
    evidence = np.where(c > expected, np.maximum(evidence, 0.0), 0.0)

    # One row per unordered pair of columns from different files.
    file_ids, _ = pd.factorize(pd.Series([f"{p.package}/{p.file}" for p in profiles]))
    upper = (a < b) & (file_ids[a] != file_ids[b])
    back = np.searchsorted(key, b[upper].astype(np.int64) * n_cols + a[upper])  # the (B, A) entries
    joins = pd.DataFrame({
        "id_a": a[upper].astype(np.int32),
        "id_b": b[upper].astype(np.int32),
        "shared": shared.data[upper].astype(np.int32),
        "containment_a_in_b": c[upper],
        "containment_b_in_a": c[back],
        "jaccard": k[upper] / (n[a[upper]] + n[b[upper]] - k[upper]),
        "expected_a_in_b": expected[upper],
        "expected_b_in_a": expected[back],
        "evidence_a_in_b": evidence[upper],
        "evidence_b_in_a": evidence[back],
    })
    joins["evidence"] = joins[["evidence_a_in_b", "evidence_b_in_a"]].max(axis=1)
    beyond_chance = [(joins[f"containment_{d}"] - joins[f"expected_{d}"]) / (1 - joins[f"expected_{d}"])
                     for d in ("a_in_b", "b_in_a")]
    joins["score"] = np.clip(np.maximum(*beyond_chance), 0.0, 1.0)
    joins = joins[joins["evidence"] >= min_evidence]
    return joins.sort_values(["score", "evidence", "id_a", "id_b"], ascending=[False, False, True, True],
                             ignore_index=True)
