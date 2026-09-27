# analyze

Find joinable columns across City of Toronto Open Data packages in the `download` folder.
Only complete packages are read; incomplete ones are skipped. Ported from `scratch/main.ipynb`.

```bash
uv run analyze.py owners                          # divisions + package counts
uv run analyze.py list --owner transit            # preview a selection
uv run analyze.py joins --owner parks --limit 50  # writes joins.csv
uv run analyze.py show "Neighbourhoods - 4326::AREA_NAME"
```

The data folder defaults to `../download/data` (override with `-d PATH` or `$OPEN_TORONTO_DL_ROOT`).

## Selecting packages

Filters work with `owners`, `list` and `joins`. Different filters combine with AND; repeating one combines with OR.

| Option | Selects |
| --- | --- |
| `-o, --owner TEXT` | owner division contains TEXT (case-insensitive) |
| `-p, --package NAME` | a package by name, id or portal URL |
| `-P, --packages-file FILE` | packages listed one per line (`#` comments) |
| `-n, --limit N` | the first N packages by name, after the other filters |

## Output

`joins` writes one row per joinable pair: `package_*`, `resource_*`, `file_*`, `column_*`,
`intersection` (shared distinct values), `distinct_*`, `containment_1_in_2`, `containment_2_in_1`
and `score` (the larger containment), best first.

`show COLUMN` reads that CSV and lists what one column joins with, with sample values.
COLUMN is `[PACKAGE/]RESOURCE::COLUMN` (resource name or file name), just `COLUMN`, or the id `show` prints.

## Files

| File | Step |
| --- | --- |
| `packages.py` | 1. Find complete packages in the download folder; select by owner / name / limit |
| `columns.py` | 2. Read CSVs and profile each column's distinct values |
| `joins.py` | 3-5. Presence matrix, candidate pairs, exact overlaps, scores |
| `lookup.py` | Query a saved join table (used by `show`) |
| `analyze.py` | The command line |

## Algorithm

1. Profile every CSV column whose average value length exceeds `--min-avg-length` (8), skipping `_id`:
   its distinct values after `strip().lower()`.
2. Build a columns × values presence matrix. For finding candidate pairs, drop values found in fewer
   than `--min-df` (2) or more than `max(--max-df-floor, --max-df-fraction × columns)` columns.
3. Count exact shared values for candidate pairs on the full matrix.
4. Keep pairs from different files with score > `--threshold` (0.1) where both columns have at least
   `--min-distinct` (8) distinct values.
