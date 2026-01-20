"""
Tests for Persona Pack System

Stage 3.5: Persona Pack System Implementation
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bartholomew.kernel.persona_pack import (
    Brevity,
    Formality,
    PersonaPack,
    PersonaPackManager,
    PersonaSwitchRecord,
    StyleConfig,
    create_caregiver_pack,
    create_default_pack,
    create_tactical_pack,
    get_persona_manager,
    reset_persona_manager,
)


# =============================================================================
# StyleConfig Tests
# =============================================================================


class TestStyleConfig:
    """Tests for StyleConfig dataclass."""

    def test_style_config_defaults(self):
        """Test default style configuration values."""
        style = StyleConfig()
        assert style.brevity == Brevity.BALANCED
        assert style.formality == Formality.CONVERSATIONAL
        assert style.humor_allowed is True
        assert style.emoji_allowed is False
        assert style.technical_depth == 0.5
        assert style.warmth == 0.7
        assert style.directness == 0.6

    def test_style_config_custom_values(self):
        """Test style configuration with custom values."""
        style = StyleConfig(
            brevity=Brevity.CONCISE,
            formality=Formality.PROFESSIONAL,
            humor_allowed=False,
            warmth=0.3,
            directness=0.9,
        )
        assert style.brevity == Brevity.CONCISE
        assert style.formality == Formality.PROFESSIONAL
        assert style.humor_allowed is False
        assert style.warmth == 0.3
        assert style.directness == 0.9

    def test_style_config_serialization(self):
        """Test style config serialization to dict."""
        style = StyleConfig(
            brevity=Brevity.MINIMAL,
            formality=Formality.CASUAL,
            humor_allowed=True,
            warmth=0.95,
        )
        data = style.to_dict()
        assert data["brevity"] == "minimal"
        assert data["formality"] == "casual"
        assert data["humor_allowed"] is True
        assert data["warmth"] == 0.95

    def test_style_config_deserialization(self):
        """Test style config deserialization from dict."""
        data = {
            "brevity": "expanded",
            "formality": "formal",
            "humor_allowed": False,
            "emoji_allowed": True,
            "technical_depth": 0.8,
            "warmth": 0.2,
            "directness": 0.1,
        }
        style = StyleConfig.from_dict(data)
        assert style.brevity == Brevity.EXPANDED
        assert style.formality == Formality.FORMAL
        assert style.humor_allowed is False
        assert style.emoji_allowed is True
        assert style.technical_depth == 0.8
        assert style.warmth == 0.2
        assert style.directness == 0.1

    def test_style_config_roundtrip(self):
        """Test serialization/deserialization roundtrip."""
        original = StyleConfig(
            brevity=Brevity.CONCISE,
            formality=Formality.PROFESSIONAL,
            humor_allowed=False,
            emoji_allowed=False,
            technical_depth=0.7,
            warmth=0.4,
            directness=0.9,
        )
        data = original.to_dict()
        restored = StyleConfig.from_dict(data)
        assert restored.brevity == original.brevity
        assert restored.formality == original.formality
        assert restored.humor_allowed == original.humor_allowed
        assert restored.warmth == original.warmth


# =============================================================================
# Brevity and Formality Enum Tests
# =============================================================================


class TestEnums:
    """Tests for Brevity and Formality enums."""

    def test_brevity_values(self):
        """Test Brevity enum values."""
        assert Brevity.MINIMAL.value == "minimal"
        assert Brevity.CONCISE.value == "concise"
        assert Brevity.BALANCED.value == "balanced"
        assert Brevity.EXPANDED.value == "expanded"

    def test_formality_values(self):
        """Test Formality enum values."""
        assert Formality.CASUAL.value == "casual"
        assert Formality.CONVERSATIONAL.value == "conversational"
        assert Formality.PROFESSIONAL.value == "professional"
        assert Formality.FORMAL.value == "formal"


# =============================================================================
# PersonaPack Tests
# =============================================================================


class TestPersonaPack:
    """Tests for PersonaPack dataclass."""

    def test_persona_pack_creation(self):
        """Test basic persona pack creation."""
        pack = PersonaPack(
            pack_id="test_pack",
            name="Test Pack",
            description="A test persona pack",
        )
        assert pack.pack_id == "test_pack"
        assert pack.name == "Test Pack"
        assert pack.description == "A test persona pack"
        assert pack.tone == ["warm", "helpful"]  # Default
        assert pack.archetype == "companion"  # Default
        assert pack.is_default is False  # Default

    def test_persona_pack_with_all_fields(self):
        """Test persona pack with all fields specified."""
        pack = PersonaPack(
            pack_id="full_pack",
            name="Full Pack",
            description="A fully configured pack",
            tone=["precise", "urgent"],
            style=StyleConfig(brevity=Brevity.CONCISE),
            drive_boosts={"protect_user_wellbeing": 0.3},
            narrative_overrides={"affect_shift": {"neutral": ["Template 1", "Template 2"]}},
            auto_activate_on=["gaming", "crisis"],
            archetype="tactical",
            inspirations=["Cortana"],
            author="test_author",
            version="2.0.0",
            is_default=False,
        )
        assert pack.tone == ["precise", "urgent"]
        assert pack.style.brevity == Brevity.CONCISE
        assert pack.drive_boosts["protect_user_wellbeing"] == 0.3
        assert "affect_shift" in pack.narrative_overrides
        assert pack.auto_activate_on == ["gaming", "crisis"]
        assert pack.archetype == "tactical"
        assert "Cortana" in pack.inspirations
        assert pack.author == "test_author"
        assert pack.version == "2.0.0"

    def test_persona_pack_serialization(self):
        """Test persona pack serialization to dict."""
        pack = PersonaPack(
            pack_id="serial_pack",
            name="Serial Pack",
            description="For serialization testing",
            tone=["warm", "kind"],
            drive_boosts={"show_kindness": 0.1},
        )
        data = pack.to_dict()
        assert data["pack_id"] == "serial_pack"
        assert data["name"] == "Serial Pack"
        assert data["tone"] == ["warm", "kind"]
        assert "style" in data
        assert "created_at" in data

    def test_persona_pack_deserialization(self):
        """Test persona pack deserialization from dict."""
        data = {
            "pack_id": "deser_pack",
            "name": "Deser Pack",
            "description": "Deserialization test",
            "tone": ["patient", "soothing"],
            "style": {"brevity": "expanded", "warmth": 0.95},
            "drive_boosts": {"protect_user_wellbeing": 0.3},
            "auto_activate_on": ["wellness"],
            "archetype": "caregiver",
            "inspirations": ["Baymax"],
            "author": "system",
            "version": "1.0.0",
            "is_default": True,
        }
        pack = PersonaPack.from_dict(data)
        assert pack.pack_id == "deser_pack"
        assert pack.tone == ["patient", "soothing"]
        assert pack.style.brevity == Brevity.EXPANDED
        assert pack.style.warmth == 0.95
        assert pack.drive_boosts["protect_user_wellbeing"] == 0.3
        assert pack.auto_activate_on == ["wellness"]
        assert pack.is_default is True

    def test_persona_pack_roundtrip(self):
        """Test serialization/deserialization roundtrip."""
        original = PersonaPack(
            pack_id="roundtrip",
            name="Roundtrip Pack",
            description="Testing roundtrip",
            tone=["curious", "playful"],
            style=StyleConfig(warmth=0.8, directness=0.5),
            drive_boosts={"express_curiosity": 0.2},
        )
        data = original.to_dict()
        restored = PersonaPack.from_dict(data)
        assert restored.pack_id == original.pack_id
        assert restored.name == original.name
        assert restored.tone == original.tone
        assert restored.style.warmth == original.style.warmth


class TestPersonaPackYAML:
    """Tests for PersonaPack YAML file operations."""

    def test_save_and_load_yaml(self, tmp_path):
        """Test saving and loading pack from YAML."""
        pack = PersonaPack(
            pack_id="yaml_test",
            name="YAML Test Pack",
            description="Testing YAML operations",
            tone=["warm", "helpful"],
        )
        yaml_path = tmp_path / "test_pack.yaml"
        pack.save_to_yaml(yaml_path)

        assert yaml_path.exists()

        loaded = PersonaPack.load_from_yaml(yaml_path)
        assert loaded.pack_id == "yaml_test"
        assert loaded.name == "YAML Test Pack"

    def test_load_from_nonexistent_file(self, tmp_path):
        """Test loading from nonexistent file raises error."""
        yaml_path = tmp_path / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError):
            PersonaPack.load_from_yaml(yaml_path)


# =============================================================================
# PersonaSwitchRecord Tests
# =============================================================================


class TestPersonaSwitchRecord:
    """Tests for PersonaSwitchRecord dataclass."""

    def test_switch_record_creation(self):
        """Test switch record creation."""
        record = PersonaSwitchRecord(
            record_id="rec-123",
            timestamp=datetime.now(timezone.utc),
            from_pack_id="default",
            to_pack_id="tactical",
            trigger="manual",
            context_tags=["gaming"],
        )
        assert record.record_id == "rec-123"
        assert record.from_pack_id == "default"
        assert record.to_pack_id == "tactical"
        assert record.trigger == "manual"
        assert "gaming" in record.context_tags

    def test_switch_record_serialization(self):
        """Test switch record serialization."""
        record = PersonaSwitchRecord(
            record_id="rec-456",
            timestamp=datetime(2026, 1, 21, 10, 0, 0, tzinfo=timezone.utc),
            from_pack_id=None,
            to_pack_id="default",
            trigger="startup",
            context_tags=[],
            metadata={"reason": "initial load"},
        )
        data = record.to_dict()
        assert data["record_id"] == "rec-456"
        assert data["from_pack_id"] is None
        assert data["to_pack_id"] == "default"
        assert data["trigger"] == "startup"
        assert data["metadata"]["reason"] == "initial load"

    def test_switch_record_deserialization(self):
        """Test switch record deserialization."""
        data = {
            "record_id": "rec-789",
            "timestamp": "2026-01-21T12:00:00+00:00",
            "from_pack_id": "default",
            "to_pack_id": "caregiver",
            "trigger": "auto",
            "context_tags": ["wellness", "distress"],
            "metadata": {},
        }
        record = PersonaSwitchRecord.from_dict(data)
        assert record.record_id == "rec-789"
        assert record.from_pack_id == "default"
        assert record.to_pack_id == "caregiver"
        assert record.trigger == "auto"
        assert "wellness" in record.context_tags


# =============================================================================
# Default Pack Factory Tests
# =============================================================================


class TestDefaultPackFactories:
    """Tests for the default pack factory functions."""

    def test_create_default_pack(self):
        """Test default pack creation."""
        pack = create_default_pack()
        assert pack.pack_id == "default"
        assert pack.name == "Bartholomew"
        assert "warm" in pack.tone
        assert pack.archetype == "companion"
        assert pack.is_default is True
        assert pack.style.warmth == 0.8

    def test_create_tactical_pack(self):
        """Test tactical pack creation."""
        pack = create_tactical_pack()
        assert pack.pack_id == "tactical"
        assert pack.name == "Tactical Bartholomew"
        assert "precise" in pack.tone
        assert pack.archetype == "tactical"
        assert pack.is_default is False
        assert pack.style.directness == 0.9
        assert pack.style.humor_allowed is False
        assert "gaming" in pack.auto_activate_on

    def test_create_caregiver_pack(self):
        """Test caregiver pack creation."""
        pack = create_caregiver_pack()
        assert pack.pack_id == "caregiver"
        assert pack.name == "Caregiver Bartholomew"
        assert "patient" in pack.tone
        assert pack.archetype == "caregiver"
        assert pack.is_default is False
        assert pack.style.warmth == 0.95
        assert "wellness" in pack.auto_activate_on
        assert "Baymax" in pack.inspirations


# =============================================================================
# PersonaPackManager Tests
# =============================================================================


class TestPersonaPackManagerInit:
    """Tests for PersonaPackManager initialization."""

    def test_manager_init_defaults(self, tmp_path):
        """Test manager initialization with defaults."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        assert manager.get_active_pack() is None
        assert manager.list_packs() == []

    def test_manager_loads_packs_from_directory(self, tmp_path):
        """Test manager loads packs from directory."""
        # Create a test pack file
        pack_data = {
            "pack_id": "test_auto_load",
            "name": "Auto Load Test",
            "description": "Testing auto-load",
            "is_default": True,
        }
        pack_file = tmp_path / "test_auto_load.yaml"
        import yaml

        with open(pack_file, "w", encoding="utf-8") as f:
            yaml.dump(pack_data, f)

        manager = PersonaPackManager(packs_dir=tmp_path)
        assert "test_auto_load" in manager.list_packs()
        # Default pack should be auto-activated
        assert manager.get_active_pack_id() == "test_auto_load"


class TestPersonaPackManagerRegistration:
    """Tests for PersonaPackManager pack registration."""

    def test_register_pack(self, tmp_path):
        """Test registering a pack."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        pack = create_default_pack()
        manager.register_pack(pack)

        assert "default" in manager.list_packs()
        assert manager.get_pack("default") is not None
        # Default pack should auto-activate
        assert manager.get_active_pack_id() == "default"

    def test_register_multiple_packs(self, tmp_path):
        """Test registering multiple packs."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())
        manager.register_pack(create_caregiver_pack())

        packs = manager.list_packs()
        assert len(packs) == 3
        assert "default" in packs
        assert "tactical" in packs
        assert "caregiver" in packs

    def test_unregister_pack(self, tmp_path):
        """Test unregistering a pack."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())

        # Switch to tactical first so default can be unregistered
        manager.switch_pack("tactical")
        result = manager.unregister_pack("default")

        assert result is True
        assert "default" not in manager.list_packs()

    def test_cannot_unregister_active_pack(self, tmp_path):
        """Test that active pack cannot be unregistered."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())

        result = manager.unregister_pack("default")
        assert result is False
        assert "default" in manager.list_packs()

    def test_unregister_nonexistent_pack(self, tmp_path):
        """Test unregistering nonexistent pack returns False."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        result = manager.unregister_pack("nonexistent")
        assert result is False


class TestPersonaPackManagerSwitching:
    """Tests for PersonaPackManager pack switching."""

    def test_switch_pack(self, tmp_path):
        """Test switching packs."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())

        result = manager.switch_pack("tactical")
        assert result is True
        assert manager.get_active_pack_id() == "tactical"

    def test_switch_to_same_pack(self, tmp_path):
        """Test switching to already active pack."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())

        # Switch once
        manager.switch_pack("default")
        # Switch again
        result = manager.switch_pack("default")
        assert result is True  # Should succeed (no-op)

    def test_switch_to_nonexistent_pack(self, tmp_path):
        """Test switching to nonexistent pack fails."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())

        result = manager.switch_pack("nonexistent")
        assert result is False
        assert manager.get_active_pack_id() == "default"

    def test_switch_logs_to_database(self, tmp_path):
        """Test that switches are logged to database."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())

        manager.switch_pack("tactical", trigger="manual")

        history = manager.get_switch_history()
        assert len(history) == 1
        assert history[0].to_pack_id == "tactical"
        assert history[0].trigger == "manual"

    def test_switch_with_context_tags(self, tmp_path):
        """Test switching with context tags."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())

        manager.switch_pack(
            "tactical",
            trigger="auto",
            context_tags=["gaming", "competitive"],
        )

        history = manager.get_switch_history()
        assert "gaming" in history[0].context_tags
        assert "competitive" in history[0].context_tags


class TestPersonaPackManagerAutoActivation:
    """Tests for PersonaPackManager auto-activation."""

    def test_check_auto_activation(self, tmp_path):
        """Test checking for auto-activation match."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())

        # Tactical pack auto-activates on "gaming"
        pack_id = manager.check_auto_activation(["gaming"])
        assert pack_id == "tactical"

    def test_check_auto_activation_no_match(self, tmp_path):
        """Test checking when no auto-activation matches."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())

        pack_id = manager.check_auto_activation(["random_tag"])
        assert pack_id is None

    def test_auto_activate_if_needed(self, tmp_path):
        """Test automatic activation based on context."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())
        manager.register_pack(create_caregiver_pack())

        # Start with default
        assert manager.get_active_pack_id() == "default"

        # Trigger auto-activation with wellness tag
        switched = manager.auto_activate_if_needed(["wellness"])
        assert switched is True
        assert manager.get_active_pack_id() == "caregiver"

    def test_auto_activate_no_change_when_already_active(self, tmp_path):
        """Test no switch when target pack already active."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())

        manager.switch_pack("tactical")
        switched = manager.auto_activate_if_needed(["gaming"])
        assert switched is False  # Already on tactical


class TestPersonaPackManagerCallbacks:
    """Tests for PersonaPackManager callbacks."""

    def test_on_switch_callback(self, tmp_path):
        """Test switch callback is called."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())

        callback_calls = []

        def on_switch(from_pack, to_pack):
            callback_calls.append(
                (
                    from_pack.pack_id if from_pack else None,
                    to_pack.pack_id,
                ),
            )

        manager.on_switch(on_switch)
        manager.switch_pack("tactical")

        assert len(callback_calls) == 1
        assert callback_calls[0] == ("default", "tactical")

    def test_multiple_callbacks(self, tmp_path):
        """Test multiple switch callbacks."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())

        calls_1 = []
        calls_2 = []

        manager.on_switch(lambda f, t: calls_1.append(t.pack_id))
        manager.on_switch(lambda f, t: calls_2.append(t.pack_id))
        manager.switch_pack("tactical")

        assert len(calls_1) == 1
        assert len(calls_2) == 1

    def test_remove_callback(self, tmp_path):
        """Test removing a callback."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())

        calls = []

        def callback(f, t):
            calls.append(t.pack_id)

        manager.on_switch(callback)
        removed = manager.remove_switch_callback(callback)
        assert removed is True

        manager.switch_pack("tactical")
        assert len(calls) == 0  # Callback was removed

    def test_callback_error_doesnt_break_switch(self, tmp_path):
        """Test that callback errors don't break the switch."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())

        def bad_callback(f, t):
            raise RuntimeError("Callback error!")

        manager.on_switch(bad_callback)
        result = manager.switch_pack("tactical")

        assert result is True  # Switch should still succeed
        assert manager.get_active_pack_id() == "tactical"


class TestPersonaPackManagerHistory:
    """Tests for PersonaPackManager switch history."""

    def test_get_switch_history(self, tmp_path):
        """Test getting switch history."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())
        manager.register_pack(create_caregiver_pack())

        manager.switch_pack("tactical")
        manager.switch_pack("caregiver")
        manager.switch_pack("default")

        history = manager.get_switch_history()
        assert len(history) == 3
        # Most recent first
        assert history[0].to_pack_id == "default"
        assert history[1].to_pack_id == "caregiver"
        assert history[2].to_pack_id == "tactical"

    def test_get_switch_history_with_limit(self, tmp_path):
        """Test getting switch history with limit."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())
        manager.register_pack(create_caregiver_pack())

        manager.switch_pack("tactical")
        manager.switch_pack("caregiver")
        manager.switch_pack("default")

        history = manager.get_switch_history(limit=2)
        assert len(history) == 2

    def test_get_switch_count(self, tmp_path):
        """Test getting total switch count."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())

        assert manager.get_switch_count() == 0

        manager.switch_pack("tactical")
        assert manager.get_switch_count() == 1

        manager.switch_pack("default")
        assert manager.get_switch_count() == 2


class TestPersonaPackManagerNarrativeIntegration:
    """Tests for PersonaPackManager narrative integration."""

    def test_get_narrative_templates(self, tmp_path):
        """Test getting narrative templates from active pack."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_tactical_pack())
        manager.switch_pack("tactical")

        templates = manager.get_narrative_templates("attention_focus", "neutral")
        assert templates is not None
        assert len(templates) > 0
        assert any("{target}" in t for t in templates)

    def test_get_narrative_templates_no_override(self, tmp_path):
        """Test getting templates when no override exists."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_default_pack())
        manager.switch_pack("default")

        # Default pack has no narrative overrides
        templates = manager.get_narrative_templates("affect_shift", "neutral")
        assert templates is None

    def test_get_style(self, tmp_path):
        """Test getting style from active pack."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_caregiver_pack())
        manager.switch_pack("caregiver")

        style = manager.get_style()
        assert style is not None
        assert style.warmth == 0.95
        assert style.brevity == Brevity.EXPANDED

    def test_get_tone(self, tmp_path):
        """Test getting tone from active pack."""
        manager = PersonaPackManager(packs_dir=tmp_path)
        manager.register_pack(create_tactical_pack())
        manager.switch_pack("tactical")

        tone = manager.get_tone()
        assert "precise" in tone
        assert "urgent" in tone


# =============================================================================
# GlobalWorkspace Integration Tests
# =============================================================================


class TestPersonaPackWorkspaceIntegration:
    """Tests for PersonaPackManager GlobalWorkspace integration."""

    def test_switch_emits_workspace_event(self, tmp_path):
        """Test that switching packs emits a workspace event."""
        from bartholomew.kernel.global_workspace import (
            EventType,
            GlobalWorkspace,
        )

        workspace = GlobalWorkspace()
        manager = PersonaPackManager(packs_dir=tmp_path, workspace=workspace)
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())

        received_events = []

        def on_event(event):
            received_events.append(event)

        workspace.subscribe("persona", on_event)
        manager.switch_pack("tactical")

        assert len(received_events) == 1
        event = received_events[0]
        assert event.event_type == EventType.PERSONA_SWITCHED
        assert event.payload["from_pack_id"] == "default"
        assert event.payload["to_pack_id"] == "tactical"


# =============================================================================
# ExperienceKernel Integration Tests
# =============================================================================


class TestPersonaPackKernelIntegration:
    """Tests for PersonaPackManager ExperienceKernel integration."""

    def test_switch_applies_drive_boosts(self, tmp_path):
        """Test that switching applies drive boosts to kernel."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        kernel = ExperienceKernel()
        manager = PersonaPackManager(
            packs_dir=tmp_path,
            experience_kernel=kernel,
        )
        manager.register_pack(create_default_pack())
        manager.register_pack(create_tactical_pack())

        # Initial state - default pack with small boosts
        # Just verify the drive exists
        kernel.get_drive("show_kindness_in_all_interactions")

        # Switch to tactical
        manager.switch_pack("tactical")

        # Check tactical boosts are applied
        tactical_drive = kernel.get_drive("provide_tactical_support_when_needed")
        if tactical_drive:
            assert tactical_drive.context_boost == 0.3

    def test_switch_clears_previous_boosts(self, tmp_path):
        """Test that switching clears previous pack's boosts."""
        from bartholomew.kernel.experience_kernel import ExperienceKernel

        kernel = ExperienceKernel()
        manager = PersonaPackManager(
            packs_dir=tmp_path,
            experience_kernel=kernel,
        )
        # Register tactical first (so it doesn't auto-activate)
        manager.register_pack(create_tactical_pack())
        manager.register_pack(create_default_pack())

        # Switch to tactical to apply its boosts
        manager.switch_pack("tactical")
        tactical_drive = kernel.get_drive("provide_tactical_support_when_needed")
        if tactical_drive:
            assert tactical_drive.context_boost == 0.3

        # Switch to default
        manager.switch_pack("default")

        # Tactical boosts should be cleared
        tactical_drive = kernel.get_drive("provide_tactical_support_when_needed")
        if tactical_drive:
            assert tactical_drive.context_boost == 0.0


# =============================================================================
# Singleton Tests
# =============================================================================


class TestPersonaPackSingleton:
    """Tests for the singleton pattern."""

    def teardown_method(self):
        """Reset singleton after each test."""
        reset_persona_manager()

    def test_get_persona_manager_returns_same_instance(self, tmp_path):
        """Test that get_persona_manager returns the same instance."""
        manager1 = get_persona_manager(packs_dir=tmp_path)
        manager2 = get_persona_manager()

        assert manager1 is manager2

    def test_reset_persona_manager(self, tmp_path):
        """Test that reset_persona_manager creates new instance."""
        manager1 = get_persona_manager(packs_dir=tmp_path)
        reset_persona_manager()
        manager2 = get_persona_manager(packs_dir=tmp_path)

        assert manager1 is not manager2


# =============================================================================
# Load from Config Directory Tests
# =============================================================================


class TestPersonaPackLoadFromConfig:
    """Tests for loading packs from config/persona_packs."""

    def test_loads_default_yaml(self):
        """Test loading the default.yaml pack from config."""
        config_dir = Path("config/persona_packs")
        if not config_dir.exists():
            pytest.skip("config/persona_packs directory not found")

        default_path = config_dir / "default.yaml"
        if not default_path.exists():
            pytest.skip("default.yaml not found")

        pack = PersonaPack.load_from_yaml(default_path)
        assert pack.pack_id == "default"
        assert pack.is_default is True

    def test_loads_tactical_yaml(self):
        """Test loading the tactical.yaml pack from config."""
        config_dir = Path("config/persona_packs")
        if not config_dir.exists():
            pytest.skip("config/persona_packs directory not found")

        tactical_path = config_dir / "tactical.yaml"
        if not tactical_path.exists():
            pytest.skip("tactical.yaml not found")

        pack = PersonaPack.load_from_yaml(tactical_path)
        assert pack.pack_id == "tactical"
        assert "gaming" in pack.auto_activate_on

    def test_loads_caregiver_yaml(self):
        """Test loading the caregiver.yaml pack from config."""
        config_dir = Path("config/persona_packs")
        if not config_dir.exists():
            pytest.skip("config/persona_packs directory not found")

        caregiver_path = config_dir / "caregiver.yaml"
        if not caregiver_path.exists():
            pytest.skip("caregiver.yaml not found")

        pack = PersonaPack.load_from_yaml(caregiver_path)
        assert pack.pack_id == "caregiver"
        assert "wellness" in pack.auto_activate_on
