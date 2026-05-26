from datetime import date, datetime
from zoneinfo import ZoneInfo

from harvester.aggregate import DayEntry, build_entries, extract_tickets
from harvester.commits import Commit

TZ = ZoneInfo("America/Chicago")
PATTERNS = [r"([A-Z][A-Z0-9]+-\d+)", r"#(\d+)"]


def _c(
    day: int, hour: int, msg: str = "wip", branch: str | None = None,
    on_default: bool = False,
) -> Commit:
    """Build a synthetic Commit. Tickets are extracted from `branch` (not `msg`)
    for normal commits; pass `on_default=True` to simulate a commit found on
    the repo's default branch, which also extracts from the message subject.
    """
    return Commit(
        repo="acme/x",
        sha=f"{day:02d}{hour:02d}",
        when_local=datetime(2026, 4, day, hour, 0, tzinfo=TZ),
        message=msg,
        branch=branch,
        on_default_branch=on_default,
    )


def test_extract_tickets_jira_and_gh():
    # extract_tickets itself still works on any text — only build_entries
    # restricts its input to branch names.
    assert extract_tickets("ABC-123: fix; also CON-9 and #42", PATTERNS) == [
        "ABC-123",
        "CON-9",
        "42",
    ]


def test_hours_clamped_and_tickets_deduped():
    commits = [
        _c(6, 9, branch="abc-1-start"),
        _c(6, 9, branch="abc-1-start"),
        _c(6, 17, branch="abc-2-other"),
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
        [_c(6, 10, branch="abc-1-foo")], 2026, 4,
        ticket_patterns=PATTERNS, fill="none",
    )
    assert entries[0].hours == 1.0


def test_empty_when_no_tickets_anywhere():
    entries = build_entries(
        [_c(6, 10), _c(6, 11)], 2026, 4,
        ticket_patterns=PATTERNS, fill="weekdays",
    )
    assert entries == []


def test_message_tickets_are_ignored():
    # A ticket id only in the commit message must NOT contribute for normal
    # (feature-branch) commits — this is the whole point of branch-only
    # extraction. Cross-references and PR descriptions don't pollute.
    entries = build_entries(
        [_c(6, 10, msg="closes ABC-1 and references ABC-2")],
        2026, 4, ticket_patterns=PATTERNS, fill="none",
    )
    assert entries == []


def test_default_branch_commits_extract_from_subject_line():
    # Squash-merge commits land on the default branch — the branch tag has no
    # ticket id but the message subject (the PR title) does. Pick it up.
    commits = [
        _c(
            6, 10,
            msg="ABC-1: add feature (#1234)\n\nAlso touches ABC-99 in passing.",
            branch="main",
            on_default=True,
        ),
    ]
    entries = build_entries(
        commits, 2026, 4, ticket_patterns=PATTERNS, fill="none",
    )
    assert "ABC-1" in entries[0].tickets
    # Body references are NOT extracted (cross-reference protection holds).
    assert "ABC-99" not in entries[0].tickets


def test_subject_wins_over_branch_when_on_default_branch():
    # Squash-merge commit reachable from both `main` and a later feature branch
    # (because the feature branch was created from main after the squash). The
    # branch tag is the feature branch's, but the subject identifies the
    # original ticket. Subject must win — the commit *is* CP-1 work, not CP-2.
    commits = [
        _c(
            6, 10,
            msg="ABC-1: original work\n\nFixes ABC-99 in passing.",
            branch="abc-2-feature",   # feature branch dedup'd the SHA first
            on_default=True,          # but the same SHA also lives on main
        ),
    ]
    entries = build_entries(
        commits, 2026, 4, ticket_patterns=PATTERNS, fill="none",
    )
    assert entries[0].tickets == ["ABC-1"]
    assert "ABC-2" not in entries[0].tickets  # branch tag suppressed
    assert "ABC-99" not in entries[0].tickets  # body still ignored


def test_default_branch_falls_back_to_branch_when_subject_has_no_ticket():
    # Merge commit on `main` whose subject has no ticket id (e.g. a non-squash
    # "Merge pull request" with a generic message). Subject yields nothing,
    # so we fall back to the branch tag.
    commits = [
        _c(
            6, 10,
            msg="merge: random fix",
            branch="abc-2-feature",
            on_default=True,
        ),
    ]
    entries = build_entries(
        commits, 2026, 4, ticket_patterns=PATTERNS, fill="none",
    )
    assert entries[0].tickets == ["ABC-2"]


def test_default_branch_subject_ignored_when_on_default_is_false():
    # Same commit with on_default=False (e.g., direct push tracked off main):
    # subject is ignored, no ticket.
    commits = [
        _c(
            6, 10,
            msg="ABC-1: add feature",
            branch="main",
            on_default=False,
        ),
    ]
    entries = build_entries(
        commits, 2026, 4, ticket_patterns=PATTERNS, fill="none",
    )
    assert entries == []


def test_ticketless_day_inherits_from_prior():
    commits = [
        _c(6, 10, branch="abc-1-morning"),
        _c(6, 16, branch="abc-1-morning"),
        _c(7, 10),                          # Tue: commits but no branch ⇒ no ticket
        _c(7, 14),
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
        _c(6, 9, branch="abc-1-foo"),
        _c(6, 17, branch="abc-1-foo"),
        _c(10, 9, branch="abc-2-bar"),  # Friday with new work
        _c(10, 14, branch="abc-2-bar"),
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
    commits = [
        _c(15, 10, branch="abc-1-foo"),
        _c(15, 14, branch="abc-1-foo"),
    ]
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


def test_transition_day_carries_both_tickets():
    # Three days on ABC-1, then a switch to ABC-2. We work sequentially, so the
    # day of the switch likely touched both tickets — the new-ticket day should
    # include the previous day's tickets too.
    commits = [
        _c(6, 10, branch="abc-1-foo"), _c(6, 17, branch="abc-1-foo"),
        _c(7, 10, branch="abc-1-foo"), _c(7, 16, branch="abc-1-foo"),
        _c(8, 10, branch="abc-2-bar"), _c(8, 17, branch="abc-2-bar"),
        _c(9, 10, branch="abc-2-bar"), _c(9, 17, branch="abc-2-bar"),
    ]
    entries = build_entries(
        commits, 2026, 4, ticket_patterns=PATTERNS, fill="none",
    )
    days = {e.day: e for e in entries}
    assert days[date(2026, 4, 6)].tickets == ["ABC-1"]
    assert days[date(2026, 4, 7)].tickets == ["ABC-1"]
    assert days[date(2026, 4, 8)].tickets == ["ABC-1", "ABC-2"]  # transition
    assert days[date(2026, 4, 9)].tickets == ["ABC-2"]


def test_transition_heuristic_does_not_cascade():
    # A → B → C across three consecutive ticketed days. B should pick up A, C
    # should pick up B — but C should NOT also have A (no two-day cascading).
    commits = [
        _c(6, 10, branch="abc-1-foo"), _c(6, 16, branch="abc-1-foo"),
        _c(7, 10, branch="abc-2-bar"), _c(7, 16, branch="abc-2-bar"),
        _c(8, 10, branch="abc-3-baz"), _c(8, 16, branch="abc-3-baz"),
    ]
    entries = build_entries(
        commits, 2026, 4, ticket_patterns=PATTERNS, fill="none",
    )
    days = {e.day: e for e in entries}
    assert days[date(2026, 4, 6)].tickets == ["ABC-1"]
    assert days[date(2026, 4, 7)].tickets == ["ABC-1", "ABC-2"]
    assert days[date(2026, 4, 8)].tickets == ["ABC-2", "ABC-3"]
    assert "ABC-1" not in days[date(2026, 4, 8)].tickets


def test_transition_does_not_propagate_via_carry_forward():
    # Mon = ABC-1 (real), Tue-Thu carried, Fri = ABC-2 (real).
    # Fri is a transition day and should show both, but Tue-Thu should only
    # carry ABC-1 — the user wasn't on ABC-2 yet.
    commits = [
        _c(6, 10, branch="abc-1-foo"), _c(6, 16, branch="abc-1-foo"),    # Mon
        _c(10, 10, branch="abc-2-bar"), _c(10, 16, branch="abc-2-bar"),  # Fri
    ]
    entries = build_entries(
        commits, 2026, 4, ticket_patterns=PATTERNS, fill="weekdays",
    )
    days = {e.day: e for e in entries}
    assert days[date(2026, 4, 6)].tickets == ["ABC-1"]
    assert days[date(2026, 4, 7)].tickets == ["ABC-1"]   # Tue, carried
    assert days[date(2026, 4, 8)].tickets == ["ABC-1"]   # Wed, carried
    assert days[date(2026, 4, 9)].tickets == ["ABC-1"]   # Thu, carried
    assert days[date(2026, 4, 10)].tickets == ["ABC-1", "ABC-2"]  # Fri, transition
    # Days after Friday should carry forward only ABC-2, not the augmented set.
    assert days[date(2026, 4, 13)].tickets == ["ABC-2"]  # Mon, carried


def test_carry_forward_to_end_of_month():
    # Last tracked work is Fri 2026-04-10; remaining weekdays through 2026-04-30
    # should carry forward (reports go out at EOM, so trailing days inherit).
    commits = [
        _c(10, 9, branch="abc-1-foo"),
        _c(10, 14, branch="abc-1-foo"),
    ]
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
