# harvester

Auto-fill Harvest timesheets from your git commits.

## Setup

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env       # fill in your Harvest token
cp config.example.yaml config.yaml   # list your repos
```

You also need the `gh` CLI installed and authenticated (`gh auth status`).

## Usage

```
# Discover IDs
harvester list-projects
harvester list-tasks <project_id>
harvester inspect-form <google_form_url>

# Dry-run for a month (no writes)
harvester run --month 2026-04 --dry-run

# Generate only the spreadsheet
harvester run --month 2026-04 --skip-harvest --skip-form

# Full run with confirmation prompt
harvester run --month 2026-04

# Full run, no prompt
harvester run --month 2026-04 --yes
```
