"""Download datasets from the City of Toronto Open Data portal (CKAN API).

On-disk layout::

    <root>/
      catalog.json                               snapshot of package_search (crawl / catalog)
      <package-name>/
        .download                                present => package incomplete
        package.json                             written last: local state + CKAN metadata
        <resource-slug>__<id8>.<ext>             complete resource
        <resource-slug>__<id8>.<ext>.download    partial resource (being written / crashed)

The rule: **anything whose name ends in ``.download`` is incomplete.**

* A resource streams into ``<file>.download`` and is renamed to ``<file>`` only
  once it is fully written and its size checked, so a finished-looking file is
  always a finished file.
* A package folder gets a ``.download`` marker before any work starts. The
  marker is removed only after every selected resource succeeded and
  ``package.json`` was written. Refreshes use the same marker, so an interrupted
  refresh never looks complete.

Library usage::

    from open_toronto_dl import Downloader

    with Downloader("data", formats=["CSV"]) as dl:
        dl.get("ttc-subway-delay-data")      # one package
        dl.crawl(limit=20)                   # walk the catalogue
        for pkg in dl.iter_complete():       # only complete packages
            for resource, path in pkg.resources():
                ...
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Union
from urllib.parse import urlparse

import httpx

try:  # progress bars are optional
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    tqdm = None

__version__ = "0.1.0"

__all__ = [
    "BASE_URL",
    "CKANError",
    "Client",
    "Downloader",
    "LocalPackage",
    "PackageResult",
    "PackageStatus",
    "ResourceResult",
    "package_ref",
    "resource_filename",
]

log = logging.getLogger("open_toronto_dl")

BASE_URL = "https://ckan0.cf.opendata.inter.prod-toronto.ca"
SUFFIX = ".download"  # incomplete files end with this
MARKER = ".download"  # package-level marker file name
PACKAGE_FILE = "package.json"
CATALOG_FILE = "catalog.json"
DEFAULT_FORMATS = ("CSV",)
FORMAT_VERSION = 1

Formats = Optional[Union[str, Iterable[str]]]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return slug[:max_len].rstrip("-") or "resource"


def resource_extension(resource: dict) -> str:
    """File extension from the URL path, falling back to the CKAN format."""
    ext = Path(urlparse(resource.get("url") or "").path).suffix.lstrip(".").lower()
    if ext and ext.isalnum() and len(ext) <= 7:
        return ext
    return re.sub(r"[^a-z0-9]", "", (resource.get("format") or "").lower()) or "bin"


def resource_filename(resource: dict) -> str:
    """``<resource-name-slug>__<first 8 chars of id>.<ext>``"""
    name = slugify(resource.get("name") or resource["id"])
    return f"{name}__{resource['id'][:8]}.{resource_extension(resource)}"


def resource_version(resource: dict) -> dict:
    """What we compare to decide whether a local copy is out of date."""
    return {
        "url": resource.get("url"),
        "last_modified": resource.get("last_modified")
        or resource.get("metadata_modified")
        or resource.get("created"),
    }


def normalize_formats(formats: Formats) -> Optional[frozenset]:
    """``None``, ``"*"`` or ``"all"`` mean every format; otherwise an upper-cased set."""
    if formats is None:
        return None
    if isinstance(formats, str):
        formats = formats.split(",")
    fmts = frozenset(f.strip().upper() for f in formats if f and f.strip())
    if not fmts or "*" in fmts or "ALL" in fmts:
        return None
    return fmts


def _formats_list(fmts: Optional[frozenset]) -> list:
    return ["*"] if fmts is None else sorted(fmts)


def matches_format(resource: dict, fmts: Optional[frozenset]) -> bool:
    fmt = (resource.get("format") or "").strip().upper()
    if fmts is None:
        return fmt != "WEB"
    return fmt in fmts


def package_ref(text: str) -> str:
    """Accept a package id/name or a portal URL like
    ``https://open.toronto.ca/dataset/ttc-subway-delay-data/``."""
    text = text.strip()
    if "://" in text:
        parts = [p for p in urlparse(text).path.split("/") if p]
        if "dataset" in parts and parts.index("dataset") + 1 < len(parts):
            return parts[parts.index("dataset") + 1]
        if parts:
            return parts[-1]
    return text


def _describe(e: BaseException) -> str:
    """One-line error description."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code} {e.response.reason_phrase}".strip()
    text = str(e).strip().splitlines()
    return text[0] if text else type(e).__name__


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        log.warning("Could not parse %s: %s", path, e)
        return None


def _write_json_atomic(path: Path, data) -> None:
    tmp = path.with_name(path.name + SUFFIX)
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #


class CKANError(RuntimeError):
    """The CKAN API returned an error (e.g. package not found)."""


class _Retryable(CKANError):
    def __init__(self, message: str, response: Optional[httpx.Response] = None):
        super().__init__(message)
        self.response = response


class Client:
    """Thin CKAN client with polite throttling and retries with backoff.

    Args:
        base_url: CKAN instance (defaults to Toronto's).
        timeout: read timeout in seconds for each request.
        retries: extra attempts on network errors, 429 and 5xx responses.
        backoff: base seconds for exponential backoff (``backoff * 2**attempt``).
        delay: minimum seconds between consecutive requests.
    """

    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        timeout: float = 180.0,
        retries: int = 4,
        backoff: float = 2.0,
        delay: float = 0.5,
        http: Optional[httpx.Client] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.backoff = backoff
        self.delay = delay
        self._owns_http = http is None
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(timeout, connect=30.0),
            follow_redirects=True,
            headers={"User-Agent": f"open-toronto-dl/{__version__}"},
        )
        self._last_request = 0.0

    # context management
    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # internals
    def _throttle(self) -> None:
        wait = self._last_request + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _backoff_sleep(self, attempt: int, reason, response=None) -> None:
        wait = self.backoff * (2**attempt)
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after:
            try:
                wait = max(wait, float(retry_after))
            except ValueError:
                pass
        wait = min(wait, 120.0)
        log.warning("%s; retrying in %.0fs (attempt %d/%d)", reason, wait, attempt + 1, self.retries)
        time.sleep(wait)

    def _get(self, url: str, params: Optional[dict] = None) -> httpx.Response:
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                response = self._http.get(url, params=params)
            except httpx.TransportError as e:
                if attempt == self.retries:
                    raise
                self._backoff_sleep(attempt, f"{type(e).__name__}: {_describe(e)} ({url})")
                continue
            if response.status_code in self.RETRY_STATUS and attempt < self.retries:
                self._backoff_sleep(attempt, f"HTTP {response.status_code} for {url}", response)
                continue
            return response
        raise AssertionError("unreachable")

    # API
    def action(self, name: str, **params) -> dict:
        """Call ``/api/3/action/<name>`` and return its ``result``."""
        response = self._get(f"{self.base_url}/api/3/action/{name}", params)
        try:
            body = response.json()
        except ValueError:
            response.raise_for_status()
            raise CKANError(f"{name}: non-JSON response (HTTP {response.status_code})")
        if response.status_code >= 400 or not body.get("success"):
            error = body.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else error
            raise CKANError(f"{name} failed (HTTP {response.status_code}): {message or error}")
        return body["result"]

    def package_show(self, ref: str) -> dict:
        return self.action("package_show", id=package_ref(ref))

    def iter_packages(self, *, query: Optional[str] = None, rows: int = 500) -> Iterator[dict]:
        """Every package from ``package_search``, sorted by name."""
        start = 0
        while True:
            params = {"rows": rows, "start": start, "sort": "name asc"}
            if query:
                params["q"] = query
            result = self.action("package_search", **params)
            batch = result.get("results") or []
            yield from batch
            start += len(batch)
            if not batch or start >= result.get("count", 0):
                return

    def download(self, url: str, dest: Path, *, progress: bool = False, desc: Optional[str] = None) -> int:
        """Stream ``url`` into ``dest.download``, check its size, then rename to ``dest``.

        Returns the number of bytes written. On failure the partial file is removed;
        after a hard crash it stays behind with its ``.download`` suffix.
        """
        dest = Path(dest)
        partial = dest.with_name(dest.name + SUFFIX)
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                return self._download_once(url, dest, partial, progress, desc)
            except (httpx.TransportError, _Retryable) as e:
                partial.unlink(missing_ok=True)
                if attempt == self.retries:
                    raise
                self._backoff_sleep(attempt, f"{_describe(e)} ({url})", getattr(e, "response", None))
            except BaseException:
                partial.unlink(missing_ok=True)
                raise
        raise AssertionError("unreachable")

    def _download_once(self, url, dest: Path, partial: Path, progress: bool, desc) -> int:
        with self._http.stream("GET", url) as response:
            if response.status_code in self.RETRY_STATUS:
                raise _Retryable(f"HTTP {response.status_code}", response)
            response.raise_for_status()

            expected = None
            encoding = response.headers.get("Content-Encoding", "identity").lower()
            if encoding == "identity" and "Content-Length" in response.headers:
                expected = int(response.headers["Content-Length"])

            bar = None
            if progress and tqdm is not None:
                bar = tqdm(total=expected, unit="B", unit_scale=True, unit_divisor=1024,
                           desc=desc or dest.name, leave=False)
            written = 0
            try:
                with open(partial, "wb") as f:
                    for chunk in response.iter_bytes(1 << 20):
                        f.write(chunk)
                        written += len(chunk)
                        if bar is not None:
                            bar.update(len(chunk))
                    f.flush()
                    os.fsync(f.fileno())
            finally:
                if bar is not None:
                    bar.close()

        if expected is not None and written != expected:
            raise _Retryable(f"incomplete body: got {written} of {expected} bytes")
        os.replace(partial, dest)
        return written


# --------------------------------------------------------------------------- #
# Results / status records
# --------------------------------------------------------------------------- #


@dataclass
class ResourceResult:
    id: str
    name: str
    format: str
    file: Optional[str]
    status: str  # "downloaded" | "up-to-date" | "failed"
    bytes: Optional[int] = None
    error: Optional[str] = None


@dataclass
class PackageResult:
    name: str
    id: Optional[str]
    path: Optional[Path]
    resources: list = field(default_factory=list)
    up_to_date: bool = False  # nothing needed doing; nothing on disk was touched
    error: Optional[str] = None  # package-level failure (e.g. not found)

    @property
    def complete(self) -> bool:
        return self.error is None and all(r.status != "failed" for r in self.resources)

    @property
    def downloaded(self) -> list:
        return [r for r in self.resources if r.status == "downloaded"]

    @property
    def failed(self) -> list:
        return [r for r in self.resources if r.status == "failed"]


@dataclass
class PackageStatus:
    name: str
    path: Path
    state: str  # "complete" | "incomplete"
    files: int = 0
    bytes: int = 0
    downloaded_at: Optional[str] = None
    problems: list = field(default_factory=list)  # why it is incomplete
    stale: list = field(default_factory=list)  # differences vs catalog.json

    @property
    def complete(self) -> bool:
        return self.state == "complete"


@dataclass
class LocalPackage:
    """A complete package on disk."""

    name: str
    path: Path
    state: dict  # contents of package.json

    @property
    def metadata(self) -> dict:
        """The CKAN ``package_show`` result as of download time."""
        return self.state["package"]

    @property
    def files(self) -> dict:
        """``{resource_id: Path}`` for every downloaded resource."""
        return {rid: self.path / e["file"] for rid, e in self.state.get("files", {}).items()}

    def resources(self) -> Iterator[tuple]:
        """``(resource_metadata, Path)`` pairs for downloaded resources."""
        files = self.files
        for resource in self.metadata.get("resources", []):
            if resource["id"] in files:
                yield resource, files[resource["id"]]


# --------------------------------------------------------------------------- #
# Downloader
# --------------------------------------------------------------------------- #


class Downloader:
    """Download packages into a folder tree and track their completeness.

    Args:
        root: data folder.
        formats: resource formats to download (case-insensitive). ``None`` or
            ``"*"`` means every format. Per-call ``formats`` override this; pass
            ``"*"`` there for every format.
        client: a :class:`Client`; one is created if omitted.
        progress: show tqdm progress bars.
    """

    def __init__(
        self,
        root: Union[str, Path] = "data",
        *,
        formats: Formats = DEFAULT_FORMATS,
        client: Optional[Client] = None,
        progress: bool = False,
    ):
        self.root = Path(root)
        self.formats = normalize_formats(formats)
        self.client = client or Client()
        self._owns_client = client is None
        self.progress = progress

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "Downloader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- paths & state ---------------------------------------------------- #

    def package_dir(self, name: str) -> Path:
        return self.root / name

    def _fmts(self, formats: Formats) -> Optional[frozenset]:
        return self.formats if formats is None else normalize_formats(formats)

    def read_state(self, name: str) -> Optional[dict]:
        """Contents of ``<name>/package.json`` (may describe a stale copy if a marker exists)."""
        return _read_json(self.package_dir(name) / PACKAGE_FILE)

    def read_marker(self, name: str) -> Optional[dict]:
        """Marker contents, ``{}`` if the marker exists but is unreadable, ``None`` if absent."""
        path = self.package_dir(name) / MARKER
        if not path.exists():
            return None
        return _read_json(path) or {}

    def _write_marker(self, pdir: Path, marker: dict) -> None:
        # Written in place: even a torn write leaves a marker, i.e. "incomplete".
        (pdir / MARKER).write_text(json.dumps(marker, indent=2, ensure_ascii=False), "utf-8")

    def _package_dirs(self) -> Iterator[Path]:
        if not self.root.is_dir():
            return
        for pdir in sorted(self.root.iterdir()):
            if pdir.is_dir() and not pdir.name.startswith("."):
                if (pdir / PACKAGE_FILE).exists() or (pdir / MARKER).exists():
                    yield pdir

    # ---- downloading ------------------------------------------------------ #

    def get(self, ref: str, *, force: bool = False, formats: Formats = None) -> PackageResult:
        """Fetch a package by id, name or portal URL and download its resources."""
        try:
            meta = self.client.package_show(ref)
        except (CKANError, httpx.HTTPError) as e:
            log.info("%s: %s", ref, e)
            return PackageResult(name=package_ref(ref), id=None, path=None, error=_describe(e))
        return self.download_package(meta, force=force, formats=formats)

    def download_package(self, meta: dict, *, force: bool = False, formats: Formats = None) -> PackageResult:
        """Download the resources of a package given its CKAN metadata dict.

        Resources already on disk with the same URL and ``last_modified`` are
        kept unless ``force`` is set.
        """
        fmts = self._fmts(formats)
        name, pid = meta["name"], meta["id"]
        pdir = self.package_dir(name)
        result = PackageResult(name=name, id=pid, path=pdir)

        all_resources = {r["id"]: r for r in meta.get("resources", [])}
        selected = [r for r in all_resources.values() if matches_format(r, fmts)]
        if not selected:
            log.info("%s: no resources in formats %s", name, _formats_list(fmts))
            return result

        previous = self.read_state(name) or {}
        old_marker = self.read_marker(name)
        known = dict(previous.get("files", {}))
        if old_marker:  # resources finished by an interrupted run
            known.update(old_marker.get("done", {}))

        def fresh(resource: dict) -> Optional[dict]:
            entry = known.get(resource["id"])
            if force or not entry or {k: entry.get(k) for k in ("url", "last_modified")} != resource_version(resource):
                return None
            path = pdir / entry["file"]
            if not path.is_file() or (entry.get("bytes") is not None and path.stat().st_size != entry["bytes"]):
                return None
            return entry

        # Nothing to do: complete, same metadata, every selected resource fresh.
        if (
            old_marker is None
            and previous
            and previous.get("package", {}).get("metadata_modified") == meta.get("metadata_modified")
            and all(fresh(r) for r in selected)
        ):
            for r in selected:
                entry = known[r["id"]]
                result.resources.append(ResourceResult(
                    r["id"], r.get("name", ""), r.get("format", ""), entry["file"], "up-to-date", entry.get("bytes")))
            result.up_to_date = True
            return result

        pdir.mkdir(parents=True, exist_ok=True)
        prev_formats = previous.get("formats") or []
        formats_list = ["*"] if fmts is None or "*" in prev_formats else sorted(set(prev_formats) | set(fmts))
        marker = {
            "package_id": pid,
            "package_name": name,
            "formats": formats_list,
            "started_at": _now(),
            "pending": [r["id"] for r in selected],
            # carry over work from an interrupted run so a second crash doesn't lose it
            "done": {rid: e for rid, e in (old_marker or {}).get("done", {}).items() if rid in all_resources},
            "failed": {},
        }
        self._write_marker(pdir, marker)

        # Keep entries for resources that still exist upstream and are on disk
        # (e.g. files of other formats from earlier runs).
        files = {rid: e for rid, e in known.items()
                 if rid in all_resources and (pdir / e["file"]).is_file()}

        for r in selected:
            rid, rname, rfmt = r["id"], r.get("name", ""), r.get("format", "")
            entry = fresh(r)
            if entry:
                marker["done"][rid] = entry
                result.resources.append(ResourceResult(rid, rname, rfmt, entry["file"], "up-to-date", entry.get("bytes")))
            else:
                fname = resource_filename(r)
                try:
                    if not r.get("url"):
                        raise CKANError("resource has no URL")
                    log.info("%s: downloading %s", name, fname)
                    size = self.client.download(r["url"], pdir / fname, progress=self.progress,
                                                desc=f"{name}/{fname}")
                except (httpx.HTTPError, CKANError, OSError) as e:
                    log.info("%s: %s failed: %s", name, fname, e)
                    marker["failed"][rid] = f"{fname}: {_describe(e)}"
                    result.resources.append(ResourceResult(rid, rname, rfmt, fname, "failed", error=_describe(e)))
                else:
                    new_entry = {"file": fname, **resource_version(r), "bytes": size, "downloaded_at": _now()}
                    old_file = files.get(rid, {}).get("file")
                    if old_file and old_file != fname:  # resource renamed upstream
                        (pdir / old_file).unlink(missing_ok=True)
                    files[rid] = new_entry
                    marker["done"][rid] = new_entry
                    result.resources.append(ResourceResult(rid, rname, rfmt, fname, "downloaded", size))
            marker["pending"].remove(rid)
            self._write_marker(pdir, marker)

        if marker["failed"]:
            marker["finished_at"] = _now()
            self._write_marker(pdir, marker)
            return result

        state = {
            "format_version": FORMAT_VERSION,
            "downloaded_at": _now(),
            "source": self.client.base_url,
            "formats": formats_list,
            "files": files,
            "package": meta,
        }
        _write_json_atomic(pdir / PACKAGE_FILE, state)
        (pdir / MARKER).unlink()
        return result

    def refresh_catalog(self, *, query: Optional[str] = None) -> list:
        """Fetch every package via ``package_search`` and write ``catalog.json``.

        Returns the full package dicts (the file keeps a trimmed copy).
        """
        packages = list(self.client.iter_packages(query=query))
        keep = ("id", "name", "format", "url", "last_modified", "metadata_modified", "created", "size")
        catalog = {
            "fetched_at": _now(),
            "source": self.client.base_url,
            "query": query,
            "count": len(packages),
            "packages": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "title": p.get("title"),
                    "metadata_modified": p.get("metadata_modified"),
                    "resources": [{k: r.get(k) for k in keep} for r in p.get("resources", [])],
                }
                for p in packages
            ],
        }
        self.root.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(self.root / CATALOG_FILE, catalog)
        return packages

    def crawl(
        self,
        *,
        query: Optional[str] = None,
        skip: int = 0,
        limit: Optional[int] = None,
        force: bool = False,
        formats: Formats = None,
        on_result: Optional[Callable[[PackageResult], None]] = None,
    ) -> list:
        """Refresh the catalogue, then download every package with a matching resource.

        ``skip`` / ``limit`` slice the list of matching packages (sorted by name).
        Packages that are already complete and up to date are no-ops.
        """
        fmts = self._fmts(formats)
        packages = self.refresh_catalog(query=query)
        candidates = [p for p in packages if any(matches_format(r, fmts) for r in p.get("resources", []))]
        end = None if limit is None else skip + limit
        candidates = candidates[skip:end]

        results = []
        iterable = candidates
        if self.progress and tqdm is not None:
            iterable = tqdm(candidates, desc="packages", unit="pkg")
        for meta in iterable:
            try:
                res = self.download_package(meta, force=force, formats=formats)
            except Exception as e:  # keep crawling
                log.exception("%s: unexpected error", meta.get("name"))
                res = PackageResult(meta.get("name", "?"), meta.get("id"), None, error=_describe(e))
            results.append(res)
            if on_result:
                on_result(res)
        return results

    def resume(self, *, force: bool = False, on_result=None) -> list:
        """Retry every incomplete package, with the formats it was started with."""
        results = []
        for st in self.status(with_catalog=False):
            if st.complete:
                continue
            marker = self.read_marker(st.name) or {}
            state = self.read_state(st.name) or {}
            ref = marker.get("package_id") or state.get("package", {}).get("id") or st.name
            formats = marker.get("formats") or state.get("formats")
            res = self.get(ref, force=force, formats=formats)
            results.append(res)
            if on_result:
                on_result(res)
        return results

    # ---- inspection ------------------------------------------------------- #

    def status(self, *, with_catalog: bool = True) -> list:
        """Check every package folder. A package is complete only if it has no
        marker, has ``package.json``, every listed file exists with the recorded
        size, and there are no partial files."""
        catalog = {}
        if with_catalog:
            cat = _read_json(self.root / CATALOG_FILE) or {}
            catalog = {p["id"]: p for p in cat.get("packages", [])}

        out = []
        for pdir in self._package_dirs():
            st = PackageStatus(name=pdir.name, path=pdir, state="complete")
            marker = self.read_marker(pdir.name)
            state = self.read_state(pdir.name)

            if marker is not None:
                pending = marker.get("pending") or []
                failed = marker.get("failed") or {}
                if failed:
                    st.problems += [f"failed: {msg}" for msg in failed.values()]
                if pending:
                    st.problems.append(f"interrupted with {len(pending)} resource(s) pending")
                if not failed and not pending:
                    st.problems.append("interrupted before finishing")
            if state is None:
                if marker is None:
                    st.problems.append(f"no {PACKAGE_FILE}")
            else:
                st.downloaded_at = state.get("downloaded_at")
                for entry in state.get("files", {}).values():
                    path = pdir / entry["file"]
                    if not path.is_file():
                        st.problems.append(f"missing file: {entry['file']}")
                        continue
                    size = path.stat().st_size
                    if entry.get("bytes") is not None and size != entry["bytes"]:
                        st.problems.append(f"size mismatch: {entry['file']} ({size} != {entry['bytes']})")
                    st.files += 1
                    st.bytes += size
            for p in pdir.iterdir():
                if p.name != MARKER and p.name.endswith(SUFFIX):
                    st.problems.append(f"partial file: {p.name}")

            if st.problems:
                st.state = "incomplete"

            pid = (state or {}).get("package", {}).get("id") or (marker or {}).get("package_id")
            if state and pid in catalog:
                fmts = normalize_formats(state.get("formats"))
                files = state.get("files", {})
                upstream = {r["id"]: r for r in catalog[pid].get("resources", [])}
                for rid, r in upstream.items():
                    if not matches_format(r, fmts):
                        continue
                    if rid not in files:
                        st.stale.append(f"new upstream: {r.get('name') or rid}")
                    elif {k: files[rid].get(k) for k in ("url", "last_modified")} != resource_version(r):
                        st.stale.append(f"updated upstream: {files[rid]['file']}")
                for rid, entry in files.items():
                    if rid not in upstream:
                        st.stale.append(f"removed upstream: {entry['file']}")
            out.append(st)
        return out

    def iter_complete(self) -> Iterator[LocalPackage]:
        """Yield only complete packages; incomplete ones are skipped."""
        for st in self.status(with_catalog=False):
            if st.complete:
                yield LocalPackage(st.name, st.path, self.read_state(st.name))

    def not_downloaded(self, formats: Formats = None) -> list:
        """Catalogue packages with matching resources that have no complete local copy."""
        fmts = self._fmts(formats)
        cat = _read_json(self.root / CATALOG_FILE) or {}
        complete = {st.name for st in self.status(with_catalog=False) if st.complete}
        return [
            p["name"]
            for p in cat.get("packages", [])
            if p["name"] not in complete and any(matches_format(r, fmts) for r in p.get("resources", []))
        ]

    def clean(self, *, incomplete: bool = False, orphans: bool = False, dry_run: bool = False) -> list:
        """Remove partial ``*.download`` files.

        Args:
            incomplete: also delete package folders that never completed
                (marker present and no ``package.json``). Interrupted *refreshes*
                are kept; use :meth:`resume` for those.
            orphans: also delete files in complete packages that ``package.json``
                does not list.
            dry_run: only report what would be removed.
        """
        removed = []

        def rm(path: Path) -> None:
            removed.append(path)
            if dry_run:
                return
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)

        stray_catalog = self.root / (CATALOG_FILE + SUFFIX)
        if stray_catalog.exists():
            rm(stray_catalog)

        for pdir in self._package_dirs():
            has_marker = (pdir / MARKER).exists()
            state = self.read_state(pdir.name)
            if incomplete and has_marker and state is None:
                rm(pdir)
                continue
            for p in sorted(pdir.iterdir()):
                if p.is_file() and p.name != MARKER and p.name.endswith(SUFFIX):
                    rm(p)
            if orphans and state is not None and not has_marker:
                listed = {e["file"] for e in state.get("files", {}).values()} | {PACKAGE_FILE}
                for p in sorted(pdir.iterdir()):
                    if p.is_file() and p.name not in listed and p.name != MARKER and not p.name.endswith(SUFFIX):
                        rm(p)
        return removed
