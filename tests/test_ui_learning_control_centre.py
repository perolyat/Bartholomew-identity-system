"""
The Learning and Memory Control Centre, as a page.

Structural assertions on the markup and the script, for the reason
`tests/test_ui_test1_defect_regressions.py` documents at length: this
repository has no JavaScript test runner, so the page-side properties are
pinned by reading the source. `tests/test_learning_control_centre_api.py`
covers the behaviour these controls invoke.

What is pinned here is what a person would see, and what they must never see:

  * the view exists, is reachable, and does not displace the ordinary one;
  * the shadow-mode statement is present, is rendered from the server's own
    value rather than a constant in the page, and is not optional;
  * approving and accepting are two separate controls with two separate
    confirmations, and Accept is disabled until an approval applies;
  * a `would_accept` is never rendered without the sentence saying nothing
    happened;
  * every edit and policy save sends the revision it was based on, and a
    conflict is rendered as two versions rather than resolved silently;
  * export is opt-in per record, with no control that means "everything";
  * ordinary controls come before diagnostics.
"""

from __future__ import annotations

import pathlib
import re

import pytest

UI_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "bartholomew_api_bridge_v0_1"
    / "ui"
    / "minimal"
    / "index.html"
)


@pytest.fixture(scope="module")
def ui_source() -> str:
    return UI_PATH.read_text(encoding="utf-8")


def _section(source: str, section_id: str) -> str:
    """Return the markup of one top-level <section id=...> by tag matching."""
    match = re.search(rf'<section id="{re.escape(section_id)}"', source)
    assert match, f"section {section_id} not found"
    depth = 0
    i = match.start()
    for tag in re.finditer(r"</?section\b", source[i:]):
        depth += 1 if not tag.group(0).startswith("</") else -1
        if depth == 0:
            return source[i : i + tag.end()]
    raise AssertionError(f"unbalanced <section> for {section_id}")


@pytest.fixture(scope="module")
def learning_view(ui_source: str) -> str:
    return _section(ui_source, "view-learning")


# ---------------------------------------------------------------------------
# The view exists and is reachable, without displacing the ordinary one
# ---------------------------------------------------------------------------


def test_the_learning_view_exists_and_is_a_tab_panel(ui_source: str):
    assert '<section id="view-learning"' in ui_source
    assert 'id="tab-learning"' in ui_source
    assert 'aria-controls="view-learning"' in ui_source
    assert 'role="tabpanel"' in _section(ui_source, "view-learning")


def test_the_learning_view_is_registered_and_addressable(ui_source: str):
    """It survives a reload and can be linked, like the other two."""
    assert 'const VIEWS = ["home", "learning", "workshop"];' in ui_source
    assert "showView('learning')" in ui_source


def test_the_learning_view_is_hidden_by_default(learning_view: str):
    """An ordinary session still opens on Bartholomew."""
    assert re.search(r'<section id="view-learning"[^>]*\shidden', learning_view)


def test_the_ordinary_view_is_still_the_default(ui_source: str):
    assert 'return VIEWS.includes(h) ? h : "home";' in ui_source
    assert not re.search(r'<section id="view-home"[^>]*\shidden', ui_source)


def test_opening_the_view_rereads_from_the_server(ui_source: str):
    """
    Nothing on this view may survive as stale client state.

    Activation calls the one refresh entry point, so returning to the view
    after acting elsewhere shows what is stored rather than what was rendered
    last time.
    """
    assert 'if(name === "learning" && typeof refreshLearningView === "function")' in ui_source
    assert "async function refreshLearningView()" in ui_source
    for call in (
        "await refreshLearningOverview();",
        "await refreshCandidates();",
        "await refreshCompetencies();",
        "await refreshPolicy();",
        "await refreshEvaluations();",
        "await refreshApprovals();",
    ):
        assert call in ui_source, f"the view refresh must {call}"


# ---------------------------------------------------------------------------
# Shadow mode is stated, and stated truthfully
# ---------------------------------------------------------------------------


def test_the_shadow_mode_statement_is_the_first_thing_on_the_view(learning_view: str):
    """
    A preview screen could most easily imply that Bartholomew is learning on
    his own, so the sentence saying he is not comes before anything else.
    """
    banner = learning_view.index('id="learning-banner"')
    first_card = learning_view.index('class="card"')
    assert banner < first_card, "the mode statement must precede the panels"
    assert "Bartholomew is not accepting lessons on his own" in learning_view
    assert 'id="learning-mode"' in learning_view


def test_the_banner_is_rendered_from_the_server_not_from_a_constant(ui_source: str):
    """
    The page must not be able to claim a mode the backend is not in.

    `renderShadowBanner` reads `shadow.execution_mode` and
    `shadow.automatic_acceptance_enabled` from the API response, and every
    panel that mentions a policy calls it.
    """
    assert "function renderShadowBanner(shadow)" in ui_source
    assert "shadow.execution_mode" in ui_source
    assert "shadow.automatic_acceptance_enabled" in ui_source
    # Never a hard-coded claim about the mode in the script. Matched as a
    # pattern rather than one exact spelling: forbidding a single string with
    # one particular arrangement of spaces and quotes would let the same claim
    # back in written any other way, which is not a guard.
    script = ui_source.split("<script>", 1)[1]
    hardcoded = re.findall(
        r"""(execution_mode|automatic_acceptance_enabled)\s*[:=]\s*['"]?(shadow|true|false)""",
        script,
    )
    assert not hardcoded, f"the page states the mode itself: {hardcoded}"
    assert "renderShadowBanner(j.shadow_mode)" in ui_source


def test_the_policy_form_says_a_configured_auto_mode_changes_nothing(
    learning_view: str,
    ui_source: str,
):
    """
    The one control a user could most reasonably misread.

    Choosing "accept matching lessons automatically" records a preference. The
    form says so immediately under the control, not in a footnote elsewhere on
    the page.
    """
    assert "cannot make him accept" in learning_view
    assert "They change what the preview says" in learning_view

    start = ui_source.index("function renderPolicyForm(p, vocab)")
    end = ui_source.index("function policyNumber(id, fallback)")
    form = ui_source[start:end]
    assert "Accept matching lessons automatically" in form
    assert "Recorded as a preference" in form
    assert "This release cannot act on it" in form
    # The disclaimer sits after the control it qualifies.
    assert form.index("Accept matching lessons automatically") < form.index(
        "Recorded as a preference",
    )


def test_a_would_accept_is_never_rendered_without_its_explanation(ui_source: str):
    """
    The decision block carries the mode sentence itself, rather than relying
    on a banner elsewhere on the page that scrolling could separate from it.
    """
    start = ui_source.index("function renderShadowDecision(evaluation, shadow)")
    end = ui_source.index("async function refreshCompetencies()", start)
    block = ui_source[start:end]
    assert "would have accepted it" in block
    assert "shadow.notice" in block
    assert "shadow_mode_notice" in block


def test_previews_are_listed_as_having_accepted_nothing(learning_view: str, ui_source: str):
    assert "Recorded previews" in learning_view
    assert "None of them accepted anything." in learning_view
    assert "nothing was accepted" in ui_source


# ---------------------------------------------------------------------------
# Approving and accepting are two acts
# ---------------------------------------------------------------------------


def test_approve_and_accept_are_separate_controls(ui_source: str):
    assert 'data-cact="approve"' in ui_source
    assert 'data-cact="accept"' in ui_source
    assert "async function approveCandidate(index)" in ui_source
    assert "async function acceptCandidate(index)" in ui_source


def test_approving_never_accepts(ui_source: str):
    """
    The page must not chain one into the other.

    `approveCandidate` refreshes and stops; it does not call `acceptCandidate`
    and does not post to the accept endpoint.
    """
    start = ui_source.index("async function approveCandidate(index)")
    end = ui_source.index("async function acceptCandidate(index)")
    block = ui_source[start:end]
    assert "acceptCandidate" not in block
    assert "/accept" not in block
    assert "It does not teach it to" in block


def test_a_preview_never_leads_to_an_accept(ui_source: str):
    """A counterfactual is not a prompt to act on it."""
    start = ui_source.index("async function previewCandidate(index)")
    end = ui_source.index("function renderShadowDecision(evaluation, shadow)")
    block = ui_source[start:end]
    assert "acceptCandidate" not in block
    assert "approveCandidate" not in block
    assert "/accept" not in block
    assert "/approve" not in block


def test_accept_is_disabled_until_an_approval_applies(ui_source: str):
    """
    `can_accept_now` comes from the API, which computes it from the same
    fingerprint comparison acceptance itself makes -- so the button's state
    cannot disagree with what the seam would do.
    """
    assert "c.can_accept_now ? '' : ' disabled title=\"Approve this exact lesson first.\"'" in (
        ui_source
    )


def test_the_destructive_and_durable_actions_are_confirmed(ui_source: str):
    """Approving, accepting, rejecting and revoking each ask first."""
    for marker in (
        "Approve this exact lesson?",
        "Teach this to Bartholomew?",
        "Reject this lesson?",
        "Stop Bartholomew recalling this?",
    ):
        assert marker in ui_source, f"missing confirmation: {marker}"
    assert "This is final: it cannot be accepted later." in ui_source


def test_an_approval_that_no_longer_applies_says_so(ui_source: str):
    assert "valid_for_current_revision" in ui_source
    assert "no longer applies" in ui_source
    assert "Editing" in ui_source and "cancels the approval" in ui_source


# ---------------------------------------------------------------------------
# Revisions and conflicts
# ---------------------------------------------------------------------------


def test_every_mutation_sends_the_revision_it_was_based_on(ui_source: str):
    """
    Acceptance requirement 19, from the page's side.

    An edit, an approval and a policy save each name the version they were
    made against, so the server can refuse a stale one rather than merging.
    """
    for start_marker, end_marker in (
        ("async function saveCandidateEdit(index)", "async function approveCandidate(index)"),
        ("async function approveCandidate(index)", "async function acceptCandidate(index)"),
        ("async function savePolicy()", "async function refreshEvaluations()"),
    ):
        block = ui_source[ui_source.index(start_marker) : ui_source.index(end_marker)]
        assert "expected_revision" in block, f"{start_marker} must send expected_revision"


def test_a_conflict_shows_both_versions_and_resolves_nothing(ui_source: str):
    """
    Silent last-write-wins is what this exists to prevent, and so is silently
    discarding the user's work: the stored version is shown beside theirs, and
    what they typed stays in the box.
    """
    start = ui_source.index("function renderCandidateConflict(index, detail)")
    end = ui_source.index("function beginEditCandidate(index)")
    block = ui_source[start:end]
    assert "This lesson changed while you had it open" in block
    assert "Nothing was saved" in block
    assert "What is stored now" in block
    assert "What you were editing" in block
    assert "Your changes are still in the box below" in block
    assert "conflict-pair" in block


def test_a_409_is_handled_wherever_a_revision_is_sent(ui_source: str):
    for function_name in ("saveCandidateEdit", "approveCandidate", "savePolicy"):
        start = ui_source.index(f"async function {function_name}(")
        block = ui_source[start : start + 4000]
        assert "r.status === 409" in block, f"{function_name} must handle a conflict"


def test_an_administrative_change_is_labelled_as_not_a_change_of_meaning(ui_source: str):
    """Acceptance requirement 6, in the words the user reads."""
    assert "Display (does not change its meaning)" in ui_source
    assert "Setting it aside or pinning it is not" in ui_source
    assert "it cancels any approval" in ui_source


# ---------------------------------------------------------------------------
# Provenance, classification and sharing
# ---------------------------------------------------------------------------


def test_a_candidate_shows_what_it_stands_on(ui_source: str):
    """Acceptance requirement 3, on the page."""
    start = ui_source.index("function renderCandidateItem(c, index)")
    end = ui_source.index("function candidateNode(index)")
    block = ui_source[start:end]
    assert "What this stands on" in block
    assert "experience.objective_id" in block
    assert "experience.observations" in block
    assert "provenance" in block
    assert "epistemic_status" in block
    assert "confidence" in block
    assert "privacy_class" in block
    assert "classification" in block


def test_an_unassessed_risk_is_shown_as_unassessed(ui_source: str):
    """
    Not "low". The preview treats an unassessed lesson as the most risky, and
    the page says the same thing rather than showing a blank.
    """
    assert "risk not assessed" in ui_source
    assert "treats an unassessed lesson as the most risky" in ui_source


def test_sharing_is_shown_as_eligibility_never_as_a_completed_share(ui_source: str):
    """
    Session E owns transport and does not exist yet.

    The page reads `transport_available` and says sharing is not connected,
    rather than showing an empty list that reads like "nothing was shared".
    """
    assert "sharing.transport_available" in ui_source
    assert "could be shared" in ui_source
    assert "not shareable" in ui_source


def test_accepted_knowledge_is_distinguished_from_proposals(learning_view: str):
    assert "Lessons he is proposing" in learning_view
    assert "What he has learned" in learning_view
    assert "the only records here he can actually recall" in learning_view


def test_every_required_area_has_a_panel(learning_view: str):
    """
    The contract names the areas one coherent control centre must expose.

    Each gets a heading or a panel of its own, so "it is technically in the
    API" is not mistaken for "a person can see it".
    """
    for heading in (
        "Candidate lessons",
        "Accepted knowledge",
        "Superseded and corrected",
        "Memories and preferences",
        "Recorded previews",
        "Acceptance approvals",
        "If Bartholomew could accept lessons on his own",
    ):
        assert heading in learning_view, f"no panel for: {heading}"


def test_preferences_are_a_named_area_not_a_lump(learning_view: str, ui_source: str):
    """
    "Personal memories" and "preferences" are two required areas.

    They live in the same store -- `personal_facts` writes a preference as a
    `user_profile` row keyed `preference.<slug>` -- so the control that
    separates them is the only thing that makes them two areas rather than one
    list a person has to read through.
    """
    assert 'id="learning-memory-area"' in learning_view
    assert ">Preferences<" in learning_view
    assert ">Other memories<" in learning_view
    assert '"/api/learning/memories?"' in ui_source


def test_retention_is_shown_beside_every_record(ui_source: str):
    """
    "Privacy and retention classifications" is a required area, and retention
    is the half that is easy to leave in the API and never render.
    """
    assert ui_source.count("How it is kept:") >= 3


def test_contradictory_evidence_can_be_supplied(learning_view: str, ui_source: str):
    """
    A required evaluator input that nothing in this release measures.

    If the interface cannot supply it, the policy's contradiction rule is
    unreachable in practice however carefully it is configured.
    """
    assert 'data-cact="contradictions"' in ui_source
    assert "function contradictionCount(index)" in ui_source
    assert "contradicting_evidence_count: contradictionCount(index)" in ui_source


def test_every_material_field_the_help_text_names_is_editable(ui_source: str):
    """
    The edit form's own help text lists what counts as a change of meaning.

    Naming a field there and not offering a control for it tells the user
    something is editable when it is not.
    """
    start = ui_source.index("function beginEditCandidate(index)")
    end = ui_source.index("async function saveCandidateEdit(index)")
    form = ui_source[start:end]
    for control in (
        "cedit-rule-",
        "cedit-cond-",
        "cedit-risk-",
        "cedit-rev-",
        "cedit-apps-",
        "cedit-class-",
        "cedit-conf-",
        "cedit-share-",
        "cedit-display-",
    ):
        assert control in form, f"the edit form has no control for {control}"


def test_the_policy_form_cannot_silently_erase_excluded_categories(ui_source: str):
    """
    The defect this pins: the form rendered no control for
    `excluded_categories` and then hard-coded `excluded_categories: []` into
    every save, so opening the policy screen and pressing Save deleted whatever
    the user had excluded.
    """
    assert 'id="policy-excluded-categories"' in ui_source
    assert "data-excat=" in ui_source
    assert "excluded_categories: excludedCategories" in ui_source
    assert "excluded_categories: []" not in ui_source


def test_the_policy_form_exposes_every_required_dimension(ui_source: str):
    """All twelve dimensions the contract names, each with a control."""
    start = ui_source.index("function renderPolicyForm(p, vocab)")
    end = ui_source.index("function policyNumber(id, fallback)")
    form = ui_source[start:end]
    for control in (
        "policy-categories",
        "policy-excluded-categories",
        "policy-max-risk",
        "policy-reversible",
        "policy-min-exp",
        "policy-min-conf",
        "policy-contradiction",
        "policy-max-caps",
        "policy-max-apps",
        "policy-privacy",
        "policy-classifications",
        "policy-sharing",
        "policy-expiry",
        "policy-review",
    ):
        assert control in form, f"the policy form has no control for {control}"


def test_superseded_versions_are_shown_as_unrecallable(learning_view: str, ui_source: str):
    """
    "Superseded or corrected knowledge" is a required area, and the one thing
    it must not imply is that Bartholomew still uses any of it.
    """
    assert "What he used to think" in learning_view
    assert "None of it can be recalled" in learning_view
    assert "he cannot recall this" in ui_source
    assert "async function refreshSuperseded()" in ui_source


def test_a_record_that_cannot_be_exported_says_so_before_it_is_ticked(ui_source: str):
    """
    Better than a refusal after the fact: the box is disabled and the reason is
    on the row.
    """
    start = ui_source.index("async function refreshMemoryPicker()")
    end = ui_source.index("function toggleLearningSelection(kind, key, on)")
    block = ui_source[start:end]
    assert "m.exportable" in block
    assert "Not exportable:" in block
    assert "disabled" in block


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_is_opt_in_per_record_with_no_everything_control(learning_view: str, ui_source: str):
    """
    Acceptance requirement: no bulk export by default.

    The button exports the ticked selection, and refuses to do anything when
    nothing is ticked -- rather than defaulting to the whole store.
    """
    assert "Only the records you tick are exported" in learning_view
    assert 'There is no "export everything"' in learning_view
    assert "Export selected" in learning_view

    start = ui_source.index("async function exportSelectedLearning()")
    end = ui_source.index("// --- the policy form ---")
    block = ui_source[start:end]
    assert "learningSelection.size === 0" in block
    assert "There is no export-everything button" in block
    assert "Array.from(learningSelection.values())" in block


def test_the_export_reports_what_it_left_out(ui_source: str):
    """An export that quietly dropped a ticked record would misrepresent
    itself as complete."""
    start = ui_source.index("async function exportSelectedLearning()")
    end = ui_source.index("// --- the policy form ---")
    block = ui_source[start:end]
    assert "payload.skipped" in block
    assert "Left out:" in block


# ---------------------------------------------------------------------------
# Ordinary controls before diagnostics
# ---------------------------------------------------------------------------


def test_diagnostics_are_available_but_secondary(learning_view: str, ui_source: str):
    """
    Fingerprints, matched rule ids and policy revisions are real and useful,
    and they sit inside a collapsed <details> under the plain-language
    explanation rather than in front of it.
    """
    start = ui_source.index("function renderShadowDecision(evaluation, shadow)")
    end = ui_source.index("async function refreshCompetencies()", start)
    block = ui_source[start:end]
    reasons_at = block.index("evaluation.reasons")
    details_at = block.index("<details")
    assert reasons_at < details_at, "the plain-language reasons must come first"
    assert "candidate_fingerprint" in block
    assert "rules matched" in block


def test_the_page_never_interpolates_a_record_key_into_a_handler(ui_source: str):
    """
    The rule `renderMemoryItem()` documents: identifiers go into data
    attributes and listeners are bound with addEventListener, so no stored
    value can influence what code runs.
    """
    for render_fn, next_fn in (
        ("function renderCandidateItem(c, index)", "function candidateNode(index)"),
        ("function renderCompetencyItem(k, index)", "function competencyNode(index)"),
    ):
        block = ui_source[ui_source.index(render_fn) : ui_source.index(next_fn)]
        assert "onclick=" not in block, f"{render_fn} must not build inline handlers"
        assert "data-cindex" in block or "data-kindex" in block

    assert "function bindCandidateActions()" in ui_source
    assert "function bindCompetencyActions()" in ui_source


def test_every_new_control_is_labelled_for_a_screen_reader(learning_view: str):
    for label in (
        'aria-label="Search candidate lessons"',
        'aria-label="Filter candidate lessons by review state"',
        'aria-label="Search memories and preferences"',
        'aria-label="Which memories to show"',
        'aria-label="Search accepted knowledge"',
        'role="status"',
    ):
        assert label in learning_view, f"missing accessibility attribute: {label}"
