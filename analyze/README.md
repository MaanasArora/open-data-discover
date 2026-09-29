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
| `columns.parquet` | One row per profiled column: `id` (row number), `key` (`resource_id::column`), `package`, `resource_id`, `resource`, `file`, `column`, `n_values`, `n_distinct`, `avg_length`, `kind` (`number`, `date` or empty), `samples` |
| `joins.parquet` | One row per pair of columns from different files with enough evidence, best first: `id_a`, `id_b` (column ids, a < b), `shared` (distinct values in common), `containment_a_in_b`, `containment_b_in_a`, `jaccard`, `expected_a_in_b`, `expected_b_in_a` (containment expected by chance), `evidence_a_in_b`, `evidence_b_in_a`, `evidence`, `score` |
| `manifest.json` | Selection, parameters, packages analyzed with each resource's `last_modified`, counts. Written last: no manifest means an incomplete run. |

The **score** (0-100%) is the share of one column's values found in the other beyond what chance
explains, `(containment - expected) / (1 - expected)`, for the direction where it is larger. 100% means
every value is found; 0% means no more than chance would put there.

It is only reported when it is trustworthy: pairs need at least `--min-evidence` (10 nats) of
**evidence** that the overlap is not chance. Each of A's n distinct values is a trial, "is it in B?";
under the null B holds value v with probability p0(v), if linked at rate c = k / n (the containment).
The evidence is the log-likelihood ratio (10 nats: about 22,000 times likelier linked than by chance):

    evidence_a_in_b = sum over the k shared values: ln(c / p0(v)) + sum over the others: ln((1 - c) / (1 - p0(v)))

It is 0 unless c beats the average p0 (`expected_a_in_b`); `evidence` is the larger direction.

p0(v) is the larger of:

- co-occurrence: the share of other columns holding v, counting only columns unrelated to A (a column
  weighs 1 minus its overlap with A's other values). Ward names found in 30 ward columns stay rare;
  "toronto" found in 30 unrelated columns is common. A and B are left out; one pseudo-column keeps p0 > 0.
- density, when B holds numbers or dates: the share of the lattice points around v that B fills
  (`ordered.py`), for the shared values. Every id from 1 to 800, or every day of 2023, holds any
  value in range, so sharing one says nothing.

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
| `ordered.py` | 2. For number and date columns, the density of each value |
| `joins.py` | 3-5. Pairs sharing a value, the null, evidence |
| `results.py` | Write / read a results folder |
| `views.py` | Readable views: the join table, one column's joins, column lookup |
| `analyze.py` | The command line |

## Algorithm

1. Select complete packages (see above) and their CSV files.
2. Profile every CSV column whose average value length exceeds `--min-avg-length` (6), skipping `_id`:
   its distinct values after `strip().lower()`, and for numbers and dates the density at each value.
3. Build a columns × values presence matrix; every pair of columns sharing a value is a candidate.
4. Estimate p0 for each shared value (above), and score both directions of each pair.
5. Keep pairs with at least `--min-evidence` nats; sort by score, then evidence.
