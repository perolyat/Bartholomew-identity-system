"""
Comprehensive Memory Management System for Bartholomew
Implements episodic, semantic, affective, and symbolic memory modalities
"""

import asyncio
import base64
import json
import logging
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import keyring
from cryptography.fernet import Fernet

from bartholomew.kernel.memory.privacy_guard import (
    is_sensitive,
    request_permission_to_store,
)
from bartholomew.kernel.memory_rules import _rules_engine
from bartholomew.kernel.redaction_engine import apply_redaction


# Schema version for migration management
CURRENT_SCHEMA_VERSION = 3

# Configurable keyring identifiers
SERVICE_NAME = os.getenv("BARTHO_MEMORY_KEYRING_SERVICE", "bartholomew_memory")
KEY_NAME = os.getenv("BARTHO_MEMORY_KEYRING_NAME", "encryption_key")


class MemoryModality(Enum):
    """Types of memory storage"""

    EPISODIC = "episodic"  # Specific events and conversations
    SEMANTIC = "semantic"  # Facts and learned knowledge
    AFFECTIVE = "affective"  # Emotional context and relationships
    SYMBOLIC = "symbolic"  # Abstract patterns and insights


@dataclass
class MemoryEntry:
    """Base memory entry structure"""

    id: str
    modality: MemoryModality
    timestamp: datetime
    content: str
    metadata: dict[str, Any]
    confidence: float
    ttl_days: int | None = None
    anchor: str | None = None
    encrypted: bool = False
    summary: str | None = None  # Phase 2c: Optional summary
    # e.g. "user.essential", "user.identity",
    # "environment.passive", "thirdparty.private"
    privacy_class: str | None = None
    # e.g. "context_only", "always", "never"
    recall_policy: str | None = None
    # e.g. "30d", "7d", null = no expiry
    expires_in: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage"""
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        data["modality"] = self.modality.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryEntry":
        """Create from dictionary"""
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        data["modality"] = MemoryModality(data["modality"])
        return cls(**data)


@dataclass
class ConversationTurn:
    """Single conversation exchange"""

    id: str
    timestamp: datetime
    user_input: str
    ai_response: str
    context: dict[str, Any]
    confidence: float
    model_used: str

    def to_memory_entry(self) -> MemoryEntry:
        """Convert to episodic memory entry"""
        content = f"User: {self.user_input}\nAI: {self.ai_response}"
        metadata = {
            "context": self.context,
            "model_used": self.model_used,
            "conversation_turn": True,
        }

        return MemoryEntry(
            id=self.id,
            modality=MemoryModality.EPISODIC,
            timestamp=self.timestamp,
            content=content,
            metadata=metadata,
            confidence=self.confidence,
            ttl_days=90,  # Default from Identity.yaml
        )


class MemoryManager:
    """
    Comprehensive memory management system
    Handles all four memory modalities with encryption and retention policies
    """

    def __init__(self, identity_config, data_dir: str = "./data"):
        """
        Initialize memory manager

        Args:
            identity_config: Identity configuration object
            data_dir: Directory for memory storage
        """
        self.identity = identity_config
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

        # Memory policy from Identity.yaml
        self.memory_policy = identity_config.memory_policy

        # Vector store will be handled by separate component
        self.vector_store_path = Path(self.memory_policy.vector_store.path)

        # Logger
        self.logger = logging.getLogger(__name__)

        # Initialize encryption
        self._init_encryption()

        # Initialize database
        self.db_path = self.data_dir / "memory.db"
        self._init_database()

    def _init_encryption(self):
        """Initialize encryption using OS keystore"""
        try:
            # Try to get existing key
            key_b64 = keyring.get_password(SERVICE_NAME, KEY_NAME)
            if key_b64:
                self.encryption_key = base64.b64decode(key_b64.encode())
            else:
                # Generate new key
                self.encryption_key = Fernet.generate_key()
                key_b64 = base64.b64encode(self.encryption_key).decode()
                keyring.set_password(SERVICE_NAME, KEY_NAME, key_b64)

            self.cipher = Fernet(self.encryption_key)
            self.logger.info("Encryption initialized using OS keystore")

        except Exception as e:
            self.logger.error(f"Failed to initialize encryption: {e}")
            # Check if encryption is required
            encryption_required = self.memory_policy.encryption.get("at_rest", False)
            if encryption_required:
                raise RuntimeError(
                    "Encryption is required but keystore initialization "
                    f"failed: {e}. Ensure OS keystore is accessible.",
                )
            # Fallback to no encryption (development only)
            self.cipher = None

    def _init_database(self):
        """Initialize SQLite database with schema versioning"""
        with sqlite3.connect(self.db_path) as conn:
            # Enable safety and performance features
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")

            # Check schema version
            version = conn.execute("PRAGMA user_version").fetchone()[0] or 0

            if version == 0:
                # Fresh database - create v1 schema
                self._create_schema_v1(conn)
                conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION};")
                self.logger.info(f"Initialized database schema v{CURRENT_SCHEMA_VERSION}")
            elif version < CURRENT_SCHEMA_VERSION:
                # Migration needed
                self._migrate_schema(conn, version, CURRENT_SCHEMA_VERSION)
                self.logger.info(
                    f"Migrated database from v{version} to v{CURRENT_SCHEMA_VERSION}",
                )

    def _create_schema_v1(self, conn):
        """Create version 1 schema"""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                modality TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                content TEXT NOT NULL,
                summary TEXT,
                metadata TEXT NOT NULL,
                confidence REAL NOT NULL,
                ttl_days INTEGER,
                anchor TEXT,
                encrypted BOOLEAN DEFAULT FALSE,
                expires_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                privacy_class TEXT,
                recall_policy TEXT,
                expires_in TEXT
            )
        """,
        )

        conn.execute("CREATE INDEX IF NOT EXISTS idx_modality ON memories(modality)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON memories(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expires_at ON memories(expires_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_anchor ON memories(anchor)")

    def _migrate_schema(self, conn, from_version: int, to_version: int):
        """Migrate database schema between versions"""
        for v in range(from_version + 1, to_version + 1):
            if v == 2:
                # Add privacy-aware metadata columns
                try:
                    conn.execute("ALTER TABLE memories ADD COLUMN privacy_class TEXT;")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                try:
                    conn.execute("ALTER TABLE memories ADD COLUMN recall_policy TEXT;")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                try:
                    conn.execute("ALTER TABLE memories ADD COLUMN expires_in TEXT;")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            elif v == 3:
                # Phase 2c: Add summary column
                try:
                    conn.execute("ALTER TABLE memories ADD COLUMN summary TEXT;")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            else:
                raise RuntimeError(f"No migration path defined for version {v}")
        conn.execute(f"PRAGMA user_version = {to_version};")

    def _encrypt_content(self, content: str) -> str:
        """Encrypt content if encryption is enabled"""
        if self.cipher and self.memory_policy.encryption.get("at_rest", False):
            encrypted_bytes = self.cipher.encrypt(content.encode())
            return base64.b64encode(encrypted_bytes).decode()
        return content

    def _decrypt_content(self, content: str, is_encrypted: bool) -> str:
        """Decrypt content if it's encrypted"""
        if is_encrypted and self.cipher:
            try:
                encrypted_bytes = base64.b64decode(content.encode())
                decrypted_bytes = self.cipher.decrypt(encrypted_bytes)
                return decrypted_bytes.decode()
            except Exception as e:
                self.logger.error(f"Failed to decrypt content: {e}")
                return "[DECRYPTION_FAILED]"
        return content

    def _calculate_expiry(
        self,
        ttl_days: int | None,
        anchor: str | None,
    ) -> datetime | None:
        """Calculate when memory expires based on TTL and anchor rules"""
        if anchor in self.memory_policy.retention_rules.long_term_anchors:
            return None  # Never expires

        if ttl_days is None:
            ttl_days = self.memory_policy.retention_rules.default_ttl_days

        return datetime.now() + timedelta(days=ttl_days)

    def store_memory(self, memory: MemoryEntry) -> bool:
        """
        Store a memory entry

        Args:
            memory: Memory entry to store

        Returns:
            True if stored successfully
        """
        try:
            # Rule evaluation: check governance rules first
            memory_dict = memory.to_dict()
            evaluated = _rules_engine.evaluate(memory_dict)

            # Check if storage is blocked by rules
            if not _rules_engine.should_store(evaluated):
                self.logger.info(f"Memory blocked by governance rules: {memory.id}")
                return False

            # Check if consent is required by rules
            if _rules_engine.requires_consent(evaluated):
                try:
                    # Check if we're in a running event loop
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = None

                    if loop and loop.is_running():
                        # Use sync fallback
                        from identity_interpreter.adapters.consent_terminal import (  # noqa: E501
                            ConsentAdapter,
                        )

                        allowed = ConsentAdapter(self.identity).request_consent(  # noqa: E501
                            action="store_sensitive_memory",
                            details=memory.content,
                            scope="per_use",
                        )
                    else:
                        # Safe to use asyncio.run
                        allowed = asyncio.run(request_permission_to_store(memory.content))
                except Exception as e:
                    self.logger.error(f"Consent prompt failed: {e}")
                    return False

                if not allowed:
                    print("[Bartholomew] OK, I won't store that memory.")
                    return False

            # Apply redaction if required by rules (Phase 2a)
            if evaluated.get("redact_strategy"):
                memory.content = apply_redaction(memory.content, evaluated)
                strategy = evaluated["redact_strategy"]
                self.logger.debug(
                    f"Applied redaction strategy '{strategy}' to memory {memory.id}",
                )

            # Inject enriched metadata from rules
            if evaluated.get("privacy_class"):
                memory.privacy_class = evaluated["privacy_class"]
            if evaluated.get("recall_policy"):
                memory.recall_policy = evaluated["recall_policy"]
            if evaluated.get("expires_in"):
                memory.expires_in = evaluated["expires_in"]

            # TODO Phase 2b: Enforce encryption based on evaluated["encrypt"]
            # TODO Phase 2c: Generate summary if evaluated["summarize"] is true

            # Privacy guard fallback: check for sensitive content
            if is_sensitive(memory.content):
                try:
                    # Check if we're in a running event loop
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        loop = None

                    if loop and loop.is_running():
                        # Use sync fallback to avoid nested loop issues
                        from identity_interpreter.adapters.consent_terminal import (  # noqa: E501
                            ConsentAdapter,
                        )

                        allowed = ConsentAdapter(self.identity).request_consent(  # noqa: E501
                            action="store_sensitive_memory",
                            details=memory.content,
                            scope="per_use",
                        )
                    else:
                        # Safe to use asyncio.run
                        allowed = asyncio.run(request_permission_to_store(memory.content))
                except Exception as e:
                    self.logger.error(f"Consent prompt failed: {e}")
                    return False

                if not allowed:
                    print("[Bartholomew] OK, I won't store that memory.")
                    return False

            # Calculate expiry
            expires_at = self._calculate_expiry(memory.ttl_days, memory.anchor)

            # Encrypt content if needed
            should_encrypt = self.memory_policy.encryption.get(
                "at_rest",
                False,
            )
            if should_encrypt:
                content = self._encrypt_content(memory.content)
            else:
                content = memory.content

            # Store in database
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO memories
                    (id, modality, timestamp, content, metadata,
                     confidence, ttl_days, anchor, encrypted, expires_at,
                     privacy_class, recall_policy, expires_in)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        memory.id,
                        memory.modality.value,
                        memory.timestamp.isoformat(),
                        content,
                        json.dumps(memory.metadata),
                        memory.confidence,
                        memory.ttl_days,
                        memory.anchor,
                        should_encrypt,
                        expires_at.isoformat() if expires_at else None,
                        memory.privacy_class,
                        memory.recall_policy,
                        memory.expires_in,
                    ),
                )

            msg = f"Stored {memory.modality.value} memory: {memory.id}"
            self.logger.debug(msg)
            return True

        except Exception as e:
            self.logger.error(f"Failed to store memory {memory.id}: {e}")
            return False

    def retrieve_memories(
        self,
        modality: MemoryModality | None = None,
        limit: int = 100,
        since: datetime | None = None,
        anchor: str | None = None,
    ) -> list[MemoryEntry]:
        """
        Retrieve memories with filtering

        Args:
            modality: Filter by memory type
            limit: Maximum number of memories to return
            since: Only return memories after this timestamp
            anchor: Filter by anchor type

        Returns:
            List of memory entries
        """
        try:
            # Clean up expired memories first
            self._cleanup_expired_memories()

            # Build query
            query = "SELECT * FROM memories WHERE 1=1"
            params = []

            if modality:
                query += " AND modality = ?"
                params.append(modality.value)

            if since:
                query += " AND timestamp >= ?"
                params.append(since.isoformat())

            if anchor:
                query += " AND anchor = ?"
                params.append(anchor)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            # Execute query
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()

            # Convert to memory entries
            memories = []
            for row in rows:
                content = self._decrypt_content(
                    row["content"],
                    row["encrypted"],
                )

                memory = MemoryEntry(
                    id=row["id"],
                    modality=MemoryModality(row["modality"]),
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    content=content,
                    metadata=json.loads(row["metadata"]),
                    confidence=row["confidence"],
                    ttl_days=row["ttl_days"],
                    anchor=row["anchor"],
                    encrypted=row["encrypted"],
                    privacy_class=row["privacy_class"],
                    recall_policy=row["recall_policy"],
                    expires_in=row["expires_in"],
                )
                memories.append(memory)

            return memories

        except Exception as e:
            self.logger.error(f"Failed to retrieve memories: {e}")
            return []

    def store_conversation_turn(self, turn: ConversationTurn):
        """Store a conversation turn as episodic memory"""
        memory = turn.to_memory_entry()
        return self.store_memory(memory)

    def get_recent_conversation(
        self,
        limit: int = 10,
    ) -> list[ConversationTurn]:
        """Get recent conversation history"""
        memories = self.retrieve_memories(
            modality=MemoryModality.EPISODIC,
            limit=limit,
        )

        turns = []
        for memory in memories:
            if memory.metadata.get("conversation_turn"):
                # Parse conversation content
                lines = memory.content.split("\n", 1)
                if len(lines) >= 2:
                    user_input = lines[0].replace("User: ", "")
                    ai_response = lines[1].replace("AI: ", "")

                    turn = ConversationTurn(
                        id=memory.id,
                        timestamp=memory.timestamp,
                        user_input=user_input,
                        ai_response=ai_response,
                        context=memory.metadata.get("context", {}),
                        confidence=memory.confidence,
                        model_used=memory.metadata.get("model_used", "unknown"),
                    )
                    turns.append(turn)

        return turns

    def _cleanup_expired_memories(self):
        """Remove expired memories"""
        try:
            now = datetime.now().isoformat()
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?",
                    (now,),
                )
                if cursor.rowcount > 0:
                    msg = f"Cleaned up {cursor.rowcount} expired memories"
                    self.logger.info(msg)
        except Exception as e:
            self.logger.error(f"Failed to cleanup expired memories: {e}")

    def export_memories(self, format: str = "jsonl") -> str:
        """Export all memories for user control"""
        if format not in self.memory_policy.export_formats:
            raise ValueError(f"Unsupported export format: {format}")

        memories = self.retrieve_memories(limit=10000)  # Get all memories

        if format == "jsonl":
            lines = []
            for memory in memories:
                lines.append(json.dumps(memory.to_dict()))
            return "\n".join(lines)

        # Add other formats as needed
        raise NotImplementedError(f"Export format {format} not implemented")

    def erase_all_memories(self) -> bool:
        """Erase all memories (user control)"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM memories")
            self.logger.info("All memories erased by user request")
            return True
        except Exception as e:
            self.logger.error(f"Failed to erase memories: {e}")
            return False

    # Stable API interface methods

    def write_memory(self, memory: MemoryEntry) -> bool:
        """
        Stable API: Write a memory entry
        Alias for store_memory for cross-language consistency
        """
        return self.store_memory(memory)

    def read_memories(
        self,
        modality: MemoryModality | None = None,
        limit: int = 100,
        since: datetime | None = None,
        anchor: str | None = None,
    ) -> list[MemoryEntry]:
        """
        Stable API: Read memories with filtering
        Alias for retrieve_memories for cross-language consistency
        """
        return self.retrieve_memories(modality=modality, limit=limit, since=since, anchor=anchor)

    def build_context(self, limit: int = 10) -> str:
        """
        Stable API: Build context string for prompt injection
        Formats recent conversation history for LLM context
        """
        turns = self.get_recent_conversation(limit=limit)
        lines = []
        for turn in reversed(turns):  # Chronological order
            lines.append(f"User: {turn.user_input}")
            lines.append(f"AI: {turn.ai_response}")
        return "\n".join(lines)

    def cleanup(self) -> int:
        """
        Stable API: Clean up expired memories
        Returns count of deleted memories
        """
        try:
            now = datetime.now().isoformat()
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?",
                    (now,),
                )
                count = cursor.rowcount or 0
                if count > 0:
                    self.logger.info(f"Cleaned up {count} expired memories")
                return count
        except Exception as e:
            self.logger.error(f"Cleanup failed: {e}")
            return 0

    def health_check(self) -> dict[str, bool]:
        """
        Stable API: Verify memory system health
        Returns status of database and encryption components
        """
        status = {"db": True, "cipher": True}

        # Check database connectivity
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("SELECT 1")
        except Exception as e:
            status["db"] = False
            self.logger.error(f"DB health check failed: {e}")

        # Check cipher availability when encryption is required
        if self.memory_policy.encryption.get("at_rest", False):
            status["cipher"] = self.cipher is not None

        return status


# End of memory_manager.py
