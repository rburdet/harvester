# harvester

Auto-fill [Harvest](https://www.getharvest.com/) timesheets from your git
commits, then generate a monthly Clients Report and a monthly Invoice PDF.

Given a month and a list of repos, it:

1. Pulls your commits from GitHub for that month (via the `gh` CLI).
2. Groups them by day, extracts ticket IDs from messages/branch names, and
   estimates hours from the time span between the first and last commit.
3. Carries forward the previous day's tickets onto days with no commits
   (still on weekdays only by default), through the end of the month.
4. Lets you review the table, then posts entries to Harvest and writes a
   spreadsheet + a timesheet PDF + a Clients Report PDF + an Invoice PDF.

## Prerequisites

- Python 3.11+
- The [`gh` CLI](https://cli.github.com/), authenticated:
  ```
  gh auth status
  ```
- A Harvest account with an access token (https://id.getharvest.com/developers).

## Setup

```bash
git clone <this-repo>
cd harvester

python3 -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env                  # then edit (see "Environment" below)
cp config.example.yaml config.yaml    # then edit (see "Configuration" below)
```

### Environment (`.env`)

| Var | What |
|---|---|
| `HARVEST_ACCOUNT_ID` | Your Harvest account ID (the `Harvest-Account-ID` header value from the developer page). |
| `HARVEST_ACCESS_TOKEN` | Personal access token from https://id.getharvest.com/developers. |
| `HARVEST_SUBDOMAIN` | The subdomain of your Harvest URL (e.g. `acme` for `acme.harvestapp.com`). |
| `HARVEST_USER_AGENT` | Free-form, but include a contact email per Harvest's API guidelines. |
| `GIT_AUTHOR_EMAIL` | The email you commit with — used to filter commits to *your* work. |
| `LOCAL_TZ` | IANA timezone for grouping commits by day (e.g. `America/Chicago`). |

### Configuration (`config.yaml`)

The example file is annotated; the highlights:

- **`repos`** — list of `owner/repo` strings you want scanned each month.
- **`harvest.project_id` / `harvest.task_id`** — what to log against. Discover
  them with:
  ```
  harvester list-projects
  ```
- **`branding.account_name`** — optional; sets the top-right wordmark on the
  Clients Report PDF. If omitted, the script reads it from Harvest's
  `/company` endpoint.
- **`hours.min_per_day` / `hours.max_per_day`** — clamp the per-day estimate
  (commit time-span → hours, bounded).
- **`hours.fill_no_commit_days`** — `weekdays`, `all`, or `none`. Controls
  whether days with no commits inherit the previous day's tickets.
- **`tickets.patterns`** — regexes that capture ticket IDs from commit
  messages and branch names. Multi-group patterns are joined with `-`
  (e.g. `(CP)\s+(\d+)` → `CP-12345`).
- **`invoice`** — bill-to address, vendor info, and a flat monthly line item.
  Invoice numbers auto-increment from `latest_invoice_number` anchored at
  `latest_invoice_month`; invoice date is the last day of the target month.

## Usage

```bash
# Discover Harvest IDs
harvester list-projects

# Dry-run for a month: shows the table, writes the invoice PDF, no Harvest writes.
harvester run --month 2026-05 --dry-run

# Generate the local files only (spreadsheet, timesheet PDF, Clients Report
# PDF, invoice PDF) — but don't post to Harvest.
harvester run --month 2026-05 --skip-harvest

# Full run with confirmation prompt.
harvester run --month 2026-05

# Full run, no prompt.
harvester run --month 2026-05 --yes

# Generate just an invoice PDF for a month (uses `invoice:` from config).
harvester invoice --month 2026-05

# Override the auto-computed invoice number.
harvester invoice --month 2026-05 --number 42
```

All generated files land in `./out/` (gitignored):

```
out/timesheet_<YYYY-MM>.{xlsx,csv,pdf}
out/harvest_clients_report_from<from>to<to>.pdf
out/invoice_<number>.pdf
```

## Cleaning up test runs

```
harvester delete-entries --from 2026-05-01 --to 2026-05-31 --project-id <id>
```

Deletes time entries you logged in the range on the given project. Asks for
confirmation; pass `--yes` to skip the prompt.

## Tests

```
pip install -e ".[dev]"
pytest
```

## License

MIT — see [LICENSE](LICENSE).
