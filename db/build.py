"""Build and update the Toronto Open Data DuckDB database instance.

Scans the download data folder and creates/updates schemas and views for all
completed datasets.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

# Add parent directory to path to allow importing db.instance
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.instance import TorontoDuckDB


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="duckdb-build",
        description="Sync downloaded Open Data Toronto datasets into DuckDB.",
    )
    parser.add_argument(
        "-d",
        "--data",
        type=Path,
        default=Path("download/data"),
        help="path to downloaded data folder (default: download/data)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("db/toronto_opendata.duckdb"),
        help="DuckDB database file path (default: db/toronto_opendata.duckdb)",
    )
    parser.add_argument(
        "--materialize",
        action="store_true",
        help="create physical tables instead of views (consumes disk space)",
    )

    args = parser.parse_args(argv)
    console = Console()

    console.print(f"[bold cyan]Toronto Open Data -> DuckDB Builder[/bold cyan]")
    console.print(f"  Data Directory: [yellow]{args.data}[/yellow]")
    console.print(f"  DuckDB Path:    [yellow]{args.db}[/yellow]")
    console.print(f"  Mode:           [magenta]{'Materialized Tables' if args.materialize else 'Zero-Copy Views'}[/magenta]\n")

    manager = TorontoDuckDB(db_path=args.db, data_dir=args.data)
    stats = manager.sync_all(materialize=args.materialize)

    table = Table(title="DuckDB Synchronization Summary", show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="dim")
    table.add_column("Value", justify="right", style="bold green")

    table.add_row("Total Catalogue Packages", str(stats["catalog_packages"]))
    table.add_row("Downloaded Packages Registered", str(stats["packages_registered"]))
    table.add_row("Views / Tables Active", str(stats["views_registered"]))
    if stats["views_failed"] > 0:
        table.add_row("Views Failed", f"[red]{stats['views_failed']}[/red]")
    table.add_row("DuckDB File Location", stats["db_path"])

    console.print(table)
    console.print("\n[bold green]Success![/bold green] You can query this database using `python -m db.cli query \"<SQL>\"` or any DuckDB client.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
