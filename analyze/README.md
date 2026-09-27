# analyze

Find joinable columns across City of Toronto Open Data packages in the `download` folder.
Only complete packages are read; incomplete ones are skipped. Ported from `scratch/main.ipynb`.

```bash
uv run analyze.py owners                          # divisions + package counts
uv run analyze.py list --owner transit            # preview a selection
uv run analyze.py run --owner parks --limit 50    # writes results/
uv run analyze.py show "Neighbourhoods - 4326::AREA_NAME"
uv run analyze.py export                          # writes joins.csv
```

The data folder defaults to `../download/data` (override with `-d PATH` or `$OPEN_TORONTO_DL_ROOT`).

## Selecting packages

Filters work with `owners`, `list` and `run`. Different filters combine with AND; repeating one combines with OR.

| Option | Selects |
| --- | --- |
| `-o, --owner TEXT` | owner division contains TEXT (case-insensitive) |
| `-p, --package NAME` | a package by name, id or portal URL |
| `-P, --packages-file FILE` | packages listed one per line (`#` comments) |
| `-n, --limit N` | the first N packages by name, after the other filters |

## Results

`run` writes to `results/` (`-r PATH` to change):

| File | Contents |
| --- | --- |
| `columns.parquet` | One row per profiled column: `id` (row number), `key` (`resource_id::column`), `package`, `resource_id`, `resource`, `file`, `column`, `n_values`, `n_distinct`, `avg_length`, `samples` |
| `joins.parquet` | One row per candidate pair of columns from different files, best first: `id_a`, `id_b` (column ids, a < b), `shared` (distinct values in common), `containment_a_in_b`, `containment_b_in_a`, `jaccard`, `score` |
| `manifest.json` | Selection, parameters, packages analyzed with each resource's `last_modified`, counts. Written last: no manifest means an incomplete run. |

Scores, for a pair sharing `shared` distinct values:

- `containment_a_in_b = shared / n_distinct(a)` (and the reverse)
- `jaccard = shared / (n_distinct(a) + n_distinct(b) - shared)`
- `score = max(containment_a_in_b, containment_b_in_a)`

With `run --idf` (experimental), shared values are weighted by rarity across datasets,
`log((1 + N) / (1 + df)) + 1`, and the containments and Jaccard use those weights. It is off by default:
on Toronto's data the most widespread values are ward names, so it mostly lowered good joins and raised
weak ones (dates).

There is no score threshold: every candidate pair is kept, sorted by score. To find a column's joins,
take the rows where `id_a` or `id_b` is its `id`; they are already in order.

`export` writes the same joins as a readable CSV (`joins.csv`), with package, resource, file and column
names for both sides. `show COLUMN` lists the joins of one column. COLUMN is `[PACKAGE/]RESOURCE::COLUMN`,
just `COLUMN`, a key, or the label `show` prints.

The Parquet files use Snappy compression and 32-bit integers, so JavaScript readers such as
[hyparquet](https://github.com/hyparam/hyparquet) return plain numbers.

## Files

| File | Step |
| --- | --- |
| `packages.py` | 1. Find complete packages in the download folder; select by owner / name / limit |
| `columns.py` | 2. Read CSVs and profile each column: distinct values, stats, samples |
| `joins.py` | 3-5. Presence matrix, candidate pairs, exact shared counts, scores |
| `results.py` | Write / read a results folder |
| `views.py` | Readable views: the join table, one column's joins, column lookup |
| `analyze.py` | The command line |

## Algorithm

1. Select complete packages (see above) and their CSV files.
2. Profile every CSV column whose average value length exceeds `--min-avg-length` (6), skipping `_id`:
   its distinct values after `strip().lower()`.
3. Build a columns × values presence matrix.
4. Candidate pairs are columns from different files, each with at least `--min-distinct` (8) distinct
   values, that share a value found in at least `--min-df` (2) and at most
   `max(--max-df-floor, --max-df-fraction × columns)` columns.
5. Count exact shared values for each candidate pair and score it.
