"""Find joinable columns across downloaded Open Data packages.

    uv run analyze.py owners
    uv run analyze.py list --owner transit --limit 10
    uv run analyze.py run --owner parks --owner transportation    # writes results/
    uv run analyze.py show "Neighbourhoods - 4326::AREA_NAME"
    uv run analyze.py export -o joins.csv
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from columns import csv_files, profile_columns
from joins import find_joins
from packages import read_package_list, select_packages
from results import ResultsError, columns_frame, package_versions, read_results, write_results
from views import find_columns, join_table, joins_of, label

DEFAULT_ROOT = "../download/data"

out = Console()
err = Console(stderr=True)


def selected(args):
    """Apply the selection filters and report anything skipped."""
    names = list(args.package or [])
    for path in args.packages_file or []:
        names += read_package_list(path)
    selection = select_packages(args.data, owners=args.owner, names=names, limit=args.limit)
    if selection.missing:
        err.print(f"[yellow]Not in {args.data}:[/yellow] {', '.join(selection.missing)}")
    if selection.incomplete:
        shown = selection.incomplete[:10] + (["..."] if len(selection.incomplete) > 10 else [])
        err.print(f"[yellow]Skipping {len(selection.incomplete)} incomplete package(s):[/yellow] {', '.join(shown)}")
    return selection


def summary(selection) -> str:
    return f"{len(selection.packages)} of {selection.available} complete package(s) selected"


def new_table(*columns) -> Table:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    for column in columns:
        table.add_column(column)
    return table


def print_joins(joins) -> None:
    table = new_table("Match", "Column", "Joins with", "Shared")
    for row in joins.itertuples():
        table.add_row(
            f"[green]{row.score:.0%}[/green]",
            f"[bold]{row.column_a}[/bold]\n[dim]{row.package_a} · {row.resource_a}[/dim]",
            f"[bold]{row.column_b}[/bold]\n[dim]{row.package_b} · {row.resource_b}[/dim]",
            str(row.shared),
        )
    if table.row_count:
        out.print(table)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_owners(args) -> int:
    selection = selected(args)
    table = new_table("Packages", "Owner")
    counts = Counter(p.owner or "(none)" for p in selection.packages)
    for owner, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        table.add_row(str(count), owner)
    out.print(table, f"[dim]{summary(selection)}[/dim]")
    return 0


def cmd_list(args) -> int:
    selection = selected(args)
    table = new_table("Package", "Owner", "CSVs")
    for package in selection.packages:
        table.add_row(package.name, f"[dim]{package.owner}[/dim]", str(len(csv_files([package]))))
    out.print(table, f"[dim]{summary(selection)}[/dim]")
    return 0


def cmd_run(args) -> int:
    selection = selected(args)
    files = csv_files(selection.packages)
    out.print(f"{summary(selection)}, {len(files)} CSV file(s)")
    if not files:
        return 1

    progress = not args.no_progress and sys.stderr.isatty()
    parameters = {"min_avg_length": args.min_avg_length, "max_rows": args.max_rows, "min_evidence": args.min_evidence}
    profiles = profile_columns(files, min_avg_length=args.min_avg_length, max_rows=args.max_rows, progress=progress)
    out.print(f"{len(profiles)} column(s) passed the length filter")
    joins = find_joins(profiles, min_evidence=args.min_evidence, progress=progress)

    write_results(args.results, columns=columns_frame(profiles), joins=joins, manifest={
        "data_folder": str(args.data),
        "selection": {
            "owners": args.owner,
            "packages": args.package,
            "packages_files": [str(p) for p in args.packages_file or []],
            "limit": args.limit,
        },
        "parameters": parameters,
        "packages": package_versions(selection.packages, files),
    })
    out.print(f"{len(joins)} join(s) written to [bold]{args.results}/[/bold]")
    if args.top:
        print_joins(join_table(read_results(args.results)).head(args.top))
    return 0


def cmd_export(args) -> int:
    joins = join_table(read_results(args.results))
    joins.to_csv(args.output, index=False)
    out.print(f"{len(joins)} join(s) written to [bold]{args.output}[/bold]")
    return 0


def cmd_show(args) -> int:
    results = read_results(args.results)
    matches = find_columns(results.columns, args.column)
    if len(matches) != 1:
        err.print(f"{args.column!r} is ambiguous; use one of these:" if len(matches)
                  else f"No column matches {args.column!r}.")
        for _, row in matches.iterrows():
            err.print(f"  {label(row)}")
        return 1 if matches.empty else 2

    column = matches.iloc[0]
    kind = f", {column['kind']}s" if column["kind"] else ""
    out.print(Panel("\n".join(f"· {v}" for v in column["samples"]) or "[dim]no values[/dim]",
                    title=f"[bold]{column['column']}[/bold]  [dim]{column['package']} · {column['resource']}[/dim]",
                    subtitle=f"[dim]{column['n_distinct']} distinct{kind}[/dim]", border_style="cyan", expand=False))
    out.print(f"[dim]{label(column)}[/dim]")
    out.print("[dim]Match: match strength. Found: share of this column's values found in the other.[/dim]")

    joins = joins_of(results, int(column["id"]))
    table = Table(box=box.HORIZONTALS, show_lines=True, padding=(0, 2), pad_edge=False,
                  header_style="bold", border_style="grey30")
    table.add_column("Match", justify="right", style="green", width=6)
    table.add_column("Found", justify="right", width=12)
    table.add_column("Column", style="bold")
    table.add_column("Dataset", style="dim", max_width=34, overflow="ellipsis")
    table.add_column("Shared", justify="right")
    table.add_column("Sample values", max_width=44, overflow="fold")
    for row in joins.head(args.top or None).itertuples(index=False):
        table.add_row(f"{row.score:.0%}", f"{row.contained:.0%} [dim]({row.expected:.0%})[/dim]", row.column,
                      f"{row.package} · {row.resource}", str(row.shared),
                      " [dim]·[/dim] ".join(list(row.samples)[:3]) or "[dim]—[/dim]")
    out.print(table if table.row_count else "[dim]No joinable columns found.[/dim]")
    return 0


# --------------------------------------------------------------------------- #
# Arguments
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    data = argparse.ArgumentParser(add_help=False)
    data.add_argument("-d", "--data", type=Path, default=Path(os.environ.get("OPEN_TORONTO_DL_ROOT", DEFAULT_ROOT)),
                      help=f"download folder (default: $OPEN_TORONTO_DL_ROOT or {DEFAULT_ROOT})")

    filters = argparse.ArgumentParser(add_help=False)
    group = filters.add_argument_group("package selection (filters combine with AND; repeats with OR)")
    group.add_argument("-o", "--owner", action="append", metavar="TEXT", help="owner division contains TEXT")
    group.add_argument("-p", "--package", action="append", metavar="NAME", help="package name, id or portal URL")
    group.add_argument("-P", "--packages-file", action="append", type=Path, metavar="FILE",
                       help="file with one package per line")
    group.add_argument("-n", "--limit", type=int, metavar="N", help="first N packages by name")

    results = argparse.ArgumentParser(add_help=False)
    results.add_argument("-r", "--results", type=Path, default=Path("results"), help="results folder (default: results)")

    parser = argparse.ArgumentParser(prog="analyze.py", description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true", help="log details")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = commands.add_parser("owners", parents=[data, filters], help="count packages per owner division")
    p.set_defaults(func=cmd_owners)

    p = commands.add_parser("list", parents=[data, filters], help="list the selected packages")
    p.set_defaults(func=cmd_list)

    p = commands.add_parser("run", parents=[data, filters, results], help="profile columns and score joins")
    p.add_argument("--top", type=int, default=10, help="print the N best pairs (default: 10)")
    p.add_argument("--no-progress", action="store_true", help="hide progress bars")
    group = p.add_argument_group("algorithm")
    group.add_argument("--min-avg-length", type=float, default=6,
                       help="skip columns with average value length <= this (default: 6)")
    group.add_argument("--min-evidence", type=float, default=10,
                       help="keep pairs with at least this much evidence, in nats (default: 10)")
    group.add_argument("--max-rows", type=int, help="read at most N rows per CSV")
    p.set_defaults(func=cmd_run)

    p = commands.add_parser("show", parents=[results], help="show the columns that join with one column")
    p.add_argument("column", metavar="COLUMN", help="[PACKAGE/]RESOURCE::COLUMN, COLUMN, or a printed label")
    p.add_argument("--top", type=int, default=25, help="show the N best matches, 0 for all (default: 25)")
    p.set_defaults(func=cmd_show)

    p = commands.add_parser("export", parents=[results], help="write every join as a readable CSV")
    p.add_argument("--output", type=Path, default=Path("joins.csv"), help="default: joins.csv")
    p.set_defaults(func=cmd_export)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        return args.func(args)
    except (FileNotFoundError, ResultsError) as e:
        err.print(f"[red]error:[/red] {e}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
