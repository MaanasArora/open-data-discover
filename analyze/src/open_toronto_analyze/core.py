"""Find joinable columns across City of Toronto Open Data packages.

Works on the data folder written by ``open-toronto-dl``; only complete packages
are read (incomplete ones are skipped). Ported from ``scratch/main.ipynb``:

1. **Select** packages: by owner (``owner_division``), by name, and/or a limit.
2. **Profile** every CSV column whose average value length exceeds
   ``min_avg_length``: its distinct normalized values (``strip().lower()``).
3. **Presence matrix**: columns x values. Values that occur in fewer than
   ``min_df`` columns or in more than ``max(max_df_floor, max_df_fraction * n_columns)``
   columns are dropped to find candidate pairs cheaply.
4. **Exact intersections** for the candidate pairs, on the unfiltered matrix.
5. **Score** = max(containment of A in B, containment of B in A); keep pairs
   above ``threshold`` whose columns both have at least ``min_distinct`` values.

Library usage::

    from open_toronto_analyze import select_packages, csv_resources, profile_columns, find_joins

    sel = select_packages("../download/data", owners=["transit"], limit=20)
    profiles = profile_columns(csv_resources(sel.packages))
    joins = find_joins(profiles)            # pandas DataFrame, best first
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

import numpy as np
import pandas as pd
from scipy import sparse

from open_toronto_dl import Downloader, LocalPackage, package_ref

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    tqdm = None

__version__ = "0.1.0"

log = logging.getLogger("open_toronto_analyze")

DEFAULT_ROOT = "../download/data"
EXCLUDE_COLUMNS = ("_id",)  # row numbers added by the CKAN datastore

JOIN_COLUMNS = [
    "package_1", "resource_1", "file_1", "column_1",
    "package_2", "resource_2", "file_2", "column_2",
    "intersection", "distinct_1", "distinct_2",
    "containment_1_in_2", "containment_2_in_1", "score",
]

PathLike = Union[str, Path]


def _progress(iterable, progress: bool, **kwargs):
    if progress and tqdm is not None:
        return tqdm(iterable, **kwargs)
    return iterable


# --------------------------------------------------------------------------- #
# 1. Package selection
# --------------------------------------------------------------------------- #


def owner_of(pkg: LocalPackage) -> str:
    """The City division that owns a package (CKAN ``owner_division``)."""
    return pkg.metadata.get("owner_division") or ""


@dataclass
class Selection:
    packages: list  # LocalPackage, sorted by name
    incomplete: list = field(default_factory=list)  # names skipped because incomplete
    missing: list = field(default_factory=list)  # requested names not found locally
    total_complete: int = 0  # complete packages before filtering


def read_package_list(path: PathLike) -> list:
    """Package names/ids/URLs from a text file: one per line, ``#`` starts a comment."""
    names = []
    for line in Path(path).read_text("utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names


def select_packages(
    root: PathLike = DEFAULT_ROOT,
    *,
    owners: Optional[Sequence[str]] = None,
    packages: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
) -> Selection:
    """Pick complete packages from a download folder.

    Filters combine with AND; values within one filter combine with OR.

    Args:
        owners: case-insensitive substrings of ``owner_division``
            (e.g. ``"transit"`` matches "Toronto Transit Commission").
        packages: package names, ids or portal URLs.
        limit: keep at most this many packages (after the other filters, by name).
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"data folder not found: {root}")

    with Downloader(root) as dl:
        statuses = dl.status(with_catalog=False)
        complete = [LocalPackage(s.name, s.path, dl.read_state(s.name)) for s in statuses if s.complete]
    incomplete = [s.name for s in statuses if not s.complete]
    selection = Selection(packages=[], total_complete=len(complete))

    pkgs = complete
    if packages:
        wanted = {package_ref(p) for p in packages if p.strip()}
        pkgs = [p for p in pkgs if p.name in wanted or p.metadata.get("id") in wanted]
        found = {p.name for p in pkgs} | {p.metadata.get("id") for p in pkgs}
        selection.missing = sorted(w for w in wanted - found if w not in incomplete)
        selection.incomplete = sorted(w for w in wanted if w in incomplete)
    else:
        selection.incomplete = incomplete

    if owners:
        needles = [o.strip().lower() for o in owners if o.strip()]
        pkgs = [p for p in pkgs if any(n in owner_of(p).lower() for n in needles)]

    pkgs = sorted(pkgs, key=lambda p: p.name)
    if limit is not None:
        pkgs = pkgs[:limit]
    selection.packages = pkgs
    return selection


# --------------------------------------------------------------------------- #
# 2. Column profiling
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CSVResource:
    package: str
    resource: str  # CKAN resource name
    file: str  # file name inside the package folder
    path: Path
    owner: str


def csv_resources(packages: Iterable[LocalPackage]) -> list:
    """Every downloaded CSV resource of the given packages."""
    out = []
    for pkg in packages:
        for meta, path in pkg.resources():
            if path.suffix.lower() == ".csv" or (meta.get("format") or "").upper() == "CSV":
                out.append(CSVResource(pkg.name, meta.get("name") or path.stem, path.name, path, owner_of(pkg)))
    return out


def read_csv(path: PathLike, *, usecols=None, max_rows: Optional[int] = None) -> pd.DataFrame:
    """Read a CSV as strings (keeps values like ``00123`` intact), tolerating
    bad lines and non-UTF-8 files."""
    kwargs = dict(dtype=str, usecols=usecols, nrows=max_rows, on_bad_lines="skip", low_memory=False)
    try:
        return pd.read_csv(path, encoding="utf-8-sig", **kwargs)
    except UnicodeDecodeError:
        log.info("%s is not UTF-8; reading as latin-1", path)
        return pd.read_csv(path, encoding="latin-1", **kwargs)


def normalize_values(series: pd.Series) -> pd.Series:
    return series.dropna().astype(str).str.strip().str.lower()


@dataclass
class ColumnProfile:
    package: str
    resource: str
    file: str
    column: str
    values: np.ndarray  # distinct normalized values
    samples: list  # a few distinct values, for display

    @property
    def id(self) -> str:
        return f"{self.package}/{self.file}::{self.column}"

    @property
    def n_distinct(self) -> int:
        return len(self.values)


def profile_columns(
    resources: Iterable[CSVResource],
    *,
    min_avg_length: float = 8,
    max_rows: Optional[int] = None,
    exclude: Sequence[str] = EXCLUDE_COLUMNS,
    n_samples: int = 3,
    seed: int = 42,
    progress: bool = False,
) -> list:
    """Distinct normalized values of every CSV column that passes the length filter.

    Each file is read once and dropped, so only the distinct values stay in memory.
    """
    rng = np.random.default_rng(seed)
    profiles = []
    resources = list(resources)
    for res in _progress(resources, progress, desc="Profiling columns", unit="file"):
        try:
            df = read_csv(res.path, max_rows=max_rows)
        except Exception as e:  # malformed file: skip it, keep going
            log.warning("Skipping %s/%s: %s", res.package, res.file, e)
            continue
        for column in df.columns:
            if column in exclude:
                continue
            raw = df[column].dropna()
            if raw.empty or raw.astype(str).str.len().mean() <= min_avg_length:
                continue
            values = normalize_values(raw).unique()
            if len(values) == 0:
                continue
            k = min(n_samples, len(values))
            samples = [str(v) for v in rng.choice(values, size=k, replace=False)] if k else []
            profiles.append(ColumnProfile(res.package, res.resource, res.file, str(column), values, samples))
        del df
    return profiles


# --------------------------------------------------------------------------- #
# 3-5. Matrices and join table
# --------------------------------------------------------------------------- #


def build_presence_matrix(profiles: Sequence[ColumnProfile]):
    """Sparse 0/1 matrix, one row per column profile, one column per distinct value.

    Returns ``(matrix, vocabulary)``.
    """
    lengths = np.array([p.n_distinct for p in profiles], dtype=np.int64)
    if len(profiles) == 0:
        return sparse.csr_matrix((0, 0), dtype=np.int32), np.array([], dtype=object)
    codes, vocabulary = pd.factorize(np.concatenate([p.values for p in profiles]))
    indptr = np.concatenate([[0], np.cumsum(lengths)])
    matrix = sparse.csr_matrix(
        (np.ones(len(codes), dtype=np.int32), codes.astype(np.int32), indptr),
        shape=(len(profiles), len(vocabulary)),
    )
    matrix.sort_indices()
    return matrix, vocabulary


def filter_values(matrix, *, min_df: int = 2, max_df_fraction: float = 0.005, max_df_floor: int = 5):
    """Drop values present in fewer than ``min_df`` or more than
    ``max(max_df_floor, max_df_fraction * n_columns)`` columns."""
    max_df = max(max_df_floor, int(max_df_fraction * matrix.shape[0]))
    df = np.asarray(matrix.sum(axis=0)).ravel()
    keep = (df >= min_df) & (df <= max_df)
    return matrix[:, keep]


def exact_intersections(matrix, rows, cols, *, progress: bool = False) -> np.ndarray:
    """Number of shared values for each (row, col) pair of the presence matrix."""
    out = np.empty(len(rows), dtype=np.int64)
    indptr, indices = matrix.indptr, matrix.indices
    pairs = _progress(range(len(rows)), progress, desc="Verifying pairs", unit="pair", total=len(rows))
    for k in pairs:
        i, j = rows[k], cols[k]
        a = indices[indptr[i]:indptr[i + 1]]
        b = indices[indptr[j]:indptr[j + 1]]
        out[k] = len(np.intersect1d(a, b, assume_unique=True))
    return out


def make_join_table(profiles, rows, cols, intersections, sizes, *, threshold=0.1, min_distinct=8) -> pd.DataFrame:
    """Keep pairs from different files whose best containment exceeds ``threshold``."""
    inter = intersections.astype(np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        c_ij = np.where(sizes[rows] > 0, inter / sizes[rows], 0.0)
        c_ji = np.where(sizes[cols] > 0, inter / sizes[cols], 0.0)
    score = np.maximum(c_ij, c_ji)
    keep = (inter > 0) & (score > threshold) & (sizes[rows] >= min_distinct) & (sizes[cols] >= min_distinct)

    records = []
    for i, j, n, a, b, s in zip(rows[keep], cols[keep], inter[keep], c_ij[keep], c_ji[keep], score[keep], strict=True):
        p, q = profiles[i], profiles[j]
        if (p.package, p.file) == (q.package, q.file):
            continue
        records.append((p.package, p.resource, p.file, p.column,
                        q.package, q.resource, q.file, q.column,
                        int(n), p.n_distinct, q.n_distinct, a, b, s))
    table = pd.DataFrame.from_records(records, columns=JOIN_COLUMNS)
    return table.sort_values("score", ascending=False, kind="stable").reset_index(drop=True)


def find_joins(
    profiles: Sequence[ColumnProfile],
    *,
    threshold: float = 0.1,
    min_distinct: int = 8,
    min_df: int = 2,
    max_df_fraction: float = 0.005,
    max_df_floor: int = 5,
    progress: bool = False,
) -> pd.DataFrame:
    """Run steps 3-5 and return the join table (columns: :data:`JOIN_COLUMNS`), best first."""
    if len(profiles) < 2:
        return pd.DataFrame(columns=JOIN_COLUMNS)
    presence, _ = build_presence_matrix(profiles)
    sizes = np.diff(presence.indptr).astype(np.float64)
    filtered = filter_values(presence, min_df=min_df, max_df_fraction=max_df_fraction, max_df_floor=max_df_floor)
    candidates = sparse.triu(filtered @ filtered.T, k=1).tocoo()
    log.info("%d columns, %d values, %d candidate pairs", presence.shape[0], presence.shape[1], candidates.nnz)
    inter = exact_intersections(presence, candidates.row, candidates.col, progress=progress)
    return make_join_table(profiles, candidates.row, candidates.col, inter, sizes,
                           threshold=threshold, min_distinct=min_distinct)


# --------------------------------------------------------------------------- #
# Querying a join table
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ColumnRef:
    package: str
    resource: str
    file: str
    column: str

    @property
    def id(self) -> str:
        return f"{self.package}/{self.file}::{self.column}"


def table_columns(table: pd.DataFrame) -> list:
    """Every distinct column that appears in a join table."""
    refs = set()
    for side in ("1", "2"):
        cols = [f"package_{side}", f"resource_{side}", f"file_{side}", f"column_{side}"]
        refs.update(ColumnRef(*row) for row in table[cols].itertuples(index=False, name=None))
    return sorted(refs, key=lambda r: r.id)


def find_columns(table: pd.DataFrame, query: str) -> list:
    """Resolve ``[PACKAGE/]RESOURCE::COLUMN`` (or just ``COLUMN``) against a join table.

    ``RESOURCE`` matches the resource name or file name; all matching is
    case-insensitive. An exact ``id`` (as printed by the CLI) always resolves uniquely.
    """
    refs = table_columns(table)
    exact = [r for r in refs if r.id == query]
    if exact:
        return exact
    q = query.lower()
    if "::" in q:
        where, column = q.rsplit("::", 1)
        package, _, resource = where.rpartition("/")
    else:
        package, resource, column = "", "", q

    def ok(r: ColumnRef) -> bool:
        return (
            r.column.lower() == column
            and (not package or r.package.lower() == package)
            and (not resource or resource in (r.resource.lower(), r.file.lower()))
        )

    return [r for r in refs if ok(r)]


def joinable_for(table: pd.DataFrame, ref: ColumnRef) -> pd.DataFrame:
    """Columns joinable with ``ref``: package, resource, file, column, score, intersection."""
    out = []
    for me, other in (("1", "2"), ("2", "1")):
        mask = (
            (table[f"package_{me}"] == ref.package)
            & (table[f"file_{me}"] == ref.file)
            & (table[f"column_{me}"] == ref.column)
        )
        part = table.loc[mask, [f"package_{other}", f"resource_{other}", f"file_{other}", f"column_{other}",
                                "score", "intersection"]]
        part.columns = ["package", "resource", "file", "column", "score", "intersection"]
        out.append(part)
    joined = pd.concat(out, ignore_index=True)
    return joined.sort_values("score", ascending=False, kind="stable").reset_index(drop=True)


def sample_values(root: PathLike, package: str, file: str, column: str, *, n: int = 3, seed: int = 42,
                  max_rows: Optional[int] = None) -> list:
    """A few distinct normalized values of one column, read from disk."""
    path = Path(root) / package / file
    try:
        values = normalize_values(read_csv(path, usecols=[column], max_rows=max_rows)[column]).unique()
    except Exception as e:
        log.info("Could not sample %s::%s: %s", path, column, e)
        return []
    if len(values) == 0:
        return []
    rng = np.random.default_rng(seed)
    return [str(v) for v in rng.choice(values, size=min(n, len(values)), replace=False)]
