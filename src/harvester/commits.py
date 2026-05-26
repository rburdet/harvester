from __future__ import annotations

import json
import subprocess
import urllib.parse
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from rich.console import Console


@dataclass(frozen=True)
class Commit:
    repo: str
    sha: str
    when_local: datetime
    message: str
    branch: str | None
    # True when this commit was found by scanning the repo's default branch
    # (typically a squash-merge from a deleted feature branch or a direct push
    # to `main`). Aggregation uses this to widen ticket extraction for these
    # commits — see aggregate.build_entries.
    on_default_branch: bool = False


def _gh_api(path: str) -> list[dict]:
    result = subprocess.run(
        ["gh", "api", "--paginate", path],
        check=True,
        capture_output=True,
        text=True,
    )
    out = result.stdout.strip()
    if not out:
        return []
    # --paginate concatenates arrays with `][` between pages.
    return json.loads(out.replace("][", ","))


def _default_branch(repo: str) -> str:
    """Return the repo's default branch name (e.g. 'main'); fall back to 'main'."""
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        return json.loads(result.stdout).get("default_branch") or "main"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "main"


def _resolve_login(author: str) -> str | None:
    """If `author` looks like an email, resolve it to a GitHub login via the
    user-search API. Returns None when the lookup fails or `author` is already
    a login. Best-effort; we still match on email separately."""
    if "@" not in author:
        return None
    try:
        r = subprocess.run(
            ["gh", "api", f"search/users?q={author}+in:email"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        data = json.loads(r.stdout or "{}")
        items = data.get("items") or []
        if items:
            return items[0].get("login")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return None


def _user_pr_branches(
    repo: str, login: str | None, since_date: str
) -> list[tuple[str, int]]:
    """List (head_ref_name, pr_number) for same-repo PRs you authored that
    were updated on or after `since_date` (YYYY-MM-DD). Returns [] when we
    couldn't resolve a login. PRs from forks are skipped — we can only fetch
    commits in `repo`.

    The PR number is kept so we can fall back to the PR-commits endpoint when
    the head ref no longer exists (auto-deleted after merge).
    """
    if not login:
        return []
    owner, name = repo.split("/", 1)
    q = f"repo:{owner}/{name} is:pr author:{login} updated:>={since_date}"

    pairs: list[tuple[str, int]] = []
    cursor: str | None = None
    while True:
        after_clause = f', after: "{cursor}"' if cursor else ""
        query = f"""
        query {{
          search(query: "{q}", type: ISSUE, first: 100{after_clause}) {{
            pageInfo {{ hasNextPage endCursor }}
            nodes {{
              ... on PullRequest {{
                number
                headRefName
                headRepository {{ nameWithOwner }}
              }}
            }}
          }}
        }}
        """
        try:
            result = subprocess.run(
                ["gh", "api", "graphql", "-f", f"query={query}"],
                check=True, capture_output=True, text=True, timeout=30,
            )
        except subprocess.CalledProcessError as exc:
            print(f"[warn] {repo}: PR search failed: {(exc.stderr or '').strip()}")
            return pairs
        data = json.loads(result.stdout or "{}")
        search = (data.get("data") or {}).get("search") or {}
        for node in search.get("nodes") or []:
            head_repo = (node.get("headRepository") or {}).get("nameWithOwner", "")
            if head_repo != repo:
                continue  # fork — can't fetch from this repo
            ref = node.get("headRefName")
            num = node.get("number")
            if ref and num is not None and not any(p[0] == ref for p in pairs):
                pairs.append((ref, num))
        page = search.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return pairs


def _fetch_pull_commits(
    repo: str, pr_number: int, author: str, login: str | None,
) -> list[dict]:
    """Fetch commits in a PR (preserved by GitHub even after head-branch
    deletion). The endpoint has no `?author=` filter, so we match client-side
    against the user's email *or* login.
    """
    candidates = {author.lower()}
    if login:
        candidates.add(login.lower())
    path = f"repos/{repo}/pulls/{pr_number}/commits?per_page=100"
    try:
        items = _gh_api(path)
    except subprocess.CalledProcessError as exc:
        print(f"[warn] {repo}#{pr_number}: PR commits fetch failed: {(exc.stderr or '').strip()}")
        return []
    out: list[dict] = []
    for item in items:
        commit_author = (item.get("commit") or {}).get("author") or {}
        email = (commit_author.get("email") or "").lower()
        gh_author = item.get("author") or {}
        gh_login = (gh_author.get("login") or "").lower() if gh_author else ""
        if email in candidates or gh_login in candidates:
            out.append(item)
    return out


def _user_branches(
    repo: str, author: str, login: str | None, default_branch: str
) -> list[str]:
    """List branches in `repo` you authored — i.e., where the HEAD commit's
    author matches `author` (email) or `login` (GitHub user). Excludes the
    default branch.

    Uses a single GraphQL query (paginated 100/page) to fetch each branch's
    head-commit author in one shot, avoiding one REST call per branch.
    """
    owner, name = repo.split("/", 1)
    candidates = {author.lower()}
    if login:
        candidates.add(login.lower())

    found: list[str] = []
    cursor: str | None = None
    while True:
        after_clause = f', after: "{cursor}"' if cursor else ""
        query = f"""
        query {{
          repository(owner: "{owner}", name: "{name}") {{
            refs(refPrefix: "refs/heads/", first: 100{after_clause}) {{
              pageInfo {{ hasNextPage endCursor }}
              nodes {{
                name
                target {{
                  ... on Commit {{ author {{ email user {{ login }} }} }}
                }}
              }}
            }}
          }}
        }}
        """
        try:
            result = subprocess.run(
                ["gh", "api", "graphql", "-f", f"query={query}"],
                check=True, capture_output=True, text=True, timeout=30,
            )
        except subprocess.CalledProcessError as exc:
            print(f"[warn] {repo}: GraphQL branch query failed: {(exc.stderr or '').strip()}")
            return []
        data = json.loads(result.stdout or "{}")
        refs = ((data.get("data") or {}).get("repository") or {}).get("refs") or {}
        for node in refs.get("nodes") or []:
            branch_name = node.get("name")
            if not branch_name or branch_name == default_branch:
                continue
            target = node.get("target") or {}
            author_info = target.get("author") or {}
            email = (author_info.get("email") or "").lower()
            node_login = ((author_info.get("user") or {}).get("login") or "").lower()
            if email in candidates or node_login in candidates:
                found.append(branch_name)
        page = refs.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return found


def _fetch_commits_on_branch(
    repo: str, branch: str, author: str, since_iso: str, until_iso: str
) -> list[dict]:
    sha = urllib.parse.quote(branch, safe="")
    path = (
        f"repos/{repo}/commits"
        f"?sha={sha}&author={author}&since={since_iso}&until={until_iso}&per_page=100"
    )
    try:
        return _gh_api(path)
    except subprocess.CalledProcessError as exc:
        print(f"[warn] {repo}@{branch}: commits failed: {(exc.stderr or '').strip()}")
        return []


def fetch_month(
    repos: list[str],
    author: str,
    year: int,
    month: int,
    tz: ZoneInfo,
    *,
    console: "Console | None" = None,
) -> list[Commit]:
    """Fetch your commits in [year-month] across `repos`, tagged with the branch
    they came from.

    For each repo we scan three sources and dedup by SHA:
      1. Branches *you authored* (head commit by you), via a single GraphQL
         query — the cheap, ticket-rich path: your feature branches carry the
         ticket id in their names.
      2. Head refs of PRs *you opened* this month — catches branches where
         someone else (reviewer/bot) made the most recent push so your work
         is no longer the branch HEAD.
      3. The default branch — picks up work that landed directly on `main`
         or via squash-merge of a branch that has since been deleted.

    Sources are scanned in that order, so for shared SHAs the feature-branch
    name "wins" the dedup (the ticket lives there, not on `main`).

    Pass `console` (a `rich.console.Console`) to surface per-branch progress.
    """
    start_local = datetime(year, month, 1, tzinfo=tz)
    end_local = (
        datetime(year + 1, 1, 1, tzinfo=tz)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=tz)
    )
    since_iso = start_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    until_iso = end_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

    login = _resolve_login(author)
    if console is not None and login:
        console.print(f"  [dim]resolved {author} → @{login}[/]")

    seen: dict[str, Commit] = {}

    since_date = start_local.date().isoformat()

    for repo in repos:
        if console is not None:
            console.print(f"  [dim]{repo}: discovering branches you authored…[/]")
        default = _default_branch(repo)
        user_branches = _user_branches(repo, author, login, default)
        pr_pairs = _user_pr_branches(repo, login, since_date)
        # PR head refs that aren't already in user_branches (and aren't the
        # default itself) — typically branches where a reviewer made the last
        # push, so the head-author filter skipped them.
        pr_extras = [
            (ref, num) for (ref, num) in pr_pairs
            if ref not in user_branches and ref != default
        ]
        # Scan order: PR-discovered refs first (they're the most authoritative
        # signal — you opened a PR off this branch, so it's the original owner
        # of the commits). The PR-commits fallback kicks in for refs that have
        # been auto-deleted after merge, recovering the original SHAs and
        # tagging them with the original branch name (CP-xxxx → ticket CP-xxxx).
        # Then your other authored branches (they may have inherited those
        # SHAs via a merge; PR-first dedup keeps the original tag). Default
        # last as a catch-all (subject-line extraction handles squash merges).
        scan: list[tuple[str, bool, int | None]] = (
            [(ref, False, num) for (ref, num) in pr_extras]
            + [(b, False, None) for b in user_branches]
            + [(default, True, None)]
        )
        if console is not None:
            bits = [f"{len(user_branches)} authored"]
            if pr_extras:
                bits.append(f"+{len(pr_extras)} via PRs")
            console.print(
                f"  [dim]{repo}: {', '.join(bits)} + [/dim]"
                f"[cyan]{default}[/cyan][dim]; scanning…[/dim]"
            )

        repo_before = len(seen)
        status_ctx = (
            console.status(f"  [dim]{repo}…[/]") if console is not None else nullcontext()
        )
        with status_ctx as status:
            for i, (branch_name, is_default, pr_number) in enumerate(scan, 1):
                if status is not None:
                    status.update(
                        f"  [dim]{repo}:[/] [{i}/{len(scan)}] [cyan]{branch_name}[/]"
                    )
                before = len(seen)
                items = _fetch_commits_on_branch(
                    repo, branch_name, author, since_iso, until_iso
                )
                via_pr_fallback = False
                if not items and pr_number is not None:
                    # Branch likely deleted post-merge — recover commits from
                    # the PR's preserved history. Tag them with the original
                    # head ref so branch-name ticket extraction still works.
                    items = _fetch_pull_commits(repo, pr_number, author, login)
                    via_pr_fallback = bool(items)
                for item in items:
                    commit = _normalize(
                        repo, item, tz, branch=branch_name, on_default=is_default,
                    )
                    if not commit:
                        continue
                    if not (start_local <= commit.when_local < end_local):
                        continue
                    existing = seen.get(commit.sha)
                    if existing is None:
                        seen[commit.sha] = commit
                    elif is_default and not existing.on_default_branch:
                        # Same SHA reachable from a feature branch (added
                        # earlier) AND from the default branch. Flag it so
                        # aggregate.py prefers the subject-line ticket (the
                        # original PR title) over the feature-branch tag, while
                        # keeping the feature-branch tag intact for context.
                        seen[commit.sha] = replace(existing, on_default_branch=True)
                added = len(seen) - before
                if added and console is not None:
                    suffix = " [yellow](via deleted PR branch)[/]" if via_pr_fallback else ""
                    console.print(
                        f"    [green]+{added}[/] from [cyan]{branch_name}[/]{suffix}"
                    )

        if console is not None:
            console.print(
                f"  [dim]{repo}: {len(seen) - repo_before} commit(s) collected[/]"
            )

    return sorted(seen.values(), key=lambda c: c.when_local)


def _normalize(
    repo: str, item: dict, tz: ZoneInfo, *,
    branch: str | None, on_default: bool = False,
) -> Commit | None:
    try:
        sha = item["sha"]
        author_date = item["commit"]["author"]["date"]
        when = datetime.fromisoformat(author_date.replace("Z", "+00:00")).astimezone(tz)
        message = item["commit"]["message"]
    except (KeyError, ValueError):
        return None
    return Commit(
        repo=repo, sha=sha, when_local=when, message=message,
        branch=branch, on_default_branch=on_default,
    )
