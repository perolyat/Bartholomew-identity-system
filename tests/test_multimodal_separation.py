"""Three permissions, never one. Speaking is not listening; listening is not seeing.

Acceptance gates covered: 3 (microphone, screen capture and spoken output
remain separate permissions), 15 (spoken-output authorization cannot start
microphone input), 12 (approved capture scope cannot expand silently).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bartholomew.kernel.runtime_contract import _MULTIMODAL_GATES
from bartholomew.multimodal.devices import StaticCapabilityResolver
from bartholomew.multimodal.modality import (
    CAPABILITY_KIND,
    CONSENT_PROMPT,
    CaptureScope,
    Modality,
    ScopeKind,
)
from bartholomew.multimodal.screen import CaptureRefusedError, capture_with_fallback

PACKAGE = Path(__file__).resolve().parents[1] / "bartholomew" / "multimodal"


class TestDistinctCapabilities:
    """Gate 3."""

    def test_three_distinct_capability_kinds(self):
        kinds = set(CAPABILITY_KIND.values())
        assert len(kinds) == 3
        assert kinds == {
            "multimodal.microphone_session",
            "multimodal.screen_capture",
            "multimodal.spoken_output",
        }, "capability kinds must match the frozen §3.3 declaration exactly"

    def test_three_distinct_policy_kinds_at_the_seam(self):
        policy_kinds = {kind for kind, _scope, _prompt in _MULTIMODAL_GATES.values()}
        assert len(policy_kinds) == 3

    def test_identity_allowlist_has_three_entries_and_no_blanket_one(self):
        import yaml

        identity = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "Identity.yaml").read_text(),
        )
        allowlist = identity["tool_use"]["allowlist"]
        entries = [e for e in allowlist if "multimodal" in e]
        assert sorted(entries) == [
            "multimodal_microphone_session",
            "multimodal_screen_capture",
            "multimodal_spoken_output",
        ]
        assert "multimodal" not in allowlist, "no blanket multimodal permission"

    def test_every_seam_kind_is_allowlisted_and_vice_versa(self):
        import yaml

        identity = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "Identity.yaml").read_text(),
        )
        allowlist = set(identity["tool_use"]["allowlist"])
        seam_kinds = {kind for kind, _s, _p in _MULTIMODAL_GATES.values()}
        assert seam_kinds <= allowlist

    def test_no_enum_member_grants_everything(self):
        assert {m.value for m in Modality} == {"microphone", "screen", "spoken_output"}
        for forbidden in ("all", "any", "enabled", "multimodal"):
            assert forbidden not in {m.value for m in Modality}

    def test_consent_prompts_name_one_modality_and_disclaim_the_others(self):
        assert CONSENT_PROMPT[Modality.MICROPHONE].count("does not permit") == 1
        assert "LISTEN" in CONSENT_PROMPT[Modality.MICROPHONE]
        assert "SPEAK" in CONSENT_PROMPT[Modality.SPOKEN_OUTPUT]
        assert "OBSERVE" in CONSENT_PROMPT[Modality.SCREEN]
        # Each prompt must disclaim the two it does not cover.
        assert "screen capture or speaking" in CONSENT_PROMPT[Modality.MICROPHONE]
        assert "listening or speaking" in CONSENT_PROMPT[Modality.SCREEN]
        assert "listening or screen capture" in CONSENT_PROMPT[Modality.SPOKEN_OUTPUT]

    def test_capability_resolution_is_per_kind(self):
        """A device declaring only speech cannot be used to listen."""
        resolver = StaticCapabilityResolver()
        resolver.declare("speaker-only", ["multimodal.spoken_output"])

        from bartholomew.multimodal.devices import resolve_modality_capability

        assert resolve_modality_capability(
            resolver,
            "speaker-only",
            Modality.SPOKEN_OUTPUT,
        ).supported
        assert not resolve_modality_capability(
            resolver,
            "speaker-only",
            Modality.MICROPHONE,
        ).supported
        assert not resolve_modality_capability(
            resolver,
            "speaker-only",
            Modality.SCREEN,
        ).supported

    def test_unknown_capability_version_is_unsupported(self):
        resolver = StaticCapabilityResolver()
        resolver.declare("old-device", ["multimodal.microphone_session"], version=99)
        from bartholomew.multimodal.devices import resolve_modality_capability

        result = resolve_modality_capability(resolver, "old-device", Modality.MICROPHONE)
        assert result.supported is False
        assert "does not declare" in result.reason


class TestSpeakingCannotListen:
    """Gate 15, asserted structurally rather than by inspection."""

    def test_speech_module_imports_nothing_from_microphone(self):
        tree = ast.parse((PACKAGE / "speech.py").read_text())
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(a.name for a in node.names)
        assert not any("microphone" in m for m in imported)
        assert not any("screen" in m for m in imported)

    def test_speech_module_mentions_no_capture_api(self):
        source = (PACKAGE / "speech.py").read_text()
        for forbidden in ("open_stream", "InputStream", "read_transcript", "grab("):
            assert forbidden not in source

    def test_spoken_output_capability_does_not_appear_in_microphone_paths(self):
        source = (PACKAGE / "microphone.py").read_text()
        assert "spoken_output" not in source

    def test_microphone_module_has_no_ambient_entry_point(self):
        """No wake word, no always-on, no auto-restart."""
        source = (PACKAGE / "microphone.py").read_text().lower()
        for forbidden in (
            "wake_word",
            "wakeword",
            "listen_forever",
            "always_on",
            "ambient",
            "auto_restart",
            "continuous",
        ):
            assert f"def {forbidden}" not in source
            assert f"{forbidden} =" not in source

    def test_no_module_offers_webcam_or_camera_capture(self):
        for path in PACKAGE.glob("*.py"):
            source = path.read_text().lower()
            for forbidden in ("webcam", "videocapture", "facial", "biometric"):
                assert f"def {forbidden}" not in source, path.name


class TestScopeCannotWidenSilently:
    """Gate 12."""

    def _backend(self):
        class Backend:
            def available(self):
                return True, "ok"

            def grab(self, scope):
                return object()

            def describe(self, image):
                return "a window"

        return Backend()

    def test_a_different_window_is_refused(self):
        approved = CaptureScope(ScopeKind.WINDOW, window_id="w1")
        with pytest.raises(CaptureRefusedError, match="outside the approved scope"):
            capture_with_fallback(
                approved_scope=approved,
                requested_scope=CaptureScope(ScopeKind.WINDOW, window_id="w2"),
                allow_screenshot_fallback=True,
                screen_backend=self._backend(),
            )

    def test_a_different_display_is_refused(self):
        approved = CaptureScope(ScopeKind.DISPLAY, display_id="1")
        with pytest.raises(CaptureRefusedError):
            capture_with_fallback(
                approved_scope=approved,
                requested_scope=CaptureScope(ScopeKind.DISPLAY, display_id="2"),
                allow_screenshot_fallback=True,
                screen_backend=self._backend(),
            )

    def test_a_window_scope_does_not_authorise_the_whole_display(self):
        approved = CaptureScope(ScopeKind.WINDOW, window_id="w1")
        with pytest.raises(CaptureRefusedError):
            capture_with_fallback(
                approved_scope=approved,
                requested_scope=CaptureScope(ScopeKind.DISPLAY, display_id="1"),
                allow_screenshot_fallback=True,
                screen_backend=self._backend(),
            )

    def test_a_larger_region_is_refused(self):
        approved = CaptureScope(ScopeKind.REGION, display_id="1", rect=(0, 0, 100, 100))
        with pytest.raises(CaptureRefusedError):
            capture_with_fallback(
                approved_scope=approved,
                requested_scope=CaptureScope(
                    ScopeKind.REGION,
                    display_id="1",
                    rect=(0, 0, 800, 600),
                ),
                allow_screenshot_fallback=True,
                screen_backend=self._backend(),
            )

    def test_a_region_on_another_display_is_refused(self):
        approved = CaptureScope(ScopeKind.REGION, display_id="1", rect=(0, 0, 100, 100))
        assert not approved.covers(
            CaptureScope(ScopeKind.REGION, display_id="2", rect=(0, 0, 10, 10)),
        )

    def test_a_narrower_region_is_allowed(self):
        approved = CaptureScope(ScopeKind.REGION, display_id="1", rect=(0, 0, 100, 100))
        assert approved.covers(
            CaptureScope(ScopeKind.REGION, display_id="1", rect=(10, 10, 20, 20)),
        )

    def test_scope_is_frozen(self):
        scope = CaptureScope(ScopeKind.WINDOW, window_id="w1")
        with pytest.raises(Exception):
            scope.window_id = "w2"

    def test_a_scope_must_name_its_target(self):
        for bad in (
            {"kind": ScopeKind.DISPLAY},
            {"kind": ScopeKind.WINDOW},
            {"kind": ScopeKind.REGION, "display_id": "1"},
            {"kind": ScopeKind.REGION, "rect": (0, 0, 10, 10)},
            {"kind": ScopeKind.REGION, "display_id": "1", "rect": (0, 0, 0, 10)},
        ):
            with pytest.raises(ValueError):
                CaptureScope(**bad)
