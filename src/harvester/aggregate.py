from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

from .commits import Commit


@dataclass
class DayEntry:
    day: date
    hours: float
    tickets: list[str]
    carried_from: date | None = None  # set if this day was filled by carry-forward
    commit_count: int = 0

    @property
    def notes(self) -> str:
        bullets = "\n".join(f"- {t}" for t in self.tickets) if self.tickets else ""
        if self.carried_from:
            header = f"(continued from {self.carried_from.isoformat()})"
            return f"{header}\n{bullets}".strip()
        return bullets


def extract_tickets(text: str, patterns: list[str]) -> list[str]:
    found: list[str] = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            if m.groups():
                # Multi-group patterns join with `-` so `Cp 12345` -> `CP-12345`.
                tok = "-".join(g for g in m.groups() if g is not None)
            else:
                tok = m.group(0)
            tok = tok.upper()
            if tok not in found:
                found.append(tok)
    return found


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _days_in_month(year: int, month: int) -> list[date]:
    first = date(year, month, 1)
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    out: list[date] = []
    d = first
    while d < nxt:
        out.append(d)
        d += timedelta(days=1)
    return out


def build_entries(
    commits: Iterable[Commit],
    year: int,
    month: int,
    *,
    ticket_patterns: list[str],
    min_hours: float = 1.0,
    max_hours: float = 8.0,
    fill: str = "weekdays",  # weekdays | all | none
) -> list[DayEntry]:
    """Group commits by local date, build per-day entries.

    Only days whose commits reference a real ticket (per `ticket_patterns`) become
    "ticketed days". Other days (no commits, or commits without a ticket) inherit
    tickets+hours from the nearest ticketed day (preferring backward in time;
    backfilled from the earliest ticketed day at the month's start). Days after
    the last tracked day in the month also carry forward from it — reports are
    sent at EOM, so the final unworked days inherit the last tracked activity.
    If no day in the month has a real ticket, returns []."""
    by_day: dict[date, list[Commit]] = defaultdict(list)
    for c in commits:
        by_day[c.when_local.date()].append(c)

    ticketed: dict[date, DayEntry] = {}
    commits_only: dict[date, int] = {}
    for day, day_commits in by_day.items():
        commits_only[day] = len(day_commits)
        times = sorted(c.when_local for c in day_commits)
        span_h = (times[-1] - times[0]).total_seconds() / 3600.0
        hours = _clamp(span_h, min_hours, max_hours)
        tickets: list[str] = []
        for c in day_commits:
            blob = c.message + ("\n" + c.branch if c.branch else "")
            for t in extract_tickets(blob, ticket_patterns):
                if t not in tickets:
                    tickets.append(t)
        if tickets:
            ticketed[day] = DayEntry(
                day=day, hours=hours, tickets=tickets, commit_count=len(day_commits)
            )

    if not ticketed:
        return []

    if fill == "none":
        return [ticketed[d] for d in sorted(ticketed)]

    earliest = min(ticketed)
    earliest_entry = ticketed[earliest]
    sorted_ticketed_days = sorted(ticketed)
    result: list[DayEntry] = []

    for d in _days_in_month(year, month):
        if fill == "weekdays" and d.weekday() >= 5:
            continue
        if d in ticketed:
            result.append(ticketed[d])
            continue
        # Find the most recent ticketed day <= d; if none, backfill from earliest.
        prior = [k for k in sorted_ticketed_days if k < d]
        src = ticketed[prior[-1]] if prior else earliest_entry
        result.append(
            DayEntry(
                day=d,
                hours=src.hours,
                tickets=list(src.tickets),
                carried_from=src.day,
                commit_count=commits_only.get(d, 0),
            )
        )
    return result
