# reports

Where the site's "Report" button sends a join that makes no sense: a Google Sheet, written by a
Google Apps Script web app (`Code.gs`). Anyone can report without an account.

## Set it up

1. Create a Google Sheet, then open **Extensions → Apps Script**.
2. Replace the editor's code with `Code.gs` and save.
3. **Deploy → New deployment**, type **Web app**, *Execute as* **Me**, *Who has access* **Anyone**.
   Authorize it when asked.
4. Copy the web app URL (ending in `/exec`) into `REPORTS_URL` at the top of `web/report.js`.

After changing `Code.gs`, use **Deploy → Manage deployments → Edit → New version** so the URL stays
the same. Until `REPORTS_URL` is set, the dialog opens but sending fails with "reporting is not set up yet".

## The sheet

The first sheet gets a header row, then one row per report:

| Column | Contents |
| --- | --- |
| `received_at` | When the report arrived |
| `pair` | Both column keys, sorted and joined with ` \| `, so the same join reported from either side matches |
| `column_a` | Key (`resource_id::column`) of the column whose page the report was made on |
| `column_b` | Key of the column it joins with |
| `label_a`, `label_b` | `Dataset › column (resource)`, for reading |
| `score` | The join's score when it was reported |
| `reason` | `unrelated`, `coincidental` (values overlap by coincidence), `wrong-entity` or `other` |
| `note` | Optional free text, up to 1,000 characters |

Columns are identified by key, not by `id`: ids are row numbers in `columns.parquet` and change with
every analysis run. The script rejects reports without two different keys or with an unknown reason.
It stores every text value with a leading `'` (hidden by Sheets), so a value is kept as typed and is
never run as a formula or turned into a number or date. The script asks only for access to its own
spreadsheet (`@OnlyCurrentDoc`).

Reports are not shown on the site yet, and the endpoint has no spam protection beyond that validation.
Set `REPORTS_URL` before merging to `main`: the site deploys on push, and without it every report fails.
