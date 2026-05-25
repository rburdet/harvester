from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Commit:
    repo: str
    sha: str
    when_local: datetime
    message: str
    branch: str | None


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


def _author_login(email_or_login: str) -> str:
    """Resolve an email to a GitHub login if needed; pass logins through."""
    if "@" not in email_or_login:
        return email_or_login
    try:
        r = subprocess.run(
            ["gh", "api", f"search/users?q={email_or_login}+in:email"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        data = json.loads(r.stdout or "{}")
        items = data.get("items") or []
        if items:
            return items[0]["login"]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return email_or_login


def fetch_month(
    repos: list[str],
    author: str,
    year: int,
    month: int,
    tz: ZoneInfo,
    *,
    fetch_branches: bool = True,  # kept for API compatibility; PR path always has the branch
) -> list[Commit]:
    start_local = datetime(year, month, 1, tzinfo=tz)
    end_local = (
        datetime(year + 1, 1, 1, tzinfo=tz)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=tz)
    )
    since_iso = start_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    until_iso = end_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    since_date = start_local.date().isoformat()

    login = _author_login(author)
    seen: dict[str, Commit] = {}

    for repo in repos:
        # 1) Commits on the default branch (catches direct pushes / merged history).
        for item in _fetch_default_branch_commits(repo, author, since_iso, until_iso):
            commit = _normalize(repo, item, tz, branch=None)
            if commit and start_local <= commit.when_local < end_local:
                seen.setdefault(commit.sha, commit)

        # 2) Commits on PRs you authored that were touched in the window.
        for pr in _fetch_pulls_authored_in_window(repo, login, since_date):
            head_ref = (pr.get("head") or {}).get("ref")
            pr_number = pr["number"]
            for item in _fetch_pull_commits(repo, pr_number):
                commit = _normalize(repo, item, tz, branch=head_ref)
                if not commit:
                    continue
                if commit.when_local < start_local or commit.when_local >= end_local:
                    continue
                if not _is_authored_by(item, author, login):
                    continue
                # Prefer the PR-derived record (it has branch info) over a default-branch one.
                prev = seen.get(commit.sha)
                if prev is None or prev.branch is None:
                    seen[commit.sha] = commit

    commits = sorted(seen.values(), key=lambda c: c.when_local)
    return commits


def _fetch_default_branch_commits(
    repo: str, author: str, since_iso: str, until_iso: str
) -> list[dict]:
    path = (
        f"repos/{repo}/commits"
        f"?author={author}&since={since_iso}&until={until_iso}&per_page=100"
    )
    try:
        return _gh_api(path)
    except subprocess.CalledProcessError as exc:
        print(f"[warn] {repo}: default-branch commits failed: {(exc.stderr or '').strip()}")
        return []


def _fetch_pulls_authored_in_window(repo: str, login: str, since_date: str) -> list[dict]:
    owner, name = repo.split("/", 1)
    q = f"is:pr+repo:{owner}/{name}+author:{login}+updated:>={since_date}"
    path = f"search/issues?q={q}&per_page=100"
    try:
        result = subprocess.run(
            ["gh", "api", "--paginate", path],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"[warn] {repo}: PR search failed: {(exc.stderr or '').strip()}")
        return []
    out = result.stdout.strip()
    if not out:
        return []
    # search responses are objects with `items`, not arrays — paginate joins them.
    # Easier path: parse each page separately.
    pulls: list[dict] = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(out):
        while idx < len(out) and out[idx].isspace():
            idx += 1
        if idx >= len(out):
            break
        obj, end = decoder.raw_decode(out, idx)
        for item in obj.get("items", []):
            pull_url = (item.get("pull_request") or {}).get("url")
            if not pull_url:
                continue
            try:
                pr_json = subprocess.run(
                    ["gh", "api", pull_url.replace("https://api.github.com/", "")],
                    check=True, capture_output=True, text=True, timeout=10,
                )
                pulls.append(json.loads(pr_json.stdout))
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue
        idx = end
    return pulls


def _fetch_pull_commits(repo: str, pr_number: int) -> list[dict]:
    path = f"repos/{repo}/pulls/{pr_number}/commits?per_page=100"
    try:
        return _gh_api(path)
    except subprocess.CalledProcessError as exc:
        print(f"[warn] {repo}#{pr_number}: commit fetch failed: {(exc.stderr or '').strip()}")
        return []


def _normalize(repo: str, item: dict, tz: ZoneInfo, *, branch: str | None) -> Commit | None:
    try:
        sha = item["sha"]
        author_date = item["commit"]["author"]["date"]
        when = datetime.fromisoformat(author_date.replace("Z", "+00:00")).astimezone(tz)
        message = item["commit"]["message"]
    except (KeyError, ValueError):
        return None
    return Commit(repo=repo, sha=sha, when_local=when, message=message, branch=branch)


def _is_authored_by(item: dict, email: str, login: str) -> bool:
    commit_author = item.get("commit", {}).get("author", {}) or {}
    if commit_author.get("email", "").lower() == email.lower():
        return True
    gh_author = item.get("author") or {}
    if (gh_author.get("login") or "").lower() == login.lower():
        return True
    return False
