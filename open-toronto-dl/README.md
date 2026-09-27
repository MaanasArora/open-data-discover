# open-toronto-dl

Download packages from the [City of Toronto Open Data](https://open.toronto.ca) CKAN API,
tracking download state with files instead of a database.

## Usage

```bash
uv sync                                        # create .venv and install
uv run open-toronto-dl get ttc-subway-delay-data
uv run open-toronto-dl crawl --limit 25        # refresh catalog.json, then download
uv run open-toronto-dl status                  # exit 1 if anything is incomplete
uv run open-toronto-dl resume                  # retry incomplete packages
uv run open-toronto-dl clean [--incomplete] [--orphans] [-n]
```

Or install it as a standalone tool: `uv tool install .`

Defaults: CSV resources only (`-f CSV,XLSX` or `-f '*'` for more), data in `./data`
(`-d PATH` or `$OPEN_TORONTO_DL_ROOT`).

From Python:

```python
from open_toronto_dl import Downloader

with Downloader("data", formats=["CSV"]) as dl:
    dl.get("ttc-subway-delay-data")
    for pkg in dl.iter_complete():           # incomplete packages are skipped
        for resource, path in pkg.resources():
            ...
```

## Layout

```
data/
  catalog.json                                     package_search snapshot (crawl / catalog)
  ttc-subway-delay-data/
    .download                                      package incomplete
    package.json                                   written last: local state + CKAN metadata
    subway-delay-data-2024__7a3f9c21.csv           complete
    subway-delay-data-2025__e0b41d88.csv.download  incomplete
```

**Anything ending in `.download` is incomplete.** Resources stream into `<file>.download`
and are renamed once their size checks out. A package folder's `.download` marker is
created before any work and removed only after every resource succeeded and
`package.json` was written, so interrupted downloads and refreshes never look complete.
