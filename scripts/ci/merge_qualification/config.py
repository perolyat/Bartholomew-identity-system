"""Loading the committed control state: required tiers and dispositions.

Both files live in the repository and therefore arrive through a reviewed
commit. That is the point: the set of tiers that must be green, and the
decision that a particular finding is no longer blocking, are repository
state under review, not assertions made in a report or a chat message.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .model import DispositionRecord, FindingClassification, RequiredCheck

CONFIG_PATH = Path(".github/merge-qualification/config.yml")
DISPOSITIONS_PATH = Path(".github/merge-qualification/dispositions.yml")


class ControlStateError(RuntimeError):
    """The committed control state is unusable. Callers must fail closed."""


@dataclass(frozen=True)
class QualificationConfig:
    required_checks: tuple[RequiredCheck, ...]
    github_resolution_satisfies_disposition: bool = False


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise ControlStateError(f"{path} does not exist")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:  # pragma: no cover - defensive
        raise ControlStateError(f"{path} is not readable YAML: {error}") from error
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ControlStateError(f"{path} must contain a mapping")
    return loaded


def load_config(path: Path = CONFIG_PATH) -> QualificationConfig:
    document = _load_yaml(path)
    tiers = document.get("required_tiers")
    if not isinstance(tiers, list) or not tiers:
        raise ControlStateError(f"{path} declares no required_tiers")

    required: list[RequiredCheck] = []
    for tier in tiers:
        if not isinstance(tier, dict):
            raise ControlStateError(f"{path}: each required tier must be a mapping")
        workflow = tier.get("workflow")
        jobs = tier.get("jobs")
        if not isinstance(workflow, str) or not workflow.strip():
            raise ControlStateError(f"{path}: a required tier is missing 'workflow'")
        if not isinstance(jobs, list) or not jobs:
            raise ControlStateError(f"{path}: tier {workflow!r} lists no jobs")
        for job in jobs:
            if not isinstance(job, str) or not job.strip():
                raise ControlStateError(f"{path}: tier {workflow!r} has an unusable job name")
            required.append(RequiredCheck(workflow=workflow.strip(), job=job.strip()))

    accept_forge = document.get("github_resolution_satisfies_disposition", False)
    if not isinstance(accept_forge, bool):
        raise ControlStateError(
            f"{path}: github_resolution_satisfies_disposition must be true or false",
        )
    return QualificationConfig(tuple(required), accept_forge)


def load_dispositions(path: Path = DISPOSITIONS_PATH) -> tuple[DispositionRecord, ...]:
    """Read the committed disposition records.

    A missing file means "nothing has been dispositioned", which is a
    perfectly ordinary state and blocks nothing on its own — every
    undispositioned finding already blocks by itself. A *malformed* file is
    different: it raises, and the caller fails closed.
    """
    if not path.exists():
        return ()
    document = _load_yaml(path)
    entries = document.get("dispositions") or []
    if not isinstance(entries, list):
        raise ControlStateError(f"{path}: 'dispositions' must be a list")

    records: list[DispositionRecord] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ControlStateError(f"{path}: each disposition must be a mapping")
        finding_id = entry.get("finding_id")
        classification = entry.get("classification")
        rationale = entry.get("rationale")
        recorded_by = entry.get("recorded_by")
        if not isinstance(finding_id, str) or not finding_id.strip():
            raise ControlStateError(f"{path}: a disposition is missing 'finding_id'")
        if finding_id in seen:
            raise ControlStateError(
                f"{path}: {finding_id} is dispositioned more than once; the intended "
                "decision is ambiguous",
            )
        seen.add(finding_id)
        try:
            parsed = FindingClassification(classification)
        except ValueError as error:
            raise ControlStateError(
                f"{path}: {finding_id} has an unknown classification {classification!r}",
            ) from error
        if parsed is FindingClassification.UNKNOWN:
            raise ControlStateError(
                f"{path}: {finding_id} is dispositioned as 'unknown', which is not a "
                "decision; remove it or classify it truthfully",
            )
        if not isinstance(rationale, str) or not rationale.strip():
            raise ControlStateError(f"{path}: {finding_id} carries no rationale")
        if not isinstance(recorded_by, str) or not recorded_by.strip():
            raise ControlStateError(f"{path}: {finding_id} names no recorded_by")
        resolved_by_commit = entry.get("resolved_by_commit")
        if resolved_by_commit is not None and not isinstance(resolved_by_commit, str):
            raise ControlStateError(f"{path}: {finding_id} has an unusable resolved_by_commit")
        records.append(
            DispositionRecord(
                finding_id=finding_id.strip(),
                classification=parsed,
                rationale=rationale.strip(),
                recorded_by=recorded_by.strip(),
                resolved_by_commit=(resolved_by_commit or None),
            ),
        )
    return tuple(records)
