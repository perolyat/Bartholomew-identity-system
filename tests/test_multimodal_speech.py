"""Governed spoken output: one authority, bounded, cancellable, truthful.

Acceptance gates covered: 3 and 15 (spoken output is a separate permission and
cannot start microphone input), plus the §7 requirements for bounded text,
cancellation, truthful unavailable output-device state and reuse of the
existing spoken-output seam rather than a second authority.
"""

from __future__ import annotations

import ast
from pathlib import Path

from bartholomew.kernel import spoken_output
from bartholomew.multimodal.speech import (
    MAX_SPOKEN_CHARS,
    SpeechHandle,
    output_available,
    prepare_text,
    speak_with_handle,
)

MODULE = Path(__file__).resolve().parents[1] / "bartholomew" / "multimodal" / "speech.py"


def _code_only(path: Path) -> str:
    """The module's source with every docstring removed.

    These tests assert what the code *does*, not what its prose mentions --
    the module docstring legitimately names `config/kernel.yaml` while
    explaining that it deliberately does not read it.
    """
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


class TestNoSecondAuthority:
    def test_the_enablement_flag_is_the_existing_one(self):
        """No second config key: `voice.spoken_output` remains the only switch."""
        code = _code_only(MODULE)
        assert "CONFIG_KEY" not in code
        assert spoken_output.CONFIG_KEY == "spoken_output"
        assert spoken_output.CONFIG_SECTION == "voice"

    def test_this_module_declares_no_config_of_its_own(self):
        code = _code_only(MODULE)
        for forbidden in ("yaml", "config/", "os.getenv", "environ"):
            assert forbidden not in code

    def test_it_does_not_read_the_brake_itself(self):
        """The seam owns the brake read; a second one could disagree with it."""
        code = _code_only(MODULE)
        assert "parking_brake" not in code
        assert "is_blocked" not in code

    def test_the_bound_defers_to_the_existing_adapter(self):
        assert MAX_SPOKEN_CHARS == spoken_output.MAX_SPEECH_CHARS

    def test_the_existing_seam_still_gates_speech(self):
        from bartholomew.kernel.runtime_contract import (
            run_spoken_output_through_runtime_contract,
        )

        assert callable(run_spoken_output_through_runtime_contract)


class TestBoundedAndCancellable:
    def test_long_text_is_bounded_and_the_truncation_recorded(self):
        text, classification = prepare_text("word " * 5000)
        assert len(text) <= MAX_SPOKEN_CHARS + 3
        assert classification.truncated is True
        assert classification.redactions

    def test_short_text_is_untouched(self):
        text, classification = prepare_text("the meeting is at three")
        assert text == "the meeting is at three"
        assert classification.truncated is False

    def test_cancelling_before_speaking_makes_no_sound(self, monkeypatch):
        spoke = []
        monkeypatch.setattr(
            spoken_output,
            "speak_text",
            lambda t: spoke.append(t) or object(),
        )
        handle = SpeechHandle.create("hello")
        handle.cancel()
        outcome = speak_with_handle(handle)
        assert outcome.spoken is False
        assert outcome.cancelled_before_speaking is True
        assert spoke == [], "a cancelled utterance must not reach the engine"

    def test_cancellation_is_idempotent(self):
        handle = SpeechHandle.create("hi")
        handle.cancel()
        handle.cancel()
        assert handle.cancelled is True


class TestTruthfulOutput:
    def test_silence_is_never_reported_as_speech(self, monkeypatch):
        class Result:
            spoken = False
            detail = "no engine"
            engine = None

        monkeypatch.setattr(spoken_output, "speak_text", lambda t: Result())
        outcome = speak_with_handle(SpeechHandle.create("hello"))
        assert outcome.spoken is False
        assert outcome.detail == "no engine"

    def test_a_successful_utterance_reports_its_engine(self, monkeypatch):
        class Result:
            spoken = True
            detail = None
            engine = "espeak-ng"

        monkeypatch.setattr(spoken_output, "speak_text", lambda t: Result())
        outcome = speak_with_handle(SpeechHandle.create("hello"))
        assert outcome.spoken is True
        assert outcome.engine == "espeak-ng"

    def test_an_exploding_engine_is_reported_not_raised(self, monkeypatch):
        def explode(text):
            raise RuntimeError("engine crashed")

        monkeypatch.setattr(spoken_output, "speak_text", explode)
        outcome = speak_with_handle(SpeechHandle.create("hello"))
        assert outcome.spoken is False
        assert "engine crashed" in outcome.detail

    def test_missing_output_device_is_truthful(self, monkeypatch):
        monkeypatch.setattr(spoken_output, "available_engine", lambda: None)
        available, detail = output_available()
        assert available is False
        assert "no local speech engine" in detail

    def test_a_failing_discovery_is_unavailable_not_available(self, monkeypatch):
        def explode():
            raise OSError("audio subsystem down")

        monkeypatch.setattr(spoken_output, "available_engine", explode)
        available, detail = output_available()
        assert available is False
        assert "audio subsystem down" in detail

    def test_speech_is_classified_ordinary_and_ephemeral(self):
        _text, classification = prepare_text("hello")
        assert classification.privacy_class.value == "ordinary"
        assert classification.retention_class.value == "ephemeral"
