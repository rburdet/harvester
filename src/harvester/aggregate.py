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
        # Just the ticket bullets — no "(continued from …)" header. The
        # carry-forward provenance is visible locally (review table, PDF) but
        # we don't want it leaking into the Harvest entry's notes field.
        return "\n".join(f"- {t}" for t in self.tickets) if self.tickets else ""


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


def _apply_transition_heuristic(
    ticketed: dict[date, "DayEntry"],
) -> dict[date, "DayEntry"]:
    """When a ticketed day introduces a ticket the previous ticketed day didn't
    have, treat it as a transition: prepend the previous day's original tickets,
    on the assumption that the switch happened mid-day (you finished one ticket
    and started the next on the same day, but only the new one shows in commits).

    Uses a snapshot of original tickets to avoid cascading across multiple
    transitions (a third-day switch shouldn't drag in tickets from two days ago).
    """
    days = sorted(ticketed)
    if len(days) < 2:
        return dict(ticketed)
    original = {d: list(ticketed[d].tickets) for d in days}
    out: dict[date, DayEntry] = {days[0]: ticketed[days[0]]}
    for i in range(1, len(days)):
        d = days[i]
        prev_tickets = original[days[i - 1]]
        cur_tickets = original[d]
        introduces_new = any(t not in prev_tickets for t in cur_tickets)
        if not introduces_new:
            out[d] = ticketed[d]
            continue
        merged: list[str] = list(prev_tickets)
        for t in cur_tickets:
            if t not in merged:
                merged.append(t)
        cur = ticketed[d]
        out[d] = DayEntry(
            day=cur.day, hours=cur.hours, tickets=merged,
            carried_from=cur.carried_from, commit_count=cur.commit_count,
        )
    return out


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
            # Per-commit ticket source precedence:
            #   1. If the commit is on the default branch (squash-merge or
            #      direct push), prefer the message *subject line* — it carries
            #      the original PR title with the right ticket id, even when
            #      this same SHA happens to also live on a later feature branch
            #      whose name says something different.
            #   2. Otherwise fall back to the branch name.
            # We never read the commit body — that's where cross-references
            # hide ("fixes CP-x, related to CP-y") and they'd pollute the day.
            found: list[str] = []
            if c.on_default_branch and c.message:
                subject = c.message.split("\n", 1)[0]
                found = extract_tickets(subject, ticket_patterns)
            if not found and c.branch:
                found = extract_tickets(c.branch, ticket_patterns)
            for t in found:
                if t not in tickets:
                    tickets.append(t)
        if tickets:
            ticketed[day] = DayEntry(
                day=day, hours=hours, tickets=tickets, commit_count=len(day_commits)
            )

    if not ticketed:
        return []

    # Augmented ticketed dict (transition days carry both old + new tickets).
    # We use this only for the visible row on actual ticketed days. Carry-forward
    # still reads `ticketed` (the originals) so the merged tickets don't
    # propagate to days *after* the switch.
    ticketed_aug = _apply_transition_heuristic(ticketed)

    if fill == "none":
        return [ticketed_aug[d] for d in sorted(ticketed_aug)]

    earliest = min(ticketed)
    earliest_entry = ticketed[earliest]
    sorted_ticketed_days = sorted(ticketed)
    result: list[DayEntry] = []

    for d in _days_in_month(year, month):
        if fill == "weekdays" and d.weekday() >= 5:
            continue
        if d in ticketed:
            result.append(ticketed_aug[d])
            continue
        # Find the most recent ticketed day <= d; if none, backfill from earliest.
        # Use original (non-augmented) tickets so transitions don't bleed forward.
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
