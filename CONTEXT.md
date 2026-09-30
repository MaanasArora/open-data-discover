# Open Data Discover

Helps people find City of Toronto Open Data that can be combined, by finding columns in different datasets that share values, and collecting the data they choose into a downloadable set.

## Language

### Source data

**Dataset**:
One listing on open.toronto.ca, with its metadata and files.
_Avoid_: Package (except when talking about the CKAN API)

**Publisher**:
The City division that publishes a Dataset.
_Avoid_: Owner, owner division

**Resource**:
One file inside a Dataset.
_Avoid_: CSV, file

**Table**:
The rows one would treat as a single table: a Resource on its own, or several Resources of one Dataset combined (a series split by year or period, with duplicate map-projection copies set aside).
_Avoid_: Group, merged resource

**Data Dictionary**:
A Resource that describes the columns of other Resources in its Dataset rather than holding data.
_Avoid_: Readme

### Discovery

**Column**:
A named column of a Resource, with its distinct values.

**Join**:
A scored pair of Columns from different Resources that share values, suggesting their rows can be matched.
_Avoid_: Match, merge

**Score**:
How strongly a Join's Columns overlap: the larger of the two containments (the share of one Column's distinct values found in the other).

### Building a set

**Shopping List**:
The tree of Picks a user builds while following Joins, which they check out to get the data.
_Avoid_: Cart, map, linked list

**Pick**:
One Resource in a Shopping List, with the Column that joins it to its parent Pick (none for the first).
_Avoid_: Node, item

**Checkout**:
Turning a Shopping List into what the user takes away: the Picks' data and the Joins between them.

### Clean data

**Common Column**:
A kind of value many Datasets share and that gets a standard form, such as Ward, Neighbourhood, Time, Address or Street.
_Avoid_: Common table, shared column

**Boundary Version**:
The set of areas a Ward or Neighbourhood value refers to, such as the 25-ward model of 2018.

**Standard Key**:
A column added beside an original one, holding its value in a Common Column's standard form.

**Cleaned Table**:
A Table whose Common Columns have been given Standard Keys, published for download.
