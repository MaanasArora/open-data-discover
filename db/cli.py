"""Command-line interface to inspect and query the Toronto Open Data DuckDB instance."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.instance import TorontoDuckDB


def cmd_query(manager: TorontoDuckDB, args) -> int:
    con = manager.connect()
    console = Console()
    try:
        rel = con.execute(args.sql)
        description = rel.description
        if not description:
            console.print("[green]Statement executed successfully.[/green]")
            return 0

        columns = [desc[0] for desc in description]
        rows = rel.fetchmany(args.limit)

        table = Table(title=f"Query Results (limit {args.limit})", show_header=True, header_style="bold cyan")
        for col in columns:
            table.add_column(col)

        for row in rows:
            table.add_row(*[str(val) if val is not None else "[dim]NULL[/dim]" for val in row])

        console.print(table)
        total_fetched = len(rows)
        console.print(f"[dim]Showing {total_fetched} row(s)[/dim]")
        return 0
    except Exception as e:
        console.print(f"[bold red]Query Error:[/bold red] {e}")
        return 1
    finally:
        con.close()


def cmd_tables(manager: TorontoDuckDB, args) -> int:
    con = manager.connect()
    console = Console()
    try:
        sql = """
        SELECT 
            package_name,
            schema_name,
            view_name,
            format,
            is_view_active
        FROM metadata.resources
        ORDER BY package_name, view_name;
        """
        rows = con.execute(sql).fetchall()
        if not rows:
            console.print("[yellow]No registered views/tables found. Have you run `python -m db.build`?[/yellow]")
            return 0

        table = Table(title="Registered Dataset Views / Tables", show_header=True, header_style="bold magenta")
        table.add_column("Package Name", style="cyan")
        table.add_column("Schema.View", style="green")
        table.add_column("Format", style="yellow")
        table.add_column("Status", style="bold")

        for pkg, schema, view, fmt, active in rows:
            status_str = "[green]ACTIVE[/green]" if active else "[red]ERROR[/red]"
            table.add_row(pkg, f"{schema}.{view}", fmt, status_str)

        console.print(table)
        console.print(f"\n[dim]Total: {len(rows)} registered resource view(s)[/dim]")
        return 0
    finally:
        con.close()


def cmd_search(manager: TorontoDuckDB, args) -> int:
    con = manager.connect()
    console = Console()
    try:
        sql = """
        SELECT 
            name,
            title,
            num_resources,
            formats,
            is_complete
        FROM metadata.packages
        WHERE lower(name) LIKE ? OR lower(title) LIKE ?
        ORDER BY name
        LIMIT 50;
        """
        pattern = f"%{args.term.lower()}%"
        rows = con.execute(sql, [pattern, pattern]).fetchall()

        table = Table(title=f"Catalogue Search for '{args.term}'", show_header=True, header_style="bold blue")
        table.add_column("Package Name", style="cyan")
        table.add_column("Title", style="white")
        table.add_column("Resources", justify="right")
        table.add_column("Formats", style="yellow")
        table.add_column("Downloaded?", justify="center")

        for name, title, n_res, fmts, complete in rows:
            dl_str = "[green]YES[/green]" if complete else "[dim]NO[/dim]"
            fmt_str = ", ".join(fmts) if fmts else ""
            table.add_row(name, title or "", str(n_res), fmt_str, dl_str)

        console.print(table)
        console.print(f"\n[dim]Found {len(rows)} package(s)[/dim]")
        return 0
    finally:
        con.close()


def cmd_shell(manager: TorontoDuckDB, args) -> int:
    console = Console()
    console.print(f"[bold cyan]Opening interactive DuckDB session on {manager.db_path}...[/bold cyan]")
    console.print("[dim]Type your SQL commands. Type 'quit' or 'exit' to leave.[/dim]\n")

    con = manager.connect()
    try:
        while True:
            try:
                line = input("duckdb> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line.lower() in ("quit", "exit", "\\q"):
                break
            try:
                rel = con.execute(line)
                desc = rel.description
                if desc:
                    cols = [d[0] for d in desc]
                    rows = rel.fetchmany(50)
                    t = Table(show_header=True, header_style="bold cyan")
                    for c in cols:
                        t.add_column(c)
                    for r in rows:
                        t.add_row(*[str(v) if v is not None else "NULL" for v in r])
                    console.print(t)
                else:
                    console.print("[green]OK[/green]")
            except Exception as e:
                console.print(f"[bold red]Error:[/bold red] {e}")
    finally:
        con.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="duckdb-cli",
        description="Inspect and query the Toronto Open Data DuckDB instance.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("db/toronto_opendata.duckdb"),
        help="DuckDB database file path (default: db/toronto_opendata.duckdb)",
    )
    parser.add_argument(
        "-d",
        "--data",
        type=Path,
        default=Path("download/data"),
        help="path to downloaded data folder (default: download/data)",
    )

    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("query", help="run an SQL query")
    p.add_argument("sql", help="SQL query to execute")
    p.add_argument("--limit", type=int, default=50, help="max rows to display (default: 50)")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("tables", help="list registered views and tables")
    p.set_defaults(func=cmd_tables)

    p = sub.add_parser("search", help="search catalogue packages by keyword")
    p.add_argument("term", help="search term")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("shell", help="open an interactive SQL prompt")
    p.set_defaults(func=cmd_shell)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manager = TorontoDuckDB(db_path=args.db, data_dir=args.data)
    return args.func(manager, args)


if __name__ == "__main__":
    sys.exit(main())
