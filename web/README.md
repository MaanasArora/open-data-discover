# web

A static site for browsing the analysis: pick a column and see the columns in other
datasets that share its values, best first. Plain HTML and ES modules; no build step.

## Run it

```bash
cd analyze && uv run analyze.py run -r ../web/data   # or copy an existing results/ folder to web/data/
cd ../web && python -m http.server 8000              # then open http://localhost:8000
```

The page loads `data/manifest.json`, `data/columns.parquet` and `data/joins.parquet`, so it must be
served over HTTP (browsers block module scripts and `fetch` on `file://`). Any static host works,
e.g. GitHub Pages; commit `data/` if the host serves from the repository.

## Pages

Type to find a dataset (the box suggests titles), then pick one of its columns, to see the columns in
other datasets that share its values. Only joins scoring at least 0.3 are shown (`MIN_SCORE` in
`data.js`), and the picker lists only datasets and columns with such a join to another dataset.
A dataset's columns are grouped by resource (file).

The URL hash holds the chosen column (`#column=<key>`), so every column page can be linked and
bookmarked. Joins are listed in score order, 25 at a time, as `Dataset › COLUMN` with the resource
underneath and a few example values; hovering a score shows the containment in both directions.
Joins within the same dataset (often the same data in another map projection) are hidden by default,
with a checkbox to include them.

Each join has a small red Report button in its top-right corner. It opens a dialog below it that asks what is
wrong with it (plus an optional note) and sends the report to a Google Sheet; see `../reports/README.md`.

## Files

| File | Contents |
| --- | --- |
| `index.html`, `style.css` | Page shell and styles (light and dark) |
| `app.js` | The picker, routing, page state and events |
| `data.js` | Loading the results; per-column join lists; datasets for the picker |
| `views.js` | HTML for each view (all data is escaped) |
| `report.js` | The report dialog; sending reports to `REPORTS_URL` |
| `vendor/hyparquet.js` | [hyparquet](https://github.com/hyparam/hyparquet) 1.31.2 (MIT), bundled into one file |

The site does no scoring: joins arrive sorted from `analyze`, and each column's list keeps that order.

To update hyparquet: `npm install hyparquet` in a scratch folder, then
`npx esbuild node_modules/hyparquet/src/index.js --bundle --format=esm --minify --outfile=vendor/hyparquet.js`.
