"""The console renders the executive's task payload in W03-B's own vocabulary.

`_render_task` is the one place a task becomes something a person reads, and the
step states it keys on are W03-B's (`executive.plan.StepStatus`), not words this
module made up. These tests feed it payloads shaped exactly like
`routes/operator.py::_result_dict` produces and pin: the hint points only at steps
a person can act on (`awaiting_authorization`); a question is rendered as a
question and nothing else; a named stop is printed, not dropped; and the
executive's explanation is carried verbatim rather than paraphrased upward.

PR Fast tier: no server, no executive package needed.
"""

from __future__ import annotations

from bartholomew.cli_operator import _render_task


def _payload(**overrides):
    base = {
        "task": {"task_id": "exec-1", "instruction": "Open Spotify", "status": "in_progress"},
        "outcome": "proposed",
        "steps": [
            {
                "ordinal": 0,
                "capability": "windows.launch_app",
                "status": "awaiting_authorization",
                "action_id": "act-1",
            },
        ],
        "explanation": "Nothing runs without your approval.",
        "named_stops": [],
    }
    base.update(overrides)
    return base


def test_the_hint_points_at_awaiting_authorization_steps_only():
    text = _render_task(_payload())
    assert "Inspect and decide" in text
    assert "bartholomew operator actions show act-1" in text


def test_a_dispatched_or_verified_step_gets_no_approval_hint():
    for status in ("planned", "dispatched", "verified", "failed", "unknown", "blocked"):
        text = _render_task(
            _payload(
                steps=[
                    {
                        "ordinal": 0,
                        "capability": "windows.launch_app",
                        "status": status,
                        "action_id": "act-1",
                    },
                ],
            ),
        )
        assert "Inspect and decide" not in text, status


def test_a_question_is_rendered_as_a_question_and_nothing_else():
    text = _render_task(_payload(question="Which 'it' did you mean?", steps=[]))
    assert "Bartholomew needs to know: Which 'it' did you mean?" in text
    assert "Inspect and decide" not in text
    assert "step" not in text


def test_a_named_stop_is_printed_not_dropped():
    text = _render_task(
        _payload(named_stops=["windows.move_file: not a capability this build ships"]),
    )
    assert "STOP: windows.move_file: not a capability this build ships" in text


def test_the_executive_s_explanation_is_carried_verbatim():
    text = _render_task(_payload(explanation="It was issued and nobody can say whether it worked."))
    assert "It was issued and nobody can say whether it worked." in text
    assert "confirmed" not in text.lower()


def test_a_task_without_an_instruction_never_prints_understood_as_none():
    text = _render_task({"task": {"task_id": "exec-9", "status": "refused"}, "steps": []})
    assert "Understood as" not in text
    assert "Status: refused" in text
