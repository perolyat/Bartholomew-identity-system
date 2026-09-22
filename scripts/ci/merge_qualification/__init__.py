"""Deterministic merge qualification for this repository (AUDIT-CTRL-1).

A pull request is merge-qualified only when the *exact current head* can be
proven green on every required CI tier and every substantive review finding
against that head has been explicitly dispositioned in committed repository
state. Anything that cannot be established refuses.

The evaluator (`evaluate.evaluate`) is pure and deterministic. Model output
may help a person read a finding or draft a disposition; it is never an input
to the verdict.
"""

from .evaluate import classify_check_run, evaluate, verify_evidence_applies_to
from .model import (
    CheckObservation,
    CheckOutcome,
    DispositionRecord,
    FindingClassification,
    QualificationInput,
    QualificationReport,
    ReasonCode,
    RequiredCheck,
    ReviewFinding,
    Verdict,
)

__all__ = [
    "CheckObservation",
    "CheckOutcome",
    "DispositionRecord",
    "FindingClassification",
    "QualificationInput",
    "QualificationReport",
    "ReasonCode",
    "RequiredCheck",
    "ReviewFinding",
    "Verdict",
    "classify_check_run",
    "evaluate",
    "verify_evidence_applies_to",
]
