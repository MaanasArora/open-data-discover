"""Store an analysis run in a folder and read it back.

    <results>/
      columns.parquet    one row per profiled column; id is the row number
      joins.parquet      one row per scored pair of columns (see joins.py), best first
      manifest.json      written last: selection, parameters, package versions, counts

The folder is complete only once ``manifest.json`` exists; it is removed first
when a run starts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

FORMAT_VERSION = 2
MANIFEST = "manifest.json"
COLUMNS = "columns.parquet"
JOINS = "joins.parquet"


class ResultsError(Exception):
    """A results folder is missing, incomplete or from an older version."""


@dataclass
class Results:
    manifest: dict
    columns: pd.DataFrame
    joins: pd.DataFrame


def columns_frame(profiles) -> pd.DataFrame:
    return pd.DataFrame({
        "id": np.arange(len(profiles), dtype=np.int32),
        "key": [p.key for p in profiles],
        "package": [p.package for p in profiles],
        "resource_id": [p.resource_id for p in profiles],
        "resource": [p.resource for p in profiles],
        "file": [p.file for p in profiles],
        "column": [p.column for p in profiles],
        "n_values": np.array([p.n_values for p in profiles], dtype=np.int32),
        "n_distinct": np.array([len(p.values) for p in profiles], dtype=np.int32),
        "avg_length": np.array([p.avg_length for p in profiles], dtype=np.float64),
        "samples": [[str(s) for s in p.samples] for p in profiles],
    })


def package_versions(packages, files) -> list:
    """What was analyzed: each package and the ``last_modified`` of its CSV resources."""
    used = {f.resource_id for f in files}
    return [
        {
            "name": p.name,
            "id": p.id,
            "title": p.metadata.get("title"),
            "owner": p.owner,
            "downloaded_at": p.downloaded_at,
            "resources": {r["id"]: r.get("last_modified") for r, _ in p.resources() if r["id"] in used},
        }
        for p in packages
    ]


def write_results(folder, *, columns: pd.DataFrame, joins: pd.DataFrame, manifest: dict) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MANIFEST).unlink(missing_ok=True)  # incomplete until the manifest is back
    columns.to_parquet(folder / COLUMNS, index=False)
    joins.to_parquet(folder / JOINS, index=False)
    manifest = {
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **manifest,
        "counts": {"columns": len(columns), "joins": len(joins)},
    }
    (folder / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), "utf-8")


def read_results(folder) -> Results:
    folder = Path(folder)
    if not (folder / MANIFEST).is_file():
        raise ResultsError(f"no complete results in {folder}; run `analyze.py run` first")
    manifest = json.loads((folder / MANIFEST).read_text("utf-8"))
    if manifest.get("format_version") != FORMAT_VERSION:
        raise ResultsError(f"results in {folder} are from an older version; run `analyze.py run` again")
    return Results(manifest, pd.read_parquet(folder / COLUMNS), pd.read_parquet(folder / JOINS))
