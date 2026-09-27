"""Find joinable columns across downloaded Open Data packages.

    uv run analyze.py owners
    uv run analyze.py list --owner transit --limit 10
    uv run analyze.py joins --owner parks --owner transportation
    uv run analyze.py show "Neighbourhoods - 4326::AREA_NAME"
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

from columns import ColumnRef, csv_files, profile_columns
from joins import find_joins
from lookup import find_columns, joins_for, load_joins, sample_values
from packages import read_package_list, select_packages

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


def cmd_joins(args) -> int:
    selection = selected(args)
    files = csv_files(selection.packages)
    out.print(f"{summary(selection)}, {len(files)} CSV file(s)")
    if not files:
        return 1

    progress = not args.no_progress and sys.stderr.isatty()
    profiles = profile_columns(files, min_avg_length=args.min_avg_length, max_rows=args.max_rows, progress=progress)
    out.print(f"{len(profiles)} column(s) passed the length filter")
    joins = find_joins(profiles, threshold=args.threshold, min_distinct=args.min_distinct, min_df=args.min_df,
                       max_df_fraction=args.max_df_fraction, max_df_floor=args.max_df_floor, progress=progress)
    joins.to_csv(args.output, index=False)
    out.print(f"{len(joins)} joinable pair(s) written to [bold]{args.output}[/bold]")

    table = new_table("Score", "Column", "Joins with", "Shared")
    for row in joins.head(args.top).itertuples():
        table.add_row(
            f"[green]{row.score:.3f}[/green]",
            f"[bold]{row.column_1}[/bold]\n[dim]{row.package_1} · {row.resource_1}[/dim]",
            f"[bold]{row.column_2}[/bold]\n[dim]{row.package_2} · {row.resource_2}[/dim]",
            str(row.intersection),
        )
    if table.row_count:
        out.print(table)
    return 0


def cmd_show(args) -> int:
    if not args.joins.exists():
        err.print(f"[red]{args.joins} not found.[/red] Run `analyze.py joins` first.")
        return 1
    joins = load_joins(args.joins)
    matches = find_columns(joins, args.column)
    if not matches:
        err.print(f"No column matches {args.column!r} in {args.joins}.")
        return 1
    if len(matches) > 1:
        err.print(f"{args.column!r} is ambiguous; use one of these ids:")
        for ref in matches:
            err.print(f"  {ref.id}")
        return 2

    ref = matches[0]

    def samples(r: ColumnRef) -> list:
        return sample_values(args.data, r, n=args.samples, max_rows=args.max_rows)

    out.print(Panel("\n".join(f"· {v}" for v in samples(ref)) or "[dim]no values[/dim]",
                    title=f"[bold]{ref.column}[/bold]  [dim]{ref.package} · {ref.resource}[/dim]",
                    border_style="cyan", expand=False))
    out.print(f"[dim]{ref.id}[/dim]")

    table = Table(box=box.HORIZONTALS, show_lines=True, padding=(0, 2), pad_edge=False,
                  header_style="bold", border_style="grey30")
    table.add_column("Score", justify="right", style="green", width=6)
    table.add_column("Column", style="bold")
    table.add_column("Dataset", style="dim", max_width=34, overflow="ellipsis")
    table.add_column("Sample values", max_width=52, overflow="fold")
    for row in joins_for(joins, ref).head(args.top or None).itertuples(index=False):
        other = ColumnRef(row.package, row.resource, row.file, row.column)
        table.add_row(f"{row.score:.3f}", row.column, f"{row.package} · {row.resource}",
                      " [dim]·[/dim] ".join(samples(other)) or "[dim]—[/dim]")
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

    parser = argparse.ArgumentParser(prog="analyze.py", description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true", help="log details")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = commands.add_parser("owners", parents=[data, filters], help="count packages per owner division")
    p.set_defaults(run=cmd_owners)

    p = commands.add_parser("list", parents=[data, filters], help="list the selected packages")
    p.set_defaults(run=cmd_list)

    p = commands.add_parser("joins", parents=[data, filters], help="find joinable column pairs, write a CSV")
    p.add_argument("--output", type=Path, default=Path("joins.csv"), help="default: joins.csv")
    p.add_argument("--top", type=int, default=20, help="print the N best pairs (default: 20)")
    p.add_argument("--no-progress", action="store_true", help="hide progress bars")
    algo = p.add_argument_group("algorithm")
    algo.add_argument("--threshold", type=float, default=0.1, help="minimum score (default: 0.1)")
    algo.add_argument("--min-distinct", type=int, default=8, help="minimum distinct values per column (default: 8)")
    algo.add_argument("--min-avg-length", type=float, default=8,
                      help="skip columns with average value length <= this (default: 8)")
    algo.add_argument("--min-df", type=int, default=2, help="pair on values in at least N columns (default: 2)")
    algo.add_argument("--max-df-fraction", type=float, default=0.005,
                      help="...and in at most this fraction of columns (default: 0.005)")
    algo.add_argument("--max-df-floor", type=int, default=5, help="...but never fewer than N columns (default: 5)")
    algo.add_argument("--max-rows", type=int, help="read at most N rows per CSV")
    p.set_defaults(run=cmd_joins)

    p = commands.add_parser("show", parents=[data], help="show what one column joins with")
    p.add_argument("column", metavar="COLUMN", help="[PACKAGE/]RESOURCE::COLUMN, COLUMN, or a column id")
    p.add_argument("-j", "--joins", type=Path, default=Path("joins.csv"), help="join table (default: joins.csv)")
    p.add_argument("--samples", type=int, default=3, help="sample values per column (default: 3)")
    p.add_argument("--top", type=int, default=25, help="show the N best matches, 0 for all (default: 25)")
    p.add_argument("--max-rows", type=int, help="read at most N rows per CSV when sampling")
    p.set_defaults(run=cmd_show)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        return args.run(args)
    except FileNotFoundError as e:
        err.print(f"[red]error:[/red] {e}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
