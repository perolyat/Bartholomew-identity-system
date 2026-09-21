"""Command line for the merge-qualification gate.

Two verbs, and the difference between them is the point.

* `qualify` reads the forge now, decides, and writes head-bound evidence.
* `verify` takes evidence written earlier and refuses it if the head has
  moved. A report generated for commit A never authorises commit B.

Both exit non-zero unless the answer is an unqualified yes. A crash exits
non-zero too: there is no path through this program on which "the gate broke"
reads as "the gate passed".
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .collect import collect
from .config import (
    CONFIG_PATH,
    DISPOSITIONS_PATH,
    ControlStateError,
    load_config,
    load_dispositions,
)
from .evaluate import evaluate, verify_evidence_applies_to
from .model import QualificationInput, QualificationReport, Verdict

EXIT_READY = 0
EXIT_NOT_READY = 1
EXIT_CONTROL_STATE_BROKEN = 2


def render(report: QualificationReport) -> str:
    lines = [
        f"Merge qualification — {report.repo} PR #{report.pr_number}",
        f"  head:    {report.head_sha}",
        f"  verdict: {report.verdict.value.upper()}",
    ]
    if report.checks:
        lines.append("  required checks:")
        for check in report.checks:
            lines.append(f"    [{check.outcome.value:>10}] {check.required.key} — {check.detail}")
    if report.findings:
        lines.append("  review findings:")
        for finding in report.findings:
            marker = "BLOCKS" if finding.blocking else "  ok  "
            lines.append(
                f"    [{marker}] {finding.finding.finding_id} "
                f"({finding.classification.value}) — {finding.detail}",
            )
    if report.reasons:
        lines.append("  refused because:")
        for reason in report.reasons:
            lines.append(f"    - {reason.code.value}: {reason.detail}")
    return "\n".join(lines)


def _qualify(args: argparse.Namespace) -> int:
    try:
        config = load_config(Path(args.config))
        dispositions = load_dispositions(Path(args.dispositions))
    except ControlStateError as error:
        print(f"merge qualification control state is unusable: {error}", file=sys.stderr)
        print("failing closed: NOT READY", file=sys.stderr)
        return EXIT_CONTROL_STATE_BROKEN

    collected = collect(args.repo, args.pr, token=args.token)
    qualification_input = QualificationInput(
        repo=collected.repo,
        pr_number=collected.pr_number,
        head_sha=collected.head_sha,
        pr_commits=collected.pr_commits,
        required_checks=config.required_checks,
        checks=collected.checks,
        findings=collected.findings,
        dispositions=dispositions,
        check_state_determined=collected.check_state_determined,
        review_state_determined=collected.review_state_determined,
    )
    report = evaluate(
        qualification_input,
        github_resolution_satisfies_disposition=(config.github_resolution_satisfies_disposition),
    )
    print(render(report))
    if args.out:
        Path(args.out).write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return EXIT_READY if report.verdict is Verdict.READY else EXIT_NOT_READY


def _verify(args: argparse.Namespace) -> int:
    try:
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"qualification evidence is unreadable: {error}", file=sys.stderr)
        return EXIT_CONTROL_STATE_BROKEN
    applies, reason = verify_evidence_applies_to(evidence, args.head)
    if not applies:
        assert reason is not None
        print(f"{reason.code.value}: {reason.detail}", file=sys.stderr)
        return EXIT_NOT_READY
    if evidence.get("verdict") != Verdict.READY.value:
        print(
            f"evidence for {args.head[:12]} records verdict "
            f"{evidence.get('verdict')!r}, not ready",
            file=sys.stderr,
        )
        return EXIT_NOT_READY
    print(f"qualification evidence is valid and READY for {args.head}")
    return EXIT_READY


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.ci.merge_qualification")
    subparsers = parser.add_subparsers(dest="command", required=True)

    qualify = subparsers.add_parser("qualify", help="qualify a pull request head")
    qualify.add_argument("--repo", required=True, help="owner/name")
    qualify.add_argument("--pr", required=True, type=int)
    qualify.add_argument("--config", default=str(CONFIG_PATH))
    qualify.add_argument("--dispositions", default=str(DISPOSITIONS_PATH))
    qualify.add_argument("--token", default=None)
    qualify.add_argument("--out", default=None, help="write the evidence JSON here")
    qualify.set_defaults(handler=_qualify)

    verify = subparsers.add_parser(
        "verify",
        help="check that evidence still applies to the current head",
    )
    verify.add_argument("--evidence", required=True)
    verify.add_argument("--head", required=True)
    verify.set_defaults(handler=_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
