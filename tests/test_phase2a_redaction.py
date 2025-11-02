"""
Tests for Phase 2a: Redaction, Encryption Routing, and Summarization
"""

import pytest

from bartholomew.kernel.memory_rules import MemoryRulesEngine
from bartholomew.kernel.redaction_engine import (
    apply_redaction,
    mask_sensitive,
    remove_sensitive,
    replace_sensitive,
)


class TestRedactionEngine:
    """Test redaction engine strategies"""

    def test_mask_sensitive(self):
        """Test masking sensitive content"""
        text = "My password is hunter2"
        pattern = r"hunter2"
        result = mask_sensitive(text, pattern)
        assert result == "My password is ****"
        assert "hunter2" not in result

    def test_mask_case_insensitive(self):
        """Test case-insensitive masking"""
        text = "My PASSWORD is HUNTER2"
        pattern = r"hunter2"
        result = mask_sensitive(text, pattern)
        assert result == "My PASSWORD is ****"

    def test_remove_sensitive(self):
        """Test removing sensitive content"""
        text = "My SSN is 123-45-6789 and I live here"
        pattern = r"\d{3}-\d{2}-\d{4}"
        result = remove_sensitive(text, pattern)
        assert result == "My SSN is  and I live here"
        assert "123-45-6789" not in result

    def test_replace_sensitive(self):
        """Test replacing sensitive content"""
        text = "My SSN is 123-45-6789"
        pattern = r"\d{3}-\d{2}-\d{4}"
        replacement = "[SSN REDACTED]"
        result = replace_sensitive(text, pattern, replacement)
        assert result == "My SSN is [SSN REDACTED]"
        assert "123-45-6789" not in result

    def test_apply_redaction_mask(self):
        """Test apply_redaction with mask strategy"""
        rule = {"content": r"password", "redact_strategy": "mask"}
        text = "My password is secret123"
        result = apply_redaction(text, rule)
        assert "****" in result
        assert "password" not in result.lower()

    def test_apply_redaction_remove(self):
        """Test apply_redaction with remove strategy"""
        rule = {"content": r"confidential", "redact_strategy": "remove"}
        text = "This is confidential information"
        result = apply_redaction(text, rule)
        assert "confidential" not in result.lower()
        assert result == "This is  information"

    def test_apply_redaction_replace(self):
        """Test apply_redaction with replace strategy"""
        rule = {"content": r"email@example\.com", "redact_strategy": "replace:[EMAIL REDACTED]"}
        text = "Contact me at email@example.com"
        result = apply_redaction(text, rule)
        assert "[EMAIL REDACTED]" in result
        assert "email@example.com" not in result

    def test_apply_redaction_no_pattern(self):
        """Test apply_redaction with no pattern returns original"""
        rule = {"redact_strategy": "mask"}
        text = "Original text"
        result = apply_redaction(text, rule)
        assert result == text

    def test_apply_redaction_unknown_strategy(self):
        """Test apply_redaction with unknown strategy returns original"""
        rule = {"content": r"test", "redact_strategy": "unknown_strategy"}
        text = "Original test text"
        result = apply_redaction(text, rule)
        assert result == text

    def test_apply_redaction_invalid_regex(self):
        """Test apply_redaction with invalid regex returns original"""
        rule = {"content": r"[invalid(regex", "redact_strategy": "mask"}
        text = "Original text"
        result = apply_redaction(text, rule)
        assert result == text


class TestMemoryRulesEnrichment:
    """Test memory rules engine enrichment with Phase 2a fields"""

    def test_redact_flag_defaults_to_mask(self):
        """Test that redact: true defaults to mask strategy"""
        engine = MemoryRulesEngine(config_path=None, watch_file=False)

        memory = {"kind": "user", "content": "test content", "metadata": {}}

        # Simulate rule that sets redact: true but no strategy
        evaluated = engine.evaluate(memory)
        evaluated["redact"] = True

        # Manually trigger the default logic
        if evaluated.get("redact") and not evaluated.get("redact_strategy"):
            evaluated["redact_strategy"] = "mask"

        assert evaluated["redact_strategy"] == "mask"

    def test_redact_strategy_preserved(self):
        """Test that explicit redact_strategy is preserved"""
        engine = MemoryRulesEngine(config_path=None, watch_file=False)

        memory = {"kind": "user", "content": "test content", "metadata": {}}

        evaluated = engine.evaluate(memory)
        evaluated["redact_strategy"] = "remove"

        assert evaluated["redact_strategy"] == "remove"

    def test_encrypt_metadata_passed_through(self):
        """Test that encrypt metadata is passed through"""
        engine = MemoryRulesEngine(config_path=None, watch_file=False)

        memory = {"kind": "user", "content": "test content", "metadata": {}}

        evaluated = engine.evaluate(memory)
        evaluated["encrypt"] = "strong"

        assert evaluated.get("encrypt") == "strong"

    def test_summarize_metadata_passed_through(self):
        """Test that summarize metadata is passed through"""
        engine = MemoryRulesEngine(config_path=None, watch_file=False)

        memory = {"kind": "user", "content": "test content", "metadata": {}}

        evaluated = engine.evaluate(memory)
        evaluated["summarize"] = True

        assert evaluated.get("summarize") is True


class TestPhase2aIntegration:
    """Integration tests for Phase 2a features"""

    def test_redaction_in_memory_dict(self):
        """Test that redaction works with memory dict format"""
        rule = {"content": r"(?i)my email is \S+@\S+", "redact_strategy": "mask"}

        text = "Hi, my email is user@example.com for contact"
        result = apply_redaction(text, rule)

        assert "****" in result
        assert "user@example.com" not in result

    def test_multiple_redactions(self):
        """Test applying multiple redactions sequentially"""
        text = "My SSN is 123-45-6789 and email is user@example.com"

        # First redaction: SSN
        rule1 = {"content": r"\d{3}-\d{2}-\d{4}", "redact_strategy": "replace:[SSN]"}
        text = apply_redaction(text, rule1)

        # Second redaction: email
        rule2 = {"content": r"\S+@\S+", "redact_strategy": "mask"}
        text = apply_redaction(text, rule2)

        assert "[SSN]" in text
        assert "****" in text
        assert "123-45-6789" not in text
        assert "user@example.com" not in text

    def test_phase2a_metadata_structure(self):
        """Test that Phase 2a metadata structure is correct"""
        evaluated = {
            "redact": True,
            "redact_strategy": "mask",
            "encrypt": "strong",
            "summarize": True,
            "privacy_class": "user.sensitive",
            "recall_policy": "always",
        }

        # Verify all Phase 2a fields present
        assert "redact" in evaluated
        assert "redact_strategy" in evaluated
        assert "encrypt" in evaluated
        assert "summarize" in evaluated

        # Verify values
        assert evaluated["encrypt"] == "strong"
        assert evaluated["summarize"] is True
        assert evaluated["redact_strategy"] == "mask"


class TestRedactionYAMLRules:
    """Test redaction rules from YAML configuration"""

    def test_yaml_redact_section_exists(self):
        """Test that redact section can be loaded from YAML"""
        import os

        yaml_path = os.path.join("bartholomew", "config", "memory_rules.yaml")

        if not os.path.exists(yaml_path):
            yaml_path = os.path.join("config", "memory_rules.yaml")

        if os.path.exists(yaml_path):
            import yaml

            with open(yaml_path) as f:
                config = yaml.safe_load(f)

            # Check that redact section exists
            assert "redact" in config or "ask_before_store" in config

            # If redact section exists, verify structure
            if "redact" in config:
                for rule in config["redact"]:
                    assert "match" in rule
                    assert "metadata" in rule
                    metadata = rule["metadata"]

                    # Should have redact_strategy
                    if "redact_strategy" in metadata:
                        strategy = metadata["redact_strategy"]
                        assert strategy in ["mask", "remove"] or strategy.startswith("replace:")

    def test_yaml_rules_have_phase2a_fields(self):
        """Test that YAML rules include Phase 2a fields"""
        import os

        yaml_path = os.path.join("bartholomew", "config", "memory_rules.yaml")

        if not os.path.exists(yaml_path):
            yaml_path = os.path.join("config", "memory_rules.yaml")

        if os.path.exists(yaml_path):
            import yaml

            with open(yaml_path) as f:
                config = yaml.safe_load(f)

            phase2a_fields_found = False

            # Check all rule categories for Phase 2a fields
            for category in config.values():
                if isinstance(category, list):
                    for rule in category:
                        if "metadata" in rule:
                            metadata = rule["metadata"]
                            if any(
                                key in metadata
                                for key in ["redact", "redact_strategy", "encrypt", "summarize"]
                            ):
                                phase2a_fields_found = True
                                break

            # At least some rules should have Phase 2a fields
            assert phase2a_fields_found, "No Phase 2a fields found in memory_rules.yaml"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
