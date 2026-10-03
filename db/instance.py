"""Toronto Open Data DuckDB Manager.

Creates and maintains a DuckDB database instance connected to City of Toronto
Open Data datasets downloaded in the workspace.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import duckdb

log = logging.getLogger("toronto_duckdb")


def sanitize_identifier(text: str) -> str:
    """Turn an arbitrary string into a clean SQL identifier."""
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", text.strip().lower())
    clean = clean.strip("_")
    if not clean or clean[0].isdigit():
        clean = f"t_{clean}"
    return clean[:60]


@dataclass
class ResourceRegistration:
    package_name: str
    resource_id: str
    resource_name: str
    format: str
    file_path: Path
    schema_name: str
    view_name: str
    success: bool
    error: Optional[str] = None


class TorontoDuckDB:
    """Manages the DuckDB instance for City of Toronto Open Data."""

    SUPPORTED_EXTENSIONS = {"spatial", "excel"}

    def __init__(
        self,
        db_path: Path | str = "db/toronto_opendata.duckdb",
        data_dir: Path | str = "download/data",
    ):
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> duckdb.DuckDBPyConnection:
        """Connect to DuckDB and load necessary extensions."""
        con = duckdb.connect(str(self.db_path))
        self.init_extensions(con)
        self.init_metadata_schema(con)
        return con

    def init_extensions(self, con: duckdb.DuckDBPyConnection) -> None:
        """Install and load required DuckDB extensions."""
        for ext in ("spatial", "excel"):
            try:
                con.execute(f"INSTALL {ext}; LOAD {ext};")
            except Exception as e:
                log.warning("Could not load DuckDB extension %s: %s", ext, e)

    def init_metadata_schema(self, con: duckdb.DuckDBPyConnection) -> None:
        """Create metadata schemas and tracking tables."""
        con.execute("CREATE SCHEMA IF NOT EXISTS metadata;")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata.packages (
                package_id VARCHAR PRIMARY KEY,
                name VARCHAR,
                title VARCHAR,
                publisher VARCHAR,
                metadata_modified VARCHAR,
                num_resources INTEGER,
                formats VARCHAR[],
                is_complete BOOLEAN,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata.resources (
                resource_id VARCHAR PRIMARY KEY,
                package_name VARCHAR,
                name VARCHAR,
                format VARCHAR,
                file_name VARCHAR,
                file_path VARCHAR,
                file_bytes BIGINT,
                schema_name VARCHAR,
                view_name VARCHAR,
                is_view_active BOOLEAN,
                error VARCHAR,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

    def sync_catalog(self, con: duckdb.DuckDBPyConnection) -> int:
        """Populate metadata.packages from catalog.json if available."""
        catalog_path = self.data_dir / "catalog.json"
        if not catalog_path.is_file():
            return 0

        try:
            with open(catalog_path, "r", encoding="utf-8") as f:
                cat_data = json.load(f)
        except Exception as e:
            log.error("Failed to read %s: %s", catalog_path, e)
            return 0

        packages = cat_data.get("packages", [])
        for pkg in packages:
            pid = pkg.get("id")
            pname = pkg.get("name")
            title = pkg.get("title")
            modified = pkg.get("metadata_modified")
            res_list = pkg.get("resources", [])
            formats = list(set(r.get("format", "").upper() for r in res_list if r.get("format")))

            # Check if package is complete locally
            pkg_json = self.data_dir / pname / "package.json"
            marker = self.data_dir / pname / ".download"
            is_complete = pkg_json.is_file() and not marker.exists()

            con.execute(
                """
                INSERT OR REPLACE INTO metadata.packages
                (package_id, name, title, metadata_modified, num_resources, formats, is_complete, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
                """,
                [pid, pname, title, modified, len(res_list), formats, is_complete],
            )
        return len(packages)

    def _candidate_queries_for_resource(self, file_path: Path, fmt: str) -> List[str]:
        """Construct candidate SQL queries to read a specific data file, ordered by likelihood."""
        posix_path = file_path.as_posix()
        ext = file_path.suffix.lower().lstrip(".")
        fmt_upper = fmt.upper()
        candidates = []

        if ext == "csv" or fmt_upper == "CSV":
            candidates.append(f"SELECT * FROM read_csv_auto('{posix_path}', ignore_errors=true, union_by_name=true, strict_mode=false)")
            candidates.append(f"SELECT * FROM read_csv_auto('{posix_path}', ignore_errors=true)")

        elif ext == "parquet" or fmt_upper == "PARQUET":
            candidates.append(f"SELECT * FROM read_parquet('{posix_path}')")

        elif ext == "geojson" or fmt_upper == "GEOJSON":
            candidates.append(f"SELECT * FROM ST_Read('{posix_path}')")
            candidates.append(f"SELECT * FROM read_json_auto('{posix_path}', ignore_errors=true)")
            candidates.append(f"SELECT * FROM read_csv_auto('{posix_path}', ignore_errors=true, strict_mode=false)")

        elif ext == "json" or fmt_upper == "JSON":
            candidates.append(f"SELECT * FROM read_json_auto('{posix_path}', ignore_errors=true)")
            candidates.append(f"SELECT * FROM ST_Read('{posix_path}')")

        elif ext == "zip" or (ext == "zip" and fmt_upper == "SHP"):
            candidates.append(f"SELECT * FROM ST_Read('/vsizip/{posix_path}')")

        elif ext in ("gpkg", "shp") or fmt_upper in ("GPKG", "SHP"):
            candidates.append(f"SELECT * FROM ST_Read('{posix_path}')")

        elif ext in ("xlsx", "xlsm") or fmt_upper in ("XLSX", "XLSM"):
            candidates.append(f"SELECT * FROM read_xlsx('{posix_path}')")

        return candidates

    def register_package(
        self,
        con: duckdb.DuckDBPyConnection,
        pkg_dir: Path,
        materialize: bool = False,
    ) -> List[ResourceRegistration]:
        """Register all supported resources of a completed package as DuckDB views."""
        pkg_json_path = pkg_dir / "package.json"
        if not pkg_json_path.is_file():
            return []

        try:
            with open(pkg_json_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception as e:
            log.error("Failed to load %s: %s", pkg_json_path, e)
            return []

        pkg_name = state.get("package", {}).get("name") or pkg_dir.name
        schema_name = sanitize_identifier(pkg_name)
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}";')

        files_map = state.get("files", {})
        resources_list = state.get("package", {}).get("resources", [])
        res_lookup = {r["id"]: r for r in resources_list if "id" in r}

        registrations = []
        for rid, finfo in files_map.items():
            fname = finfo.get("file")
            if not fname:
                continue
            file_path = (pkg_dir / fname).resolve()
            if not file_path.is_file():
                continue

            r_meta = res_lookup.get(rid, {})
            r_name = r_meta.get("name") or file_path.stem
            fmt = r_meta.get("format") or file_path.suffix.lstrip(".")
            view_name = sanitize_identifier(r_name)

            candidate_sqls = self._candidate_queries_for_resource(file_path, fmt)
            if not candidate_sqls:
                continue

            cmd_type = "TABLE" if materialize else "VIEW"
            success = False
            err_msg = None

            for select_sql in candidate_sqls:
                create_sql = f'CREATE OR REPLACE {cmd_type} "{schema_name}"."{view_name}" AS {select_sql};'
                try:
                    con.execute(create_sql)
                    alias_name = f"{schema_name}__{view_name}"
                    con.execute(
                        f'CREATE OR REPLACE VIEW main."{alias_name}" AS SELECT * FROM "{schema_name}"."{view_name}";'
                    )
                    success = True
                    err_msg = None
                    break
                except Exception as e:
                    err_msg = str(e)

            if not success:
                log.warning("Could not register %s.%s: %s", schema_name, view_name, err_msg)

            # Record in metadata.resources
            con.execute(
                """
                INSERT OR REPLACE INTO metadata.resources
                (resource_id, package_name, name, format, file_name, file_path, file_bytes,
                 schema_name, view_name, is_view_active, error, registered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
                """,
                [
                    rid,
                    pkg_name,
                    r_name,
                    fmt,
                    fname,
                    str(file_path),
                    finfo.get("bytes"),
                    schema_name,
                    view_name,
                    success,
                    err_msg,
                ],
            )

            registrations.append(
                ResourceRegistration(
                    package_name=pkg_name,
                    resource_id=rid,
                    resource_name=r_name,
                    format=fmt,
                    file_path=file_path,
                    schema_name=schema_name,
                    view_name=view_name,
                    success=success,
                    error=err_msg,
                )
            )

        return registrations

    def sync_all(self, materialize: bool = False) -> Dict[str, Any]:
        """Scan all downloaded packages, sync metadata, and register all views."""
        con = self.connect()
        try:
            cat_count = self.sync_catalog(con)

            total_views = 0
            failed_views = 0
            packages_registered = 0

            if self.data_dir.is_dir():
                for pdir in sorted(self.data_dir.iterdir()):
                    if pdir.is_dir() and not pdir.name.startswith("."):
                        marker = pdir / ".download"
                        pkg_json = pdir / "package.json"
                        # Only register completed packages
                        if pkg_json.is_file() and not marker.exists():
                            regs = self.register_package(con, pdir, materialize=materialize)
                            if regs:
                                packages_registered += 1
                                for r in regs:
                                    if r.success:
                                        total_views += 1
                                    else:
                                        failed_views += 1

            return {
                "catalog_packages": cat_count,
                "packages_registered": packages_registered,
                "views_registered": total_views,
                "views_failed": failed_views,
                "db_path": str(self.db_path),
            }
        finally:
            con.close()
