"""open-toronto-dl: command-line interface for open_toronto_dl.

Examples::

    open-toronto-dl get ttc-subway-delay-data
    open-toronto-dl get https://open.toronto.ca/dataset/bicycle-parking-racks/ --formats CSV,GeoJSON
    open-toronto-dl crawl --limit 25
    open-toronto-dl status
    open-toronto-dl resume
    open-toronto-dl clean --dry-run
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
from pathlib import Path

from open_toronto_dl import core as otd

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    tqdm = None


def _echo(msg: str = "") -> None:
    # tqdm.write keeps progress bars intact
    (tqdm.write if tqdm is not None else print)(msg)


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def _print_result(res: otd.PackageResult) -> None:
    if res.error:
        _echo(f"FAIL  {res.name}: {res.error}")
    elif not res.resources:
        _echo(f"skip  {res.name}: no resources in the requested formats")
    elif res.up_to_date:
        _echo(f"ok    {res.name}: up to date ({len(res.resources)} file(s))")
    else:
        up = len(res.resources) - len(res.downloaded) - len(res.failed)
        parts = [f"{len(res.downloaded)} downloaded"]
        if up:
            parts.append(f"{up} up to date")
        if res.failed:
            parts.append(f"{len(res.failed)} failed")
            _echo(f"FAIL  {res.name}: {', '.join(parts)}")
            for r in res.failed:
                _echo(f"        {r.file}: {r.error}")
        else:
            _echo(f"ok    {res.name}: {', '.join(parts)}")


def _summarize(results: list) -> int:
    failed = [r for r in results if not r.complete]
    if len(results) > 1:
        _echo(f"\n{len(results) - len(failed)} package(s) complete, {len(failed)} incomplete")
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_get(dl: otd.Downloader, args) -> int:
    results = []
    for ref in args.packages:
        res = dl.get(ref, force=args.force, formats=args.formats)
        _print_result(res)
        results.append(res)
    return _summarize(results)


def cmd_crawl(dl: otd.Downloader, args) -> int:
    results = dl.crawl(query=args.query, skip=args.skip, limit=args.limit, force=args.force,
                       formats=args.formats, on_result=_print_result)
    if not results:
        _echo("No matching packages.")
    return _summarize(results)


def cmd_catalog(dl: otd.Downloader, args) -> int:
    packages = dl.refresh_catalog(query=args.query)
    print(f"Wrote {dl.root / otd.CATALOG_FILE} ({len(packages)} packages)")
    return 0


def cmd_resume(dl: otd.Downloader, args) -> int:
    results = dl.resume(force=args.force, on_result=_print_result)
    if not results:
        print("Nothing to resume: no incomplete packages.")
    return _summarize(results)


def cmd_status(dl: otd.Downloader, args) -> int:
    statuses = dl.status()
    incomplete = [s for s in statuses if not s.complete]
    stale = [s for s in statuses if s.stale]
    missing = dl.not_downloaded(args.formats) if (dl.root / otd.CATALOG_FILE).exists() else None

    if args.json:
        data = {
            "packages": [
                {**dataclasses.asdict(s), "path": str(s.path)} for s in statuses
            ],
            "not_downloaded": missing,
        }
        print(json.dumps(data, indent=2))
        return 1 if incomplete else 0

    total = sum(s.bytes for s in statuses if s.complete)
    print(f"{dl.root}: {len(statuses) - len(incomplete)} complete ({_size(total)}), "
          f"{len(incomplete)} incomplete, {len(stale)} stale")
    if missing is not None:
        print(f"{len(missing)} package(s) in catalog.json not downloaded yet")

    if incomplete:
        print("\nIncomplete:")
        for s in incomplete:
            print(f"  {s.name}")
            for p in s.problems:
                print(f"      {p}")
    if stale:
        print("\nStale (vs catalog.json):")
        for s in stale:
            print(f"  {s.name}")
            for p in s.stale[: 5 if not args.all else None]:
                print(f"      {p}")
            if len(s.stale) > 5 and not args.all:
                print(f"      ... {len(s.stale) - 5} more")
    if args.all:
        print("\nComplete:")
        for s in statuses:
            if s.complete:
                print(f"  {s.name}  {s.files} file(s), {_size(s.bytes)}, downloaded {s.downloaded_at}")
    if incomplete:
        print("\nRun `open-toronto-dl resume` to retry, or `open-toronto-dl clean --incomplete` to discard.")
    return 1 if incomplete else 0


def cmd_clean(dl: otd.Downloader, args) -> int:
    removed = dl.clean(incomplete=args.incomplete, orphans=args.orphans, dry_run=args.dry_run)
    verb = "Would remove" if args.dry_run else "Removed"
    for p in removed:
        print(f"{verb} {p}")
    print(f"{verb} {len(removed)} item(s).")
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="open-toronto-dl",
        description="Download City of Toronto Open Data packages. "
        "Files ending in .download are incomplete.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {otd.__version__}")
    parser.add_argument("-d", "--root", type=Path,
                        default=Path(os.environ.get("OPEN_TORONTO_DL_ROOT", "data")),
                        help="data folder (default: ./data or $OPEN_TORONTO_DL_ROOT)")
    parser.add_argument("--base-url", default=otd.BASE_URL, help="CKAN base URL")
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between requests (default: 0.5)")
    parser.add_argument("--retries", type=int, default=4, help="retries per request (default: 4)")
    parser.add_argument("--timeout", type=float, default=180, help="request timeout in seconds (default: 180)")
    parser.add_argument("--no-progress", action="store_true", help="hide progress bars")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="-v info, -vv debug")

    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def formats_arg(p, default="CSV"):
        p.add_argument("-f", "--formats", default=default,
                       help=f"comma-separated resource formats, or '*' for all (default: {default})")

    p = sub.add_parser("get", help="download one or more packages by id, name or portal URL")
    p.add_argument("packages", nargs="+", metavar="PACKAGE")
    formats_arg(p)
    p.add_argument("--force", action="store_true", help="re-download even if up to date")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("crawl", help="refresh catalog.json and download every matching package")
    formats_arg(p)
    p.add_argument("-q", "--query", help="CKAN search query to restrict the crawl")
    p.add_argument("--skip", type=int, default=0, help="skip the first N matching packages")
    p.add_argument("--limit", type=int, help="process at most N matching packages")
    p.add_argument("--force", action="store_true", help="re-download even if up to date")
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("catalog", help="refresh catalog.json only (no downloads)")
    p.add_argument("-q", "--query", help="CKAN search query")
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("status", help="report complete / incomplete / stale packages (exit 1 if any incomplete)")
    formats_arg(p)
    p.add_argument("-a", "--all", action="store_true", help="also list complete packages")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("resume", help="retry every incomplete package")
    p.add_argument("--force", action="store_true", help="re-download every resource of those packages")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("clean", help="remove partial *.download files")
    p.add_argument("--incomplete", action="store_true",
                   help="also delete package folders that never completed")
    p.add_argument("--orphans", action="store_true",
                   help="also delete files in complete packages not listed in package.json")
    p.add_argument("-n", "--dry-run", action="store_true", help="only show what would be removed")
    p.set_defaults(func=cmd_clean)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    level = {0: logging.WARNING, 1: logging.INFO}.get(args.verbose, logging.DEBUG)
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")
    if args.verbose < 2:
        logging.getLogger("httpx").setLevel(logging.WARNING)

    client = otd.Client(args.base_url, timeout=args.timeout, retries=args.retries, delay=args.delay)
    progress = not args.no_progress and sys.stderr.isatty()
    with client, otd.Downloader(args.root, client=client, progress=progress) as dl:
        try:
            return args.func(dl, args)
        except KeyboardInterrupt:
            print("\nInterrupted. Partial downloads are marked with .download; "
                  "run `open-toronto-dl resume` to continue.", file=sys.stderr)
            return 130
        except (otd.CKANError, otd.httpx.HTTPError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2


if __name__ == "__main__":
    sys.exit(main())
