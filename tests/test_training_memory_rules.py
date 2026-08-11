"""
S5.2 Step 1 (governance foundation) regression tests.

Covers the two things Step 1 adds, per
`docs/S5_2_TRAINING_KNOWLEDGE_ACQUISITION_DESIGN.md` Sec.7.2 and Sec.13.1:

1. `memory_rules.yaml` carries explicit entries for S5.1's five competency
   kinds, so competency records no longer rely on an unset/default
   `recall_policy` and are legible to anyone auditing that file directly.
2. Those entries **cannot weaken consent**. This is the safety-relevant
   property: `MemoryRulesEngine.PRIORITY` runs `ask_before_store` before
   `always_keep` and merges metadata without clobbering fields a
   higher-priority rule already set, so competency content matching a
   consent rule still requires consent.

Also asserts the "training" brake scope is registered everywhere it has to
be for the control to actually be operable (Sec.10) -- the kernel enforces
scopes as free strings, but the API allowlist rejects unknown ones, so an
unregistered scope would be enforceable yet impossible to engage.
"""

from bartholomew.kernel.memory_rules import MemoryRulesEngine

COMPETENCY_KINDS = [
    "competency",
    "competency_knowledge",
    "competency_procedure",
    "competency_heuristic",
    "competency_evidence",
]


# ---------------------------------------------------------------------------
# 1. Governance legibility: the five kinds are explicitly governed.
# ---------------------------------------------------------------------------
def test_every_competency_kind_has_explicit_recall_policy():
    engine = MemoryRulesEngine(watch_file=False)

    for kind in COMPETENCY_KINDS:
        result = engine.evaluate(
            {"kind": kind, "key": "estate_management", "value": "ordinary content"},
        )
        assert result.get("recall_policy") == "always", (
            f"{kind} has no explicit recall_policy -- it is relying on an unset default, "
            "which is exactly what these rules exist to prevent"
        )
        assert (
            result.get("privacy_class") == "user.competency"
        ), f"{kind} has no explicit privacy_class"
        assert "always_keep" in result["matched_categories"]


def test_competency_kinds_are_storable_and_consent_free_when_content_is_ordinary():
    """The baseline case: an ordinary procedure is stored without consent."""
    engine = MemoryRulesEngine(watch_file=False)

    for kind in COMPETENCY_KINDS:
        memory = {
            "kind": kind,
            "key": "estate_management.quote_comparison",
            "value": "Get at least three quotes and compare scope, price and warranty.",
        }
        assert engine.should_store(memory) is True
        assert engine.requires_consent(memory) is False


# ---------------------------------------------------------------------------
# 2. The safety property: always_keep cannot override ask_before_store.
# ---------------------------------------------------------------------------
def test_ask_before_store_outranks_competency_always_keep_rules():
    """
    A competency record whose CONTENT trips a consent rule must still
    require consent. If this ever fails, the Sec.7.2 rules have become a
    consent bypass and must be removed, not patched around.
    """
    engine = MemoryRulesEngine(watch_file=False)

    for kind in COMPETENCY_KINDS:
        memory = {
            "kind": kind,
            "key": "estate_management.utility_account",
            "value": "The electricity account number is 12345678 and the password is hunter2.",
        }
        result = engine.evaluate(memory)

        assert result["requires_consent"] is True, (
            f"{kind}: competency always_keep rule suppressed a consent requirement -- "
            "this is a consent bypass"
        )
        assert engine.requires_consent(memory) is True
        # The higher-priority rule's own protections must also survive.
        assert result.get("redact") is True
        assert "ask_before_store" in result["matched_categories"]


def test_priority_order_places_ask_before_store_above_always_keep():
    """Guards the ordering the test above depends on, so a reordering of
    PRIORITY fails here with a clear reason rather than silently weakening
    the property."""
    priority = MemoryRulesEngine.PRIORITY
    assert priority.index("never_store") < priority.index("always_keep")
    assert priority.index("ask_before_store") < priority.index("always_keep")


# ---------------------------------------------------------------------------
# 3. The "training" brake scope is registered everywhere it must be.
# ---------------------------------------------------------------------------
def test_training_scope_accepted_by_governance_api_allowlist():
    from bartholomew_api_bridge_v0_1.services.api.routes.governance import VALID_SCOPES

    assert "training" in VALID_SCOPES, (
        "an unregistered scope is enforceable in the kernel but cannot be engaged "
        "through the API or UI -- a governance control that exists but cannot be operated"
    )


def test_training_scope_offered_by_cli_and_ui():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]

    cli_source = (repo_root / "bartholomew" / "cli.py").read_text(encoding="utf-8")
    assert "training" in cli_source, "brake CLI help text does not mention the training scope"

    ui_source = (
        repo_root / "bartholomew_api_bridge_v0_1" / "ui" / "minimal" / "index.html"
    ).read_text(encoding="utf-8")
    assert (
        'class="brake-scope" value="training"' in ui_source
    ), "the minimal UI's brake scope checkboxes are hardcoded and do not offer training"
