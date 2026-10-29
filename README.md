# csv-profile-forge

**Paste a CSV. Get a column profile, prioritized fixes, a cleaning plan, and a downloadable cleaned file.**

A local-first data quality workbench for CSV / TSV / semicolon / pipe exports. No API
keys, no uploads, no pandas - the whole analysis engine is Python standard library, and
the front end is vanilla HTML/CSS/JS.

---

## Why it matters

Every team has a spreadsheet problem. A CRM export arrives with the wrong delimiter,
"Shipped" and "shipped" and "SHIPPED" in the same status column, a currency symbol glued
to half the totals, one duplicated order, a row with an extra field, and a discount of
150%. Nobody notices until a pivot table quietly drops rows or a monthly report comes out
wrong.

The usual answers are wrong too:

| Tool | Why it does not fit |
| --- | --- |
| pandas-profiling / ydata-profiling | Heavy dependency tree, needs a Python environment, and dumps a 40-page HTML report instead of a fix list |
| Great Expectations | Built for data pipelines and expectation suites, far too much machinery for "check this file before I use it" |
| csvkit | Great at slicing CSVs, blind to data quality |
| Excel / Google Sheets eyeballing | Does not scale past the first few hundred rows and never tells you which row is wrong |

csv-profile-forge is the thing you paste a file into before you load it somewhere:
it finds the problems, shows you the offending row numbers, tells you how to fix each
one, and hands back a cleaned CSV with only the safe fixes applied.

## What it does

**Column profiles** - inferred type (integer, number, currency, percent, boolean, date,
email, url, uuid, phone, text), missingness split into true blanks versus placeholder
tokens (N/A, none, dash, null), uniqueness, top values, string lengths, numeric
distribution (min / max / mean / median / stdev / IQR fences), date ranges, IQR outliers.

**Findings with evidence** - every issue names the exact rows and values, and carries a
concrete fix:

- duplicate rows, duplicate key values, missing keys
- ragged rows (wrong field count), duplicate or blank headers, non-snake_case headers
- mixed casing (shipped vs SHIPPED), padding whitespace, invisible characters
  (non-breaking spaces, zero-width characters, smart quotes)
- numbers that will not parse: currency symbols, thousands separators, European decimal
  commas, trailing unit letters
- dates in several formats in one column, ambiguous DD/MM vs MM/DD dates, unparseable dates
- percentages outside 0-100, negative quantities or amounts, mixed currency symbols
- cross-field business rules: total does not equal quantity x price, end date before
  start date

**A 5-axis quality score** - completeness, consistency, validity, uniqueness, structure.
Weights shift with the purpose you pick (general / analytics / financial / migration /
customer), and five verdict bands say what to do next: ship_ready, mostly_ready,
needs_polish, tight_but_usable, needs_rework.

**A cleaning plan** - prioritised steps, each marked auto (safe, applied in the export)
or manual (needs a human decision).

**A cleaned CSV download** - BOM removal, invisible-character stripping, whitespace
trimming, placeholder tokens converted to real blanks in typed columns, and exact
duplicate rows dropped. Nothing else is touched.

**History and exports** - every profile is saved to a local SQLite database with notes
and a 1-5 star rating; export any profile as Markdown, JSON, or the cleaned CSV.

## Quick start

~~~
cd csv-profile-forge
./run.sh
# then open http://127.0.0.1:8787
~~~

Or manually:

~~~
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
~~~

Flask is the only dependency. The analysis engine, the sample loader and the report
generator are pure standard library.

## How to use

1. Paste your delimited data into the left panel (or press **Load messy sample**).
2. Set the dataset name, the purpose (this re-weights the score), and optionally the
   key columns and the expected row count.
3. Press **Profile dataset**.
4. Work down the findings list, top severity first. Each card shows the offending rows.
5. Download the **Cleaned CSV** and finish the manual steps from the cleaning plan.

## REST API

| Method | Route | Purpose |
| --- | --- | --- |
| POST | /api/profile | Profile a dataset. Body: csv, dataset_name, purpose, key_columns, expected_rows, delimiter, has_header |
| GET | /api/sample | The built-in messy sample CSV |
| GET | /api/sessions | Saved profiles (summary) |
| POST | /api/sessions | Save a profile. Body: profile, notes, self_rating |
| GET | /api/sessions/<id> | One saved profile, with its full report |
| PATCH | /api/sessions/<id> | Update notes and/or self rating |
| GET | /api/export/<id>?fmt=md or json or csv | Markdown report, JSON report, or cleaned CSV |
| GET | /api/health | Health check |

Example:

~~~
curl -s -X POST http://127.0.0.1:8787/api/profile \
  -H 'Content-Type: application/json' \
  -d '{"csv":"id,total\n1,10\n1,12\n","purpose":"analytics"}' | python3 -m json.tool
~~~

## Tests

~~~
python3 -m unittest discover -s tests -v
~~~

26 tests covering delimiter sniffing, type inference, number and date parsing, every
finding class on the messy sample, scoring bands, the cleaning plan, cleaned-CSV export,
and the full API lifecycle including session save, patch and all three exports.

## Tech stack

- **Backend:** Python 3.9+, Flask, SQLite (standard library sqlite3), custom analysis
  engine in dataq/ (no pandas, no numpy)
- **Frontend:** vanilla HTML, CSS and JavaScript - no build step, no CDN
- **Exports:** Markdown, JSON, cleaned CSV

## Project layout

~~~
app.py                 Flask routes and JSON API
dataq/parser.py        Delimiter sniffing, table parsing, type inference
dataq/profile.py       Column profiles, cross-column checks, scoring, cleaning plan
dataq/scoring.py       Axis weights and verdict bands
dataq/clean.py         Conservative auto-cleaning for the CSV export
dataq/report.py        Markdown and JSON report writers
dataq/db.py            SQLite session log
static/                stylesheet and front-end script
templates/index.html   single page UI
samples/               the deliberately messy sample dataset
tests/                 smoke test suite
~~~

## Domain rotation slot

**data quality / CSV profiling / data hygiene.** Built 2026-10-28.
Next run should pick a different domain - see the workspace build skip list.

## License

MIT
