"""
Phase 2c: Summarization Layer Tests
Tests for automatic content summarization in memory storage
"""

import base64
import os

import pytest

from bartholomew.kernel.encryption_engine import Envelope
from bartholomew.kernel.summarization_engine import (
    AUTO_SUMMARIZE_KINDS,
    LENGTH_THRESHOLD,
    SummarizationEngine,
)


class TestSummarizationEngine:
    """Test summarization engine core logic"""

    def test_should_summarize_explicit_rule(self):
        """Summarize when rule explicitly sets summarize: true"""
        engine = SummarizationEngine()
        meta = {"summarize": True}
        value = "Short text"
        kind = "fact"

        assert engine.should_summarize(meta, value, kind) is True

    def test_should_summarize_auto_kind_long_value(self):
        """Summarize when kind is auto-kind and value is long"""
        engine = SummarizationEngine()
        meta = {}
        value = "x" * 1500  # Exceeds LENGTH_THRESHOLD
        kind = "conversation.transcript"

        assert kind in AUTO_SUMMARIZE_KINDS
        assert engine.should_summarize(meta, value, kind) is True

    def test_should_not_summarize_auto_kind_short_value(self):
        """Don't summarize auto-kind with short value"""
        engine = SummarizationEngine()
        meta = {}
        value = "Short conversation"
        kind = "conversation.transcript"

        assert engine.should_summarize(meta, value, kind) is False

    def test_should_not_summarize_full_always_mode(self):
        """Don't summarize when summary_mode is full_always"""
        engine = SummarizationEngine()
        meta = {"summarize": True, "summary_mode": "full_always"}
        value = "x" * 1500
        kind = "conversation.transcript"

        assert engine.should_summarize(meta, value, kind) is False

    def test_summarize_extracts_sentences(self):
        """Summarize extracts first sentences up to target length"""
        engine = SummarizationEngine(target_length=100)

        # Need longer content to avoid early return for short content
        value = (
            "First sentence is here with more words to make it longer. "
            "Second sentence follows with additional content. "
            "Third sentence comes next with even more text. "
            "Fourth sentence appears with lots of extra words. "
            "Fifth sentence ends it with a conclusion. "
            "Sixth sentence adds more details. "
            "Seventh sentence continues the pattern."
        )

        summary = engine.summarize(value)

        assert len(summary) <= 110  # Allow small margin for sentence boundaries
        assert "First sentence" in summary
        assert len(summary) < len(value)

    def test_summarize_short_content_returns_original(self):
        """Summarize returns original if content is too short"""
        engine = SummarizationEngine()
        value = "Too short"

        summary = engine.summarize(value)

        assert summary == value

    def test_summarize_fallback_truncation(self):
        """Summarize truncates if no sentence boundaries found"""
        engine = SummarizationEngine(target_length=50)

        # No sentence boundaries
        value = "x" * 1000

        summary = engine.summarize(value)

        assert len(summary) <= 53  # 50 + "..."
        assert summary.endswith("...")


class TestMemoryStoreIntegration:
    """Integration tests with kernel memory store"""

    @pytest.mark.asyncio
    async def test_auto_summarize_long_transcript(self, tmp_path):
        """Auto-summarize long conversation.transcript"""
        import aiosqlite

        from bartholomew.kernel.memory_store import MemoryStore

        db_path = str(tmp_path / "test.db")
        store = MemoryStore(db_path)
        await store.init()

        # Long conversation transcript
        long_value = "This is a very long conversation transcript. " * 30
        assert len(long_value) > LENGTH_THRESHOLD

        await store.upsert_memory(
            kind="conversation.transcript",
            key="chat_001",
            value=long_value,
            ts="2024-01-01T00:00:00Z",
        )

        # Check database
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT value, summary FROM memories WHERE kind=? AND key=?",
                ("conversation.transcript", "chat_001"),
            )
            row = await cursor.fetchone()
            stored_value, stored_summary = row

        # Value should remain full text
        assert stored_value == long_value

        # Summary should be populated and shorter
        assert stored_summary is not None
        assert len(stored_summary) < len(long_value)
        assert len(stored_summary) > 0

    @pytest.mark.asyncio
    async def test_no_summarize_short_value(self, tmp_path):
        """Don't summarize short values"""
        import aiosqlite

        from bartholomew.kernel.memory_store import MemoryStore

        db_path = str(tmp_path / "test.db")
        store = MemoryStore(db_path)
        await store.init()

        short_value = "Brief chat message"

        await store.upsert_memory(
            kind="conversation.transcript",
            key="chat_002",
            value=short_value,
            ts="2024-01-01T00:00:00Z",
        )

        # Check database
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT value, summary FROM memories WHERE kind=? AND key=?",
                ("conversation.transcript", "chat_002"),
            )
            row = await cursor.fetchone()
            stored_value, stored_summary = row

        assert stored_value == short_value
        assert stored_summary is None  # No summary for short content

    @pytest.mark.asyncio
    async def test_summary_only_mode(self, tmp_path, monkeypatch):
        """summary_only mode replaces value with summary"""
        import aiosqlite

        from bartholomew.kernel.memory_store import MemoryStore

        # Mock rules engine to return summary_only mode
        def mock_evaluate(memory_dict):
            return {
                "allow_store": True,
                "summarize": True,
                "summary_mode": "summary_only",
                "kind": memory_dict.get("kind"),
                "key": memory_dict.get("key"),
                "content": memory_dict.get("value"),
                "matched_categories": [],
                "matched_rules": [],
            }

        from bartholomew.kernel import memory_rules

        original_evaluate = memory_rules._rules_engine.evaluate
        memory_rules._rules_engine.evaluate = mock_evaluate

        try:
            db_path = str(tmp_path / "test.db")
            store = MemoryStore(db_path)
            await store.init()

            long_value = "This is a long value. " * 100

            await store.upsert_memory(
                kind="test_kind",
                key="test_key",
                value=long_value,
                ts="2024-01-01T00:00:00Z",
            )

            # Check database
            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute(
                    "SELECT value, summary FROM memories WHERE kind=? AND key=?",
                    ("test_kind", "test_key"),
                )
                row = await cursor.fetchone()
                stored_value, stored_summary = row

            # Value should be compressed (the summary)
            assert len(stored_value) < len(long_value)

            # Summary column should be NULL (not storing separate)
            assert stored_summary is None
        finally:
            memory_rules._rules_engine.evaluate = original_evaluate

    @pytest.mark.asyncio
    async def test_summary_encrypted_with_value(self, tmp_path, monkeypatch):
        """Summary is encrypted when encryption is enabled"""
        import aiosqlite

        from bartholomew.kernel.memory_store import MemoryStore

        # Set up encryption key
        key = os.urandom(32)
        key_b64 = base64.urlsafe_b64encode(key).decode()
        monkeypatch.setenv("BME_KEY_STANDARD", key_b64)

        # Reload encryption engine with new key
        from bartholomew.kernel.encryption_engine import (
            EncryptionEngine,
            EnvKeyProvider,
        )

        new_engine = EncryptionEngine(key_provider=EnvKeyProvider())
        from bartholomew.kernel import encryption_engine

        encryption_engine._encryption_engine = new_engine

        # Mock rules to enable encryption and summarization
        def mock_evaluate(memory_dict):
            return {
                "allow_store": True,
                "encrypt": "standard",
                "summarize": True,
                "summary_mode": "summary_also",
                "kind": memory_dict.get("kind"),
                "key": memory_dict.get("key"),
                "content": memory_dict.get("value"),
                "matched_categories": [],
                "matched_rules": [],
            }

        from bartholomew.kernel import memory_rules

        original_evaluate = memory_rules._rules_engine.evaluate
        memory_rules._rules_engine.evaluate = mock_evaluate

        try:
            db_path = str(tmp_path / "test.db")
            store = MemoryStore(db_path)
            await store.init()

            long_value = "Sensitive information. " * 100

            await store.upsert_memory(
                kind="user_data",
                key="sensitive_001",
                value=long_value,
                ts="2024-01-01T00:00:00Z",
            )

            # Check database
            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute(
                    "SELECT value, summary FROM memories WHERE kind=? AND key=?",
                    ("user_data", "sensitive_001"),
                )
                row = await cursor.fetchone()
                stored_value, stored_summary = row

            # Both should be encrypted (JSON envelopes)
            value_env = Envelope.from_json(stored_value)
            summary_env = Envelope.from_json(stored_summary)

            assert value_env is not None, "Value should be encrypted"
            assert summary_env is not None, "Summary should be encrypted"

            # Decrypt and verify
            decrypted_value = new_engine.try_decrypt_if_envelope(stored_value)
            decrypted_summary = new_engine.try_decrypt_if_envelope(stored_summary)

            assert decrypted_value == long_value
            assert len(decrypted_summary) < len(decrypted_value)
            assert len(decrypted_summary) > 0
        finally:
            memory_rules._rules_engine.evaluate = original_evaluate

    @pytest.mark.asyncio
    async def test_schema_has_summary_column(self, tmp_path):
        """Database schema includes summary column"""
        import aiosqlite

        from bartholomew.kernel.memory_store import MemoryStore

        db_path = str(tmp_path / "test.db")
        store = MemoryStore(db_path)
        await store.init()

        # Check schema
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA table_info(memories)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

        assert "summary" in column_names

    @pytest.mark.asyncio
    async def test_schema_migration_adds_summary(self, tmp_path):
        """Existing databases are migrated to add summary column"""
        import aiosqlite

        db_path = str(tmp_path / "test.db")

        # Create old schema without summary
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    ts TEXT NOT NULL
                )
                """,
            )
            await db.commit()

        # Initialize store (should trigger migration)
        from bartholomew.kernel.memory_store import MemoryStore

        store = MemoryStore(db_path)
        await store.init()

        # Verify summary column was added
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA table_info(memories)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

        assert "summary" in column_names


class TestYAMLRulesIntegration:
    """Test summarization rules from YAML configuration"""

    def test_yaml_has_summarization_docs(self):
        """YAML config documents summarization policy"""
        import os

        yaml_path = os.path.join("bartholomew", "config", "memory_rules.yaml")

        if not os.path.exists(yaml_path):
            yaml_path = os.path.join("config", "memory_rules.yaml")

        if os.path.exists(yaml_path):
            with open(yaml_path, encoding="utf-8") as f:
                content = f.read()

            # Check for summarization documentation
            assert "Summarization policy" in content
            assert "summary_mode" in content
            assert "summary_only" in content
            assert "summary_also" in content
            assert "full_always" in content

    def test_yaml_rules_include_summary_mode(self):
        """YAML rules include summary_mode examples"""
        import os

        yaml_path = os.path.join("bartholomew", "config", "memory_rules.yaml")

        if not os.path.exists(yaml_path):
            yaml_path = os.path.join("config", "memory_rules.yaml")

        if os.path.exists(yaml_path):
            import yaml

            with open(yaml_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)

            # Check for summary_mode in rules
            has_summary_mode = False
            for category in config.values():
                if isinstance(category, list):
                    for rule in category:
                        if "metadata" in rule:
                            metadata = rule["metadata"]
                            if "summary_mode" in metadata:
                                has_summary_mode = True
                                # Verify valid mode
                                mode = metadata["summary_mode"]
                                assert mode in ["summary_only", "summary_also", "full_always"]

            assert has_summary_mode, "At least one rule should specify summary_mode"


class TestAutoSummarizeKinds:
    """Test auto-summarization for specific kinds"""

    def test_auto_summarize_kinds_defined(self):
        """AUTO_SUMMARIZE_KINDS includes expected types"""
        expected_kinds = {
            "conversation.transcript",
            "recording.transcript",
            "article.ingested",
            "code.diff",
            "chat",
        }

        assert AUTO_SUMMARIZE_KINDS == expected_kinds

    @pytest.mark.asyncio
    async def test_chat_kind_auto_summarizes(self, tmp_path):
        """chat kind with long content auto-summarizes"""
        import aiosqlite

        from bartholomew.kernel.memory_store import MemoryStore

        db_path = str(tmp_path / "test.db")
        store = MemoryStore(db_path)
        await store.init()

        long_chat = "User said something. " * 100

        await store.upsert_memory(
            kind="chat",
            key="chat_001",
            value=long_chat,
            ts="2024-01-01T00:00:00Z",
        )

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT summary FROM memories WHERE kind=? AND key=?",
                ("chat", "chat_001"),
            )
            row = await cursor.fetchone()
            summary = row[0]

        assert summary is not None
        assert len(summary) < len(long_chat)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
