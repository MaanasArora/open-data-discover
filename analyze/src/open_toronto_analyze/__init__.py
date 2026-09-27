"""Find joinable columns across City of Toronto Open Data packages downloaded with open-toronto-dl.

    from open_toronto_analyze import select_packages, csv_files, profile_columns, find_joins

    selection = select_packages("../download/data", owners=["transit"], limit=20)
    joins = find_joins(profile_columns(csv_files(selection.packages)))
"""

from .columns import ColumnProfile, ColumnRef, csv_files, profile_columns
from .joins import JOIN_COLUMNS, find_joins
from .lookup import find_columns, joins_for, load_joins, sample_values
from .packages import Package, Selection, load_package, select_packages

__version__ = "0.1.0"

__all__ = [
    "JOIN_COLUMNS",
    "ColumnProfile",
    "ColumnRef",
    "Package",
    "Selection",
    "csv_files",
    "find_columns",
    "find_joins",
    "joins_for",
    "load_joins",
    "load_package",
    "profile_columns",
    "sample_values",
    "select_packages",
]
