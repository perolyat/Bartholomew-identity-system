"""Reading forge state for one pull request.

Everything here is I/O and normalisation; no decision is taken. When a read
fails, the collector says so by clearing `check_state_determined` or
`review_state_determined` on the `QualificationInput` — it never returns an
empty list and lets the evaluator read that as "nothing wrong". That
distinction is the whole reason the flags exist.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

from .model import CheckObservation, QualificationInput, ReviewFinding

_API = "https://api.github.com"
_TIMEOUT = 30

_REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          isOutdated
          comments(first: 1) {
            nodes {
              databaseId
              body
              url
              author { login }
              originalCommit { oid }
              commit { oid }
            }
          }
        }
      }
    }
  }
}
"""


class CollectionError(RuntimeError):
    """A forge read failed. The caller must fail closed, not carry on."""


@dataclass
class _Session:
    token: str

    def rest(self, path: str, params: dict | None = None) -> object:
        response = requests.get(
            f"{_API}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params=params,
            timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            raise CollectionError(f"GET {path} returned {response.status_code}")
        return response.json()

    def rest_paged(self, path: str, key: str | None = None) -> list[dict]:
        collected: list[dict] = []
        page = 1
        while page <= 20:
            payload = self.rest(path, {"per_page": 100, "page": page})
            batch = payload if key is None else payload.get(key, [])  # type: ignore[union-attr]
            if not isinstance(batch, list):
                raise CollectionError(f"GET {path} did not return a list")
            collected.extend(batch)
            if len(batch) < 100:
                return collected
            page += 1
        raise CollectionError(f"GET {path} did not terminate within 20 pages")

    def graphql(self, query: str, variables: dict) -> dict:
        response = requests.post(
            f"{_API}/graphql",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"query": query, "variables": variables},
            timeout=_TIMEOUT,
        )
        if response.status_code != 200:
            raise CollectionError(f"GraphQL returned {response.status_code}")
        payload = response.json()
        if payload.get("errors"):
            raise CollectionError(f"GraphQL errors: {payload['errors']}")
        return payload.get("data") or {}


def _collect_checks(session: _Session, repo: str, head_sha: str) -> list[CheckObservation]:
    """Every Actions job that ran against this exact head.

    The Actions API is used rather than the check-runs API because a check
    run reports only a job name, and this repository has three workflows that
    each publish a job called `smoke` and a job called `Quality (format,
    lint, packaging contract)`. Requiring "smoke" without saying which tier
    it belongs to is exactly the ambiguity that let PR #101 read as green.
    """
    runs = session.rest(f"/repos/{repo}/actions/runs", {"head_sha": head_sha, "per_page": 100})
    observations: list[CheckObservation] = []
    for run in runs.get("workflow_runs", []):  # type: ignore[union-attr]
        workflow_name = run.get("name") or ""
        run_head = run.get("head_sha")
        jobs = session.rest_paged(f"/repos/{repo}/actions/runs/{run['id']}/jobs", "jobs")
        for job in jobs:
            observations.append(
                CheckObservation(
                    workflow=workflow_name,
                    job=job.get("name") or "",
                    head_sha=run_head,
                    status=job.get("status"),
                    conclusion=job.get("conclusion"),
                    url=job.get("html_url"),
                    run_number=run.get("run_number"),
                    run_attempt=run.get("run_attempt"),
                ),
            )
    return observations


def findings_from_rest_comments(comments: list[dict]) -> list[ReviewFinding]:
    """Normalise REST review comments into findings, fail-closed.

    Used when the GraphQL review-thread API is unreachable. REST does not
    report whether a thread was resolved on the forge, so every finding it
    produces carries `thread_resolved=False`.

    That is deliberately the conservative reading, and it cannot weaken a
    verdict: this repository does not accept forge resolution as a
    disposition in any case, so the only thing `False` changes is *which*
    blocking classification a finding gets — `unresolved_substantive` for one
    against the current head instead of `unknown`. Both refuse. The fallback
    can therefore make the gate usable without GraphQL, and cannot make it
    permissive.

    Replies are folded into their thread root: a thread is one finding, and
    dispositioning it disposes of the conversation, not of one message.
    """
    findings: list[ReviewFinding] = []
    for comment in comments:
        if comment.get("in_reply_to_id"):
            continue
        body = (comment.get("body") or "").strip()
        findings.append(
            ReviewFinding(
                finding_id=f"review_comment:{comment.get('id')}",
                author=((comment.get("user") or {}).get("login")) or "unknown",
                commit_sha=comment.get("original_commit_id") or comment.get("commit_id"),
                thread_resolved=False,
                excerpt=body.splitlines()[0][:200] if body else "",
                url=comment.get("html_url"),
            ),
        )
    return findings


def _collect_review_threads(
    session: _Session,
    owner: str,
    name: str,
    pr_number: int,
) -> list[ReviewFinding]:
    """Review threads with their forge resolution state, via GraphQL.

    Falls back to REST when GraphQL is unreachable — some tokens and some
    network paths have REST but not GraphQL, and blanket-refusing there would
    make the gate unusable rather than strict.
    """
    findings: list[ReviewFinding] = []
    cursor = None
    for _ in range(20):
        data = session.graphql(
            _REVIEW_THREADS_QUERY,
            {"owner": owner, "name": name, "number": pr_number, "cursor": cursor},
        )
        threads = data.get("repository", {}).get("pullRequest", {}).get("reviewThreads") or {}
        for thread in threads.get("nodes") or []:
            comments = (thread.get("comments") or {}).get("nodes") or []
            if not comments:
                continue
            first = comments[0]
            original_commit = (first.get("originalCommit") or {}).get("oid")
            current_commit = (first.get("commit") or {}).get("oid")
            body = first.get("body") or ""
            findings.append(
                ReviewFinding(
                    finding_id=f"review_comment:{first.get('databaseId')}",
                    author=((first.get("author") or {}).get("login")) or "unknown",
                    commit_sha=original_commit or current_commit,
                    thread_resolved=bool(thread.get("isResolved")),
                    excerpt=body.strip().splitlines()[0][:200] if body.strip() else "",
                    url=first.get("url"),
                ),
            )
        page_info = threads.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
    else:  # pragma: no cover - defensive
        raise CollectionError("review threads did not terminate within 20 pages")

    return findings


def _collect_findings(
    session: _Session,
    owner: str,
    name: str,
    pr_number: int,
) -> list[ReviewFinding]:
    try:
        findings = _collect_review_threads(session, owner, name, pr_number)
    except CollectionError:
        findings = findings_from_rest_comments(
            session.rest_paged(f"/repos/{owner}/{name}/pulls/{pr_number}/comments"),
        )

    for review in session.rest_paged(f"/repos/{owner}/{name}/pulls/{pr_number}/reviews"):
        body = (review.get("body") or "").strip()
        if not body:
            continue
        if review.get("state") == "APPROVED" and len(body) < 40:
            # A bare approval note carries no finding to disposition.
            continue
        findings.append(
            ReviewFinding(
                finding_id=f"review:{review.get('id')}",
                author=((review.get("user") or {}).get("login")) or "unknown",
                commit_sha=review.get("commit_id"),
                # A review body is not a thread and has no resolution state of
                # its own. `False` here means "no forge resolution", which is
                # the truth; it must be dispositioned explicitly.
                thread_resolved=False,
                excerpt=body.splitlines()[0][:200],
                url=review.get("html_url"),
            ),
        )
    return findings


def collect(
    repo: str,
    pr_number: int,
    *,
    token: str | None = None,
) -> QualificationInput:
    """Gather everything the evaluator needs for one pull request."""
    token = token or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        return QualificationInput(
            repo=repo,
            pr_number=pr_number,
            head_sha=None,
            check_state_determined=False,
            review_state_determined=False,
        )
    owner, _, name = repo.partition("/")
    session = _Session(token)

    try:
        pull = session.rest(f"/repos/{repo}/pulls/{pr_number}")
        head_sha = ((pull.get("head") or {}).get("sha")) or None  # type: ignore[union-attr]
        commits = [
            commit.get("sha")
            for commit in session.rest_paged(f"/repos/{repo}/pulls/{pr_number}/commits")
        ]
    except CollectionError:
        return QualificationInput(
            repo=repo,
            pr_number=pr_number,
            head_sha=None,
            check_state_determined=False,
            review_state_determined=False,
        )

    checks: list[CheckObservation] = []
    check_state_determined = True
    try:
        checks = _collect_checks(session, repo, head_sha or "")
    except CollectionError:
        check_state_determined = False

    findings: list[ReviewFinding] = []
    review_state_determined = True
    try:
        findings = _collect_findings(session, owner, name, pr_number)
    except CollectionError:
        review_state_determined = False

    return QualificationInput(
        repo=repo,
        pr_number=pr_number,
        head_sha=head_sha,
        pr_commits=tuple(sha for sha in commits if isinstance(sha, str)),
        checks=tuple(checks),
        findings=tuple(findings),
        check_state_determined=check_state_determined,
        review_state_determined=review_state_determined,
    )
