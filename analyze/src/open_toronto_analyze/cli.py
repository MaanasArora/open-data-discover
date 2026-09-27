"""open-toronto-analyze: find joinable columns across downloaded Open Data packages.

Examples::

    open-toronto-analyze owners
    open-toronto-analyze list --owner transit --limit 10
    open-toronto-analyze joins --owner "parks" --owner "transportation" -o joins.csv
    open-toronto-analyze joins --packages-file packages.txt --threshold 0.3
    open-toronto-analyze show "neighbourhoods/Neighbourhoods - 4326.csv::Neighbourhood"
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from open_toronto_analyze import core

console = Console()
err = Console(stderr=True)


def _default_root() -> Path:
    return Path(os.environ.get("OPEN_TORONTO_DL_ROOT", core.DEFAULT_ROOT))


def _select(args) -> core.Selection:
    names = list(args.package or [])
    for f in args.packages_file or []:
        names += core.read_package_list(f)
    sel = core.select_packages(args.data, owners=args.owner, packages=names or None, limit=args.limit)
    if sel.missing:
        err.print(f"[yellow]Not downloaded:[/yellow] {', '.join(sel.missing)}")
    if sel.incomplete:
        shown = ", ".join(sel.incomplete[:10]) + (" ..." if len(sel.incomplete) > 10 else "")
        err.print(f"[yellow]Skipping {len(sel.incomplete)} incomplete package(s):[/yellow] {shown}")
    return sel


def _selection_line(sel: core.Selection) -> str:
    return f"{len(sel.packages)} of {sel.total_complete} complete package(s) selected"


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_owners(args) -> int:
    sel = _select(args)
    counts = Counter(core.owner_of(p) or "(none)" for p in sel.packages)
    table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    table.add_column("Packages", justify="right")
    table.add_column("Owner (owner_division)")
    for owner, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        table.add_row(str(n), owner)
    console.print(table)
    console.print(f"[dim]{_selection_line(sel)}[/dim]")
    return 0


def cmd_list(args) -> int:
    sel = _select(args)
    table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    table.add_column("Package")
    table.add_column("Owner", style="dim", max_width=36, overflow="ellipsis", no_wrap=True)
    table.add_column("CSVs", justify="right")
    for pkg in sel.packages:
        n_csv = len(core.csv_resources([pkg]))
        table.add_row(pkg.name, core.owner_of(pkg), str(n_csv) if n_csv else "[dim]0[/dim]")
    console.print(table)
    console.print(f"[dim]{_selection_line(sel)}[/dim]")
    return 0


def cmd_joins(args) -> int:
    sel = _select(args)
    resources = core.csv_resources(sel.packages)
    console.print(f"{_selection_line(sel)}, {len(resources)} CSV file(s)")
    if not resources:
        err.print("[red]Nothing to analyze.[/red]")
        return 1

    progress = not args.no_progress and sys.stderr.isatty()
    profiles = core.profile_columns(resources, min_avg_length=args.min_avg_length,
                                    max_rows=args.max_rows, progress=progress)
    console.print(f"{len(profiles)} column(s) passed the length filter")
    joins = core.find_joins(profiles, threshold=args.threshold, min_distinct=args.min_distinct,
                            min_df=args.min_df, max_df_fraction=args.max_df_fraction,
                            max_df_floor=args.max_df_floor, progress=progress)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    joins.to_csv(args.output, index=False)
    console.print(f"{len(joins)} joinable pair(s) written to [bold]{args.output}[/bold]")

    if len(joins) and args.top:
        table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
        table.add_column("Score", justify="right", style="green")
        table.add_column("Column")
        table.add_column("Joins with")
        table.add_column("Shared", justify="right", style="dim")
        for row in joins.head(args.top).itertuples(index=False):
            table.add_row(
                f"{row.score:.3f}",
                f"[bold]{row.column_1}[/bold]\n[dim]{row.package_1} · {row.resource_1}[/dim]",
                f"[bold]{row.column_2}[/bold]\n[dim]{row.package_2} · {row.resource_2}[/dim]",
                str(row.intersection),
            )
        console.print(table)
    return 0


def cmd_show(args) -> int:
    if not args.joins.exists():
        err.print(f"[red]{args.joins} not found.[/red] Run `open-toronto-analyze joins` first.")
        return 1
    joins = pd.read_csv(args.joins, dtype={c: str for c in core.JOIN_COLUMNS[:8]})
    matches = core.find_columns(joins, args.column)
    if not matches:
        err.print(f"[red]No column matching[/red] {args.column!r} [red]in {args.joins}[/red]")
        return 1
    if len(matches) > 1:
        err.print(f"{args.column!r} is ambiguous; use one of these ids:")
        for ref in matches:
            err.print(f"  {ref.id}")
        return 2

    ref = matches[0]
    n = args.samples

    def samples(package, file, column):
        return core.sample_values(args.data, package, file, column, n=n, max_rows=args.max_rows)

    values = samples(ref.package, ref.file, ref.column)
    console.print(Panel(
        "\n".join(f"· {v}" for v in values) or "[dim]no values[/dim]",
        title=f"[bold]{ref.column}[/bold]  [dim]{ref.package} · {ref.resource}[/dim]",
        border_style="cyan",
        expand=False,
    ))
    console.print(f"[dim]{ref.id}[/dim]")

    joinable = core.joinable_for(joins, ref)
    if joinable.empty:
        console.print("[dim]No joinable columns found.[/dim]")
        return 0
    if args.top:
        joinable = joinable.head(args.top)

    table = Table(box=box.HORIZONTALS, show_lines=True, padding=(0, 2), pad_edge=False,
                  header_style="bold", border_style="grey30")
    table.add_column("Score", justify="right", style="green", width=6)
    table.add_column("Column", style="bold")
    table.add_column("Dataset", style="dim", max_width=34, overflow="ellipsis")
    table.add_column("Sample values", max_width=52, overflow="fold")
    for row in joinable.itertuples(index=False):
        vals = samples(row.package, row.file, row.column)
        table.add_row(
            f"{row.score:.3f}",
            row.column,
            f"{row.package} · {row.resource}",
            " [dim]·[/dim] ".join(vals) if vals else "[dim]—[/dim]",
        )
    console.print(table)
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    data = argparse.ArgumentParser(add_help=False)
    data.add_argument("-d", "--data", type=Path, default=_default_root(),
                      help=f"open-toronto-dl data folder (default: $OPEN_TORONTO_DL_ROOT or {core.DEFAULT_ROOT})")

    filters = argparse.ArgumentParser(add_help=False)
    g = filters.add_argument_group("package selection (filters combine with AND; repeats with OR)")
    g.add_argument("-o", "--owner", action="append", metavar="TEXT",
                   help="owner_division contains TEXT (case-insensitive); repeatable")
    g.add_argument("-p", "--package", action="append", metavar="NAME",
                   help="package name, id or portal URL; repeatable")
    g.add_argument("-P", "--packages-file", action="append", type=Path, metavar="FILE",
                   help="file with one package per line (# comments); repeatable")
    g.add_argument("-n", "--limit", type=int, metavar="N", help="at most N packages (by name, after other filters)")

    parser = argparse.ArgumentParser(
        prog="open-toronto-analyze",
        description="Find joinable columns across packages downloaded with open-toronto-dl.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {core.__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="-v info, -vv debug")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("owners", parents=[data, filters], help="list owners (divisions) and package counts")
    p.set_defaults(func=cmd_owners)

    p = sub.add_parser("list", parents=[data, filters], help="list the packages a selection includes")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("joins", parents=[data, filters], help="compute joinable column pairs and write a CSV")
    p.add_argument("--output", type=Path, default=Path("joins.csv"), help="output CSV (default: joins.csv)")
    p.add_argument("--top", type=int, default=20, help="print the N best pairs (default: 20, 0 for none)")
    a = p.add_argument_group("algorithm")
    a.add_argument("--threshold", type=float, default=0.1, help="minimum score (default: 0.1)")
    a.add_argument("--min-distinct", type=int, default=8,
                   help="both columns need at least this many distinct values (default: 8)")
    a.add_argument("--min-avg-length", type=float, default=8,
                   help="skip columns whose average value length is <= this (default: 8)")
    a.add_argument("--min-df", type=int, default=2, help="ignore values in fewer columns when pairing (default: 2)")
    a.add_argument("--max-df-fraction", type=float, default=0.005,
                   help="ignore values in more than this fraction of columns when pairing (default: 0.005)")
    a.add_argument("--max-df-floor", type=int, default=5, help="lower bound for that cutoff (default: 5)")
    a.add_argument("--max-rows", type=int, help="read at most N rows per CSV")
    p.add_argument("--no-progress", action="store_true", help="hide progress bars")
    p.set_defaults(func=cmd_joins)

    p = sub.add_parser("show", parents=[data], help="show the columns joinable with one column")
    p.add_argument("column", metavar="COLUMN", help="[PACKAGE/]RESOURCE::COLUMN, just COLUMN, or an id")
    p.add_argument("-j", "--joins", type=Path, default=Path("joins.csv"),
                   help="join table from `joins` (default: joins.csv)")
    p.add_argument("--samples", type=int, default=3, help="sample values per column (default: 3)")
    p.add_argument("--top", type=int, default=25, help="show the N best matches (default: 25, 0 for all)")
    p.add_argument("--max-rows", type=int, help="read at most N rows per CSV when sampling")
    p.set_defaults(func=cmd_show)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    level = {0: logging.WARNING, 1: logging.INFO}.get(args.verbose, logging.DEBUG)
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")
    try:
        return args.func(args)
    except FileNotFoundError as e:
        err.print(f"[red]error:[/red] {e}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
