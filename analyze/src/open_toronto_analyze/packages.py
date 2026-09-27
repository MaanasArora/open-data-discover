"""Step 1: find the complete packages in a download folder and select some of them.

Reads the folder layout written by open-toronto-dl::

    <root>/<package-name>/package.json      {"files": {resource_id: {"file", "bytes", ...}}, "package": {...}}
    <root>/<package-name>/<resource files>

A package is complete when it has ``package.json``, nothing in its folder ends in
``.download`` (the marker or a partial file), and every listed file exists with
its recorded size. Anything else is skipped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

INCOMPLETE_SUFFIX = ".download"


@dataclass
class Package:
    name: str
    path: Path
    metadata: dict  # the CKAN package_show result
    files: dict  # resource id -> Path of the downloaded file

    @property
    def id(self) -> str:
        return self.metadata.get("id", "")

    @property
    def owner(self) -> str:
        """The City division that publishes the package."""
        return self.metadata.get("owner_division") or ""

    def resources(self):
        """Yield ``(resource metadata, file path)`` for each downloaded resource."""
        for resource in self.metadata.get("resources", []):
            if resource["id"] in self.files:
                yield resource, self.files[resource["id"]]


def load_package(folder: Path) -> Package | None:
    """The package in ``folder``, or ``None`` if it is incomplete."""
    state_file = folder / "package.json"
    if not state_file.is_file() or any(p.name.endswith(INCOMPLETE_SUFFIX) for p in folder.iterdir()):
        return None
    try:
        state = json.loads(state_file.read_text("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    files = {}
    for resource_id, entry in state.get("files", {}).items():
        path = folder / entry["file"]
        if not path.is_file() or entry.get("bytes") not in (None, path.stat().st_size):
            return None
        files[resource_id] = path
    return Package(folder.name, folder, state["package"], files)


@dataclass
class Selection:
    packages: list  # selected Packages, sorted by name
    incomplete: list  # names of relevant package folders skipped as incomplete
    missing: list  # requested packages that are not in the folder
    available: int  # complete packages before filtering


def select_packages(root, *, owners=None, names=None, limit=None) -> Selection:
    """Select complete packages. Filters combine with AND; values within one filter with OR.

    Args:
        owners: case-insensitive substrings of the owner division ("transit").
        names: package names, ids or portal URLs.
        limit: keep the first N packages by name, after the other filters.
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"data folder not found: {root}")

    complete, incomplete = [], []
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        package = load_package(folder)
        if package:
            complete.append(package)
        elif (folder / "package.json").exists() or (folder / INCOMPLETE_SUFFIX).exists():
            incomplete.append(folder.name)

    selected, missing = complete, []
    if names:
        wanted = {package_name(n) for n in names}
        selected = [p for p in selected if p.name in wanted or p.id in wanted]
        found = {p.name for p in selected} | {p.id for p in selected}
        missing = sorted(wanted - found - set(incomplete))
        incomplete = sorted(wanted & set(incomplete))
    if owners:
        needles = [o.lower() for o in owners]
        selected = [p for p in selected if any(n in p.owner.lower() for n in needles)]

    return Selection(selected[:limit], incomplete, missing, len(complete))


def package_name(ref: str) -> str:
    """Accept a package name/id or a portal URL like https://open.toronto.ca/dataset/<name>/."""
    ref = ref.strip()
    if "://" not in ref:
        return ref
    parts = [p for p in urlparse(ref).path.split("/") if p]
    if "dataset" in parts[:-1]:
        return parts[parts.index("dataset") + 1]
    return parts[-1] if parts else ref


def read_package_list(path) -> list:
    """Package names from a text file: one per line, ``#`` starts a comment."""
    lines = (line.split("#", 1)[0].strip() for line in Path(path).read_text("utf-8").splitlines())
    return [line for line in lines if line]
