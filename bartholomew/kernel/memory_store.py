from __future__ import annotations
import aiosqlite
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

import numpy as np

from bartholomew.kernel.memory.privacy_guard import (
    is_sensitive,
    request_permission_to_store,
)
from bartholomew.kernel.memory_rules import _rules_engine
from bartholomew.kernel.redaction_engine import apply_redaction
from bartholomew.kernel.encryption_engine import _encryption_engine
from bartholomew.kernel.summarization_engine import _summarization_engine
from bartholomew.kernel.policy import can_index

logger = logging.getLogger(__name__)


def _load_fts_index_mode() -> str:
    """
    Load FTS index mode from kernel.yaml configuration.
    
    Returns:
        Index mode: 'summary_preferred' (default) or 'redacted_only'
    """
    import os
    import yaml
    
    try:
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "kernel.yaml"
        )
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
                if config and "fts" in config:
                    return config["fts"].get("index_mode", "summary_preferred")
    except Exception as e:
        logger.debug(f"Could not load FTS index mode config: {e}")
    
    return "summary_preferred"


@dataclass
class StoreResult:
    """Result of a memory storage operation"""
    memory_id: Optional[int] = None
    stored: bool = False
    ephemeral_embeddings: List[Tuple[str, np.ndarray]] = field(
        default_factory=list
    )
    created_or_updated: str = "created"  # "created" or "updated"


# Phase 2d: Lazy imports for embeddings (optional feature)
_embedding_engine = None
_vector_store = None
_summary_fallback_warned = False  # Global flag to warn once


def _get_embedding_components(db_path: str):
    """
    Lazy load embedding components
    
    Returns tuple of (embedding_engine, vector_store) or (None, None)
    if embeddings not enabled or imports fail
    """
    global _embedding_engine, _vector_store
    
    # Check if embeddings are enabled
    import os
    if not os.getenv("BARTHO_EMBED_ENABLED"):
        return None, None
    
    try:
        if _embedding_engine is None:
            from bartholomew.kernel.embedding_engine import (
                get_embedding_engine
            )
            _embedding_engine = get_embedding_engine()
        
        if _vector_store is None:
            from bartholomew.kernel.vector_store import VectorStore
            _vector_store = VectorStore(db_path)
        
        return _embedding_engine, _vector_store
    except Exception as e:
        logger.warning(f"Failed to load embedding components: {e}")
        return None, None


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,      -- 'fact', 'event', 'preference'
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  summary TEXT,            -- Optional summary of value content
  ts TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_kind_key ON memories(kind, key);

CREATE TABLE IF NOT EXISTS nudges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  actions TEXT,  -- JSON array of action objects
  status TEXT CHECK(
    status IN ('pending','acked','dismissed')
  ) DEFAULT 'pending',
  reason TEXT,
  created_ts TEXT NOT NULL,
  acted_ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_nudges_status_ts ON nudges(status, created_ts);

CREATE TABLE IF NOT EXISTS reflections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  meta TEXT,  -- JSON metadata
  ts TEXT NOT NULL,
  pinned INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reflections_kind_ts
  ON reflections(kind, ts);

CREATE TABLE IF NOT EXISTS memory_consent (
  memory_id INTEGER PRIMARY KEY,
  consent_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  source TEXT,
  FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
);
"""


class MemoryStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            
            # Phase 2c: Migrate existing databases to add summary column
            cursor = await db.execute("PRAGMA table_info(memories)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
            
            if "summary" not in column_names:
                await db.execute(
                    "ALTER TABLE memories ADD COLUMN summary TEXT"
                )
                logger.info("Migrated memories table: added summary column")
            
            await db.commit()
        
        # Phase 2e: Initialize FTS5 tables and triggers
        try:
            from bartholomew.kernel.fts_client import FTSClient
            fts = FTSClient(self.db_path)
            fts.init_schema()
            logger.info("FTS5 schema initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize FTS5 schema: {e}")

    async def upsert_memory(
        self, kind: str, key: str, value: str, ts: str
    ) -> StoreResult:
        # Rule evaluation: check governance rules first
        memory_dict = {
            "kind": kind,
            "key": key,
            "value": value,
            "ts": ts,
        }
        evaluated = _rules_engine.evaluate(memory_dict)
        
        # Check if storage is blocked by rules
        if not _rules_engine.should_store(evaluated):
            print(
                f"[Bartholomew] Memory blocked by governance rules: "
                f"{kind}/{key}"
            )
            return StoreResult(stored=False)
        
        # Apply redaction if required by rules (Phase 2a)
        redacted_value = value
        if evaluated.get("redact_strategy"):
            redacted_value = apply_redaction(value, evaluated)
        
        # Phase 2c: Generate summary if required (before encryption)
        summary = None
        summary_mode = evaluated.get("summary_mode", "summary_also")
        
        if _summarization_engine.should_summarize(
            evaluated, redacted_value, kind
        ):
            summary = _summarization_engine.summarize(redacted_value)
            
            # Handle summary_only mode: replace value with summary
            if summary_mode == "summary_only":
                redacted_value = summary
                summary = None  # Don't store separate summary
        
        # Phase 2e: Compute FTS index text (before encryption)
        # NEVER index raw/unredacted/blocked content
        # Use summary if available and preferred, otherwise use redacted value
        fts_index_mode = evaluated.get(
            "fts_index_mode", _load_fts_index_mode()
        )
        index_text = (
            summary if summary and fts_index_mode == "summary_preferred"
            else redacted_value
        )
        
        # Apply encryption if required by rules (Phase 2b)
        # Start with redacted_value, replace with encrypted if needed
        value_to_store = redacted_value
        cipher = _encryption_engine.encrypt_for_policy(
            redacted_value,
            evaluated,
            {"kind": kind, "key": key, "ts": ts},
        )
        if cipher is not None:
            value_to_store = cipher
        
        # Encrypt summary if present and encryption is enabled
        cipher_summary = None
        if summary is not None:
            cipher_summary = _encryption_engine.encrypt_for_policy(
                summary,
                evaluated,
                {"kind": kind, "key": key + "::summary", "ts": ts},
            )
            if cipher_summary is not None:
                summary = cipher_summary
        
        # Privacy guard fallback: check for sensitive content
        if is_sensitive(value):
            try:
                allowed = asyncio.run(request_permission_to_store(value))
            except RuntimeError:
                # Handles "event loop is already running" errors
                import nest_asyncio
                nest_asyncio.apply()
                allowed = asyncio.run(request_permission_to_store(value))

            if not allowed:
                print("[Bartholomew] OK, I won't store that kernel memory.")
                return StoreResult(stored=False)

        # Prepare result object
        result = StoreResult()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO memories(kind,key,value,summary,ts) "
                "VALUES(?,?,?,?,?) "
                "ON CONFLICT(kind,key) DO UPDATE SET "
                "value=excluded.value, summary=excluded.summary, "
                "ts=excluded.ts",
                (kind, key, value_to_store, summary, ts),
            )
            
            # Get memory_id for result
            cursor = await db.execute(
                "SELECT id FROM memories WHERE kind=? AND key=?",
                (kind, key)
            )
            row = await cursor.fetchone()
            if row:
                result.memory_id = row[0]
                result.stored = True
                
                # Phase 2e: Update FTS index in same transaction
                # CRITICAL: Tie FTS operations to same Tx as base row change
                fts_allowed = evaluated.get("fts_index", True)
                
                # Apply policy-based indexing guard
                if fts_allowed and not can_index(evaluated):
                    fts_allowed = False
                    logger.info(
                        f"FTS indexing blocked by policy for "
                        f"memory {result.memory_id}"
                    )
                
                if fts_allowed:
                    # Ensure entry in map table
                    await db.execute(
                        "INSERT OR IGNORE INTO memory_fts_map(memory_id) "
                        "VALUES (?)",
                        (result.memory_id,)
                    )
                    
                    # Delete prior FTS content for this rowid
                    await db.execute(
                        "INSERT INTO memory_fts(memory_fts, rowid, value, "
                        "summary) VALUES ('delete', ?, '', '')",
                        (result.memory_id,)
                    )
                    
                    # Insert sanitized index_text (never raw/unredacted)
                    await db.execute(
                        "INSERT INTO memory_fts(rowid, value, summary) "
                        "VALUES (?, ?, NULL)",
                        (result.memory_id, index_text)
                    )
                    logger.debug(
                        f"FTS index updated in-Tx for memory "
                        f"{result.memory_id}"
                    )
                else:
                    # Policy denies indexing: remove from FTS in same Tx
                    await db.execute(
                        "INSERT INTO memory_fts(memory_fts, rowid, value, "
                        "summary) VALUES ('delete', ?, '', '')",
                        (result.memory_id,)
                    )
                    await db.execute(
                        "DELETE FROM memory_fts_map WHERE memory_id = ?",
                        (result.memory_id,)
                    )
                    logger.debug(
                        f"FTS index removed in-Tx for memory "
                        f"{result.memory_id} (policy denied)"
                    )
            
            # Commit transaction (includes base row + FTS changes)
            await db.commit()
            
            # Phase 2d: Generate embeddings if enabled
            embed_engine, vec_store = _get_embedding_components(
                self.db_path
            )
            if embed_engine and vec_store and result.memory_id:
                # Check if rule allows embedding
                embed_mode = evaluated.get("embed", "summary")
                # Phase 2d+: embed_store defaults to True when
                # embed != 'none'
                if "embed_store" in evaluated:
                    embed_store = evaluated["embed_store"]
                else:
                    # Default: True if embeddings configured, else False
                    embed_store = (embed_mode != "none")
                
                # Apply policy-based indexing guard for embeddings
                if embed_mode != "none" and not can_index(evaluated):
                    embed_mode = "none"
                    logger.info(
                        f"Vector embedding blocked by policy for "
                        f"memory {result.memory_id}"
                    )
                
                if embed_mode != "none":
                    # Determine what to embed
                    texts_to_embed = []
                    sources = []
                    
                    # Use ORIGINAL values before encryption for embedding
                    orig_value = memory_dict["value"]
                    if evaluated.get("redact_strategy"):
                        orig_value = apply_redaction(orig_value, evaluated)
                    
                    # Check if we have summary
                    orig_summary = None
                    if _summarization_engine.should_summarize(
                        evaluated, orig_value, kind
                    ):
                        orig_summary = _summarization_engine.summarize(
                            orig_value
                        )
                    
                    # Build texts list with fallback for missing summary
                    if embed_mode in ("summary", "both"):
                        if orig_summary:
                            texts_to_embed.append(orig_summary)
                            sources.append("summary")
                        else:
                            # Phase 2d+: Fallback to redacted content
                            global _summary_fallback_warned
                            if not _summary_fallback_warned:
                                logger.warning(
                                    "Summary missing for embedding; "
                                    "using redacted content as fallback"
                                )
                                _summary_fallback_warned = True
                            # Trim to ~500 chars as summary substitute
                            fallback_text = orig_value[:500].strip()
                            if fallback_text:
                                texts_to_embed.append(fallback_text)
                                sources.append("summary")
                    
                    if embed_mode in ("full", "both"):
                        texts_to_embed.append(orig_value)
                        sources.append("full")
                    
                    if texts_to_embed:
                        try:
                            # Embed texts
                            vecs = embed_engine.embed_texts(texts_to_embed)
                            
                            if embed_store:
                                # Record consent in async transaction
                                await db.execute(
                                    "INSERT OR IGNORE INTO memory_consent "
                                    "(memory_id, source) VALUES (?, ?)",
                                    (result.memory_id, "upsert_memory")
                                )
                                await db.commit()
                            else:
                                # Compute-only: return as ephemeral
                                for src, vec in zip(sources, vecs):
                                    result.ephemeral_embeddings.append(
                                        (src, vec)
                                    )
                                logger.debug(
                                    f"Computed {len(vecs)} ephemeral "
                                    f"embedding(s) (not persisted)"
                                )
                                # Exit early for compute-only
                                return result
                            
                            # Store embeddings after closing async context
                            # to avoid database lock
                            result._pending_embeddings = (vecs, sources)
                        except Exception as e:
                            logger.error(
                                f"Failed to generate embeddings: {e}"
                            )
        
        # Phase 2d: Persist embeddings outside async context
        if hasattr(result, '_pending_embeddings'):
            vecs, sources = result._pending_embeddings
            try:
                cfg = embed_engine.config
                for src, vec in zip(sources, vecs):
                    vec_store.upsert(
                        result.memory_id, vec, src,
                        cfg.provider, cfg.model
                    )
                logger.debug(
                    f"Stored {len(vecs)} embedding(s) "
                    f"for memory {result.memory_id}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to persist embeddings: {e}"
                )
            delattr(result, '_pending_embeddings')
        
        return result

    async def delete_memory(self, kind: str, key: str) -> bool:
        """
        Delete a memory and its FTS index in a single transaction.
        
        Args:
            kind: Memory kind
            key: Memory key
            
        Returns:
            True if deleted, False if not found
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Look up memory_id
            cursor = await db.execute(
                "SELECT id FROM memories WHERE kind=? AND key=?",
                (kind, key)
            )
            row = await cursor.fetchone()
            
            if not row:
                return False
            
            memory_id = row[0]
            
            # Delete FTS index entry in same transaction
            await db.execute(
                "INSERT INTO memory_fts(memory_fts, rowid, value, "
                "summary) VALUES ('delete', ?, '', '')",
                (memory_id,)
            )
            await db.execute(
                "DELETE FROM memory_fts_map WHERE memory_id = ?",
                (memory_id,)
            )
            
            # Delete base row (triggers will also fire for cleanup)
            await db.execute(
                "DELETE FROM memories WHERE id = ?",
                (memory_id,)
            )
            
            await db.commit()
            logger.debug(
                f"Deleted memory {kind}/{key} (id={memory_id}) "
                f"with FTS cleanup in same Tx"
            )
            return True

    async def create_nudge(
        self,
        kind: str,
        message: str,
        actions: List[Dict[str, Any]],
        reason: str,
        created_ts: str,
    ) -> int:
        """Create a new nudge and return its ID."""
        actions_json = json.dumps(actions)
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "INSERT INTO nudges(kind, message, actions, reason, "
                "created_ts, status) VALUES(?,?,?,?,?,'pending')",
                (kind, message, actions_json, reason, created_ts),
            )
            await db.commit()
            return cur.lastrowid

    async def set_nudge_status(
        self, nudge_id: int, status: str, acted_ts: Optional[str] = None
    ) -> None:
        """Update nudge status to acked or dismissed."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE nudges SET status=?, acted_ts=? WHERE id=?",
                (status, acted_ts, nudge_id),
            )
            await db.commit()

    async def list_pending_nudges(self, limit: int = 50) -> List[Dict]:
        """Get pending nudges."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT id, kind, message, actions, reason, created_ts "
                "FROM nudges WHERE status='pending' "
                "ORDER BY created_ts DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
            return [
                {
                    "id": r[0],
                    "kind": r[1],
                    "message": r[2],
                    "actions": json.loads(r[3]) if r[3] else [],
                    "reason": r[4],
                    "created_ts": r[5],
                }
                for r in rows
            ]

    async def nudges_sent_today_count(
        self, kind: str, start_utc_iso: str, end_utc_iso: str
    ) -> int:
        """Count nudges of a given kind sent today."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM nudges "
                "WHERE kind=? AND created_ts BETWEEN ? AND ?",
                (kind, start_utc_iso, end_utc_iso),
            )
            row = await cur.fetchone()
            return int(row[0] or 0)

    async def last_nudge_ts(self, kind: str) -> Optional[str]:
        """Get the timestamp of the most recent nudge of a kind."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT created_ts FROM nudges WHERE kind=? "
                "ORDER BY created_ts DESC LIMIT 1",
                (kind,),
            )
            row = await cur.fetchone()
            return row[0] if row else None

    async def insert_reflection(
        self,
        kind: str,
        content: str,
        meta: Optional[Dict[str, Any]],
        ts: str,
        pinned: bool = False,
    ) -> int:
        """Insert a reflection entry and return its ID."""
        meta_json = json.dumps(meta) if meta else None
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "INSERT INTO reflections(kind, content, meta, ts, pinned) "
                "VALUES(?,?,?,?,?)",
                (kind, content, meta_json, ts, 1 if pinned else 0),
            )
            await db.commit()
            return cur.lastrowid

    async def latest_reflection(self, kind: str) -> Optional[Dict]:
        """Get the most recent reflection of a given kind."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT id, kind, content, meta, ts, pinned "
                "FROM reflections WHERE kind=? ORDER BY ts DESC LIMIT 1",
                (kind,),
            )
            row = await cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "kind": row[1],
                "content": row[2],
                "meta": json.loads(row[3]) if row[3] else None,
                "ts": row[4],
                "pinned": bool(row[5]),
            }

    async def persist_embeddings_for(
        self,
        memory_id: int,
        sources: Optional[List[str]] = None
    ) -> int:
        """
        Persist embeddings for a memory (post-consent promotion)
        
        Use this to generate and store embeddings for a memory that was
        previously blocked by ask_before_store or other consent gates.
        
        Args:
            memory_id: Memory ID to generate embeddings for
            sources: List of sources to embed ('summary', 'full').
                    If None, uses rule's embed setting.
                    
        Returns:
            Number of embeddings created
        """
        embed_engine, vec_store = _get_embedding_components(self.db_path)
        if not (embed_engine and vec_store):
            return 0
        
        # Load memory
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT kind, key, value, summary FROM memories "
                "WHERE id=?",
                (memory_id,)
            )
            row = await cursor.fetchone()
        
        if not row:
            logger.warning(f"Memory {memory_id} not found")
            return 0
        
        kind, key, value, summary = row
        
        # Re-evaluate rules (consent may have changed)
        memory_dict = {"kind": kind, "key": key, "value": value}
        evaluated = _rules_engine.evaluate(memory_dict)
        
        embed_mode = evaluated.get("embed", "summary")
        if embed_mode == "none":
            return 0
        
        # Determine sources to embed
        if sources is None:
            if embed_mode == "both":
                sources = ["summary", "full"]
            elif embed_mode == "summary":
                sources = ["summary"] if summary else []
            else:  # full
                sources = ["full"]
        
        # Generate embeddings
        texts_to_embed = []
        sources_to_store = []
        
        for src in sources:
            if src == "summary" and summary:
                texts_to_embed.append(summary)
                sources_to_store.append("summary")
            elif src == "full":
                texts_to_embed.append(value)
                sources_to_store.append("full")
        
        if not texts_to_embed:
            return 0
        
        try:
            vecs = embed_engine.embed_texts(texts_to_embed)
            cfg = embed_engine.config
            
            # Phase 2d+: Record consent for embeddings
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT OR IGNORE INTO memory_consent "
                    "(memory_id, source) VALUES (?, ?)",
                    (memory_id, "persist_embeddings_for")
                )
                await db.commit()
            
            for src, vec in zip(sources_to_store, vecs):
                vec_store.upsert(
                    memory_id, vec, src, cfg.provider, cfg.model
                )
            
            logger.info(
                f"Persisted {len(vecs)} embedding(s) for memory {memory_id}"
            )
            return len(vecs)
        except Exception as e:
            logger.error(f"Failed to persist embeddings: {e}")
            return 0
    
    async def reembed_memory(
        self,
        memory_id: int,
        sources: Optional[List[str]] = None
    ) -> int:
        """
        Re-generate embeddings for a memory (e.g., after summary change)
        
        Deletes existing embeddings and creates fresh ones based on
        current content. Transactional: either all succeed or none.
        
        Args:
            memory_id: Memory ID to re-embed
            sources: List of sources to re-embed. If None, defaults to
                    existing sources for this memory (to avoid dropping).
                    
        Returns:
            Number of embeddings created
        """
        embed_engine, vec_store = _get_embedding_components(self.db_path)
        if not (embed_engine and vec_store):
            return 0
        
        # Phase 2d+: If sources not specified, default to existing sources
        if sources is None:
            import sqlite3
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                cursor = conn.execute(
                    "SELECT DISTINCT source FROM memory_embeddings "
                    "WHERE memory_id=?",
                    (memory_id,)
                )
                rows = cursor.fetchall()
                if rows:
                    sources = [row[0] for row in rows]
                # If no existing embeddings, sources remains None
                # and persist_embeddings_for will use rule defaults
        
        # Delete existing embeddings
        vec_store.delete_for_memory(memory_id)
        
        # Re-create embeddings
        return await self.persist_embeddings_for(memory_id, sources)

    async def close(self) -> None:
        """Checkpoint and clean up WAL files and global resources."""
        # Clean up global embedding/vector store instances
        global _embedding_engine, _vector_store
        _embedding_engine = None
        _vector_store = None
        
        # Checkpoint WAL files to ensure database is clean
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
        except Exception as e:
            logger.debug(f"WAL checkpoint failed: {e}")
        
        # Try alternate checkpoint method from API bridge
        try:
            import sys
            import os
            sys.path.insert(
                0,
                os.path.join(
                    os.path.dirname(__file__), "..", "..",
                    "bartholomew_api_bridge_v0_1", "services", "api"
                )
            )
            from db_ctx import wal_checkpoint_truncate
            wal_checkpoint_truncate(self.db_path)
        except Exception:
            # Best-effort cleanup
            pass
