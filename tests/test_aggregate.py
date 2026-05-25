from datetime import date, datetime
from zoneinfo import ZoneInfo

from harvester.aggregate import DayEntry, build_entries, extract_tickets
from harvester.commits import Commit

TZ = ZoneInfo("America/Chicago")
PATTERNS = [r"([A-Z][A-Z0-9]+-\d+)", r"#(\d+)"]


def _c(day: int, hour: int, msg: str, branch: str | None = None) -> Commit:
    return Commit(
        repo="acme/x",
        sha=f"{day:02d}{hour:02d}",
        when_local=datetime(2026, 4, day, hour, 0, tzinfo=TZ),
        message=msg,
        branch=branch,
    )


def test_extract_tickets_jira_and_gh():
    assert extract_tickets("ABC-123: fix; also CON-9 and #42", PATTERNS) == [
        "ABC-123",
        "CON-9",
        "42",
    ]


def test_hours_clamped_and_tickets_deduped():
    commits = [
        _c(6, 9, "ABC-1 start"),
        _c(6, 9, "ABC-1 more"),
        _c(6, 17, "ABC-2 end"),
    ]
    entries = build_entries(
        commits, 2026, 4,
        ticket_patterns=PATTERNS, fill="none",
    )
    assert len(entries) == 1
    e = entries[0]
    assert e.day == date(2026, 4, 6)
    assert e.hours == 8.0  # span 8h, max 8
    assert e.tickets == ["ABC-1", "ABC-2"]
    assert "- ABC-1" in e.notes
    assert "- ABC-2" in e.notes


def test_single_commit_floored_to_min_hours():
    entries = build_entries(
        [_c(6, 10, "ABC-1")], 2026, 4,
        ticket_patterns=PATTERNS, fill="none",
    )
    assert entries[0].hours == 1.0


def test_empty_when_no_tickets_anywhere():
    entries = build_entries(
        [_c(6, 10, "wip"), _c(6, 11, "more wip")], 2026, 4,
        ticket_patterns=PATTERNS, fill="weekdays",
    )
    assert entries == []


def test_ticketless_day_inherits_from_prior():
    commits = [
        _c(6, 10, "ABC-1 morning"),
        _c(6, 16, "ABC-1 wrap"),
        _c(7, 10, "Fixing typo"),       # Tue: commits but no ticket
        _c(7, 14, "more fixes"),
    ]
    entries = build_entries(
        commits, 2026, 4,
        ticket_patterns=PATTERNS, fill="weekdays",
    )
    days = {e.day: e for e in entries}
    assert days[date(2026, 4, 7)].tickets == ["ABC-1"]
    assert days[date(2026, 4, 7)].carried_from == date(2026, 4, 6)
    assert days[date(2026, 4, 7)].commit_count == 2  # real commits, just no ticket


def test_carry_forward_weekdays_only():
    # Monday 2026-04-06 has a commit; Tue-Thu have none; Sat-Sun skipped.
    commits = [
        _c(6, 9, "ABC-1"),
        _c(6, 17, "ABC-1"),
        _c(10, 9, "ABC-2"),  # Friday with new work
        _c(10, 14, "ABC-2"),
    ]
    entries = build_entries(
        commits, 2026, 4,
        ticket_patterns=PATTERNS, fill="weekdays",
    )
    days = {e.day: e for e in entries}
    assert date(2026, 4, 6) in days  # Mon, real
    assert date(2026, 4, 7) in days  # Tue, carried
    assert date(2026, 4, 8) in days  # Wed, carried
    assert date(2026, 4, 9) in days  # Thu, carried
    assert date(2026, 4, 10) in days  # Fri, real (new tickets)
    assert date(2026, 4, 11) not in days  # Sat, skipped
    assert date(2026, 4, 12) not in days  # Sun, skipped
    assert date(2026, 4, 13) in days  # Mon, carried from Fri
    assert days[date(2026, 4, 7)].carried_from == date(2026, 4, 6)
    assert days[date(2026, 4, 7)].tickets == ["ABC-1"]
    assert days[date(2026, 4, 13)].carried_from == date(2026, 4, 10)
    assert days[date(2026, 4, 13)].tickets == ["ABC-2"]


def test_backfill_weekdays_before_first_commit():
    # First commit is Wed 2026-04-15. Earlier weekdays inherit from it; weekends skip.
    commits = [_c(15, 10, "ABC-1"), _c(15, 14, "ABC-1")]
    entries = build_entries(
        commits, 2026, 4,
        ticket_patterns=PATTERNS, fill="weekdays",
    )
    days = {e.day: e for e in entries}
    assert date(2026, 4, 1) in days  # Wed, backfilled
    assert date(2026, 4, 4) not in days  # Sat
    assert date(2026, 4, 5) not in days  # Sun
    assert date(2026, 4, 14) in days  # Tue, backfilled
    assert date(2026, 4, 15) in days  # Wed, real
    assert days[date(2026, 4, 1)].carried_from == date(2026, 4, 15)
    assert days[date(2026, 4, 1)].tickets == ["ABC-1"]


def test_carry_forward_to_end_of_month():
    # Last tracked work is Fri 2026-04-10; remaining weekdays through 2026-04-30
    # should carry forward (reports go out at EOM, so trailing days inherit).
    commits = [_c(10, 9, "ABC-1"), _c(10, 14, "ABC-1")]
    entries = build_entries(
        commits, 2026, 4,
        ticket_patterns=PATTERNS, fill="weekdays",
    )
    days = {e.day: e for e in entries}
    # Last weekday of April 2026 is Thu the 30th.
    assert date(2026, 4, 30) in days
    assert days[date(2026, 4, 30)].carried_from == date(2026, 4, 10)
    assert days[date(2026, 4, 30)].tickets == ["ABC-1"]
    # Weekends still skipped.
    assert date(2026, 4, 25) not in days  # Sat
    assert date(2026, 4, 26) not in days  # Sun
