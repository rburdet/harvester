from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

from .aggregate import DayEntry


def render(entries: list[DayEntry], month_label: str) -> None:
    console = Console()
    table = Table(title=f"Timesheet preview — {month_label}", show_lines=False)
    table.add_column("Date")
    table.add_column("Day", style="dim")
    table.add_column("Hours", justify="right")
    table.add_column("Source")
    table.add_column("Tickets")
    for e in entries:
        weekday = e.day.strftime("%a")
        source = f"carried from {e.carried_from.isoformat()}" if e.carried_from else f"{e.commit_count} commits"
        table.add_row(
            e.day.isoformat(),
            weekday,
            f"{e.hours:.2f}",
            source,
            ", ".join(e.tickets),
        )
    total = sum(e.hours for e in entries)
    table.add_section()
    table.add_row("", "", f"{total:.2f}", "TOTAL", "")
    console.print(table)


def prompt_confirm(entries: list[DayEntry], assume_yes: bool) -> list[DayEntry]:
    """Return (possibly edited) entries the user confirmed, or raise SystemExit."""
    if assume_yes:
        return entries
    console = Console()
    while True:
        choice = console.input(
            "[bold]Submit to Harvest?[/bold] [y]es / [n]o / [e]dit: "
        ).strip().lower()
        if choice in ("y", "yes"):
            return entries
        if choice in ("n", "no", ""):
            raise SystemExit("aborted by user")
        if choice in ("e", "edit"):
            entries = _edit(entries)
            render(entries, entries[0].day.strftime("%Y-%m") if entries else "")
            continue
        console.print("please answer y, n, or e")


def _edit(entries: list[DayEntry]) -> list[DayEntry]:
    payload = [
        {
            "day": e.day.isoformat(),
            "hours": e.hours,
            "tickets": list(e.tickets),
            "carried_from": e.carried_from.isoformat() if e.carried_from else None,
        }
        for e in entries
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        yaml.safe_dump(payload, fh, sort_keys=False)
        path = fh.name
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, path], check=True)
    with open(path) as fh:
        edited = yaml.safe_load(fh)
    Path(path).unlink(missing_ok=True)
    from datetime import date as _date
    out: list[DayEntry] = []
    for row in edited:
        out.append(
            DayEntry(
                day=_date.fromisoformat(row["day"]),
                hours=float(row["hours"]),
                tickets=list(row["tickets"]),
                carried_from=_date.fromisoformat(row["carried_from"]) if row.get("carried_from") else None,
            )
        )
    return out
