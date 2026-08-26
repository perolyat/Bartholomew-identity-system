from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import numpy as np

from bartholomew.kernel import encryption_engine as _encryption_module
from bartholomew.kernel.chunking_engine import get_chunking_engine
from bartholomew.kernel.fts_client import reindex_memory_fts_async, remove_memory_fts_async
from bartholomew.kernel.memory.privacy_guard import (
    get_consent_handler,
    is_sensitive,
    request_permission_to_store,
)
from bartholomew.kernel.memory_rules import _rules_engine
from bartholomew.kernel.policy import can_index
from bartholomew.kernel.redaction_engine import apply_redaction
from bartholomew.kernel.summarization_engine import _summarization_engine

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
        config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "kernel.yaml")
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = yaml.safe_load(f)
                if config and "fts" in config:
                    return config["fts"].get("index_mode", "summary_preferred")
    except Exception as e:
        logger.debug(f"Could not load FTS index mode config: {e}")

    return "summary_preferred"


def compute_governed_index_text(
    kind: str,
    key: str,
    plaintext_value: str,
    plaintext_summary: str | None,
    ts: str,
) -> str | None:
    """
    Recompute the governed FTS index text for already-stored plaintext.

    Re-runs the same rule evaluation, policy gate, redaction, and
    summary-preference selection that upsert_memory() applies when it
    first writes a memory. Used by callers that don't have upsert_memory()'s
    own in-flight `evaluated`/`redacted_value`/`summary` locals -- self-heal
    (repairing a memory whose FTS entry is missing or stale) and
    scripts/backfill_fts.py (rebuilding the whole index from stored,
    decrypted content) -- so neither one carries its own separate copy of
    this logic that could quietly drift from upsert_memory()'s.

    Args:
        kind: Memory kind
        key: Memory key
        plaintext_value: Decrypted, already-redacted stored value (this is
            what upsert_memory() persists as `memories.value` -- redaction
            has already happened once by the time anything is stored, so
            it is not reapplied here against the original raw content;
            redact_strategy is still applied for parity with upsert_memory,
            which is a no-op unless the redaction pattern was changed since
            the memory was first written)
        plaintext_summary: Decrypted stored summary, or None
        ts: Memory timestamp

    Returns:
        The text to index, or None if the memory must not be indexed
        (fts_index/policy denies it, or there is no indexable text).
    """
    evaluated = _rules_engine.evaluate(
        {"kind": kind, "key": key, "value": plaintext_value, "ts": ts},
    )

    if not evaluated.get("fts_index", True) or not can_index(evaluated):
        return None

    redacted_value = plaintext_value
    if evaluated.get("redact_strategy"):
        redacted_value = apply_redaction(plaintext_value, evaluated)

    fts_index_mode = evaluated.get("fts_index_mode", _load_fts_index_mode())
    index_text = (
        plaintext_summary
        if plaintext_summary and fts_index_mode == "summary_preferred"
        else redacted_value
    )

    if not index_text or not index_text.strip():
        return None

    return index_text


@dataclass
class StoreResult:
    """Result of a memory storage operation"""

    memory_id: int | None = None
    stored: bool = False
    ephemeral_embeddings: list[tuple[str, np.ndarray]] = field(default_factory=list)
    created_or_updated: str = "created"  # "created" or "updated"

    outcome: str = "stored"
    """
    Why the write ended as it did. The governed write path knows this
    directly; callers previously had to reconstruct it by diffing the
    pending-consent inbox before and after the call, which is brittle under
    concurrency and silently wrong once the inbox exceeds the scan limit.

    One of:

    * ``stored``              -- written.
    * ``queued_for_consent``  -- governance requires a human decision; the
      value is in ``pending_sensitive_writes`` and was NOT written.
    * ``refused``             -- governance rejected the value outright
      (``never_store``, or an interactive handler declining). Not storable.
    * ``precondition_failed`` -- a conditional write whose
      ``expected_memory_id`` no longer matched; nothing was written.

    `stored` remains the authoritative boolean and is unchanged for every
    existing caller; this only names *which* not-stored case occurred.
    """


# Phase 2d: Lazy imports for embeddings (optional feature)
_embedding_engine = None
_vector_store = None
_summary_fallback_warned = False  # Global flag to warn once


def _get_embedding_components(db_path: str):
    """
    Lazy load embedding components

    Returns tuple of (embedding_engine, vector_store) or (None, None)
    if embeddings not enabled or imports fail.

    Important: the vector store must always point at the same SQLite
    database file as the owning MemoryStore, so we recreate the cached
    instance when the db_path changes.
    """
    global _embedding_engine, _vector_store

    # Check if embeddings are enabled
    import os

    if not os.getenv("BARTHO_EMBED_ENABLED"):
        return None, None

    try:
        from bartholomew.kernel.embedding_engine import get_embedding_engine
        from bartholomew.kernel.vector_store import VectorStore

        if _embedding_engine is None:
            _embedding_engine = get_embedding_engine()

        # Recreate vector store if it doesn't exist yet or is bound to a
        # different database path (tests use many temp DB files).
        if _vector_store is None or getattr(_vector_store, "db_path", None) != db_path:
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

-- Phase 2f: Memory chunks for long content
CREATE TABLE IF NOT EXISTS memory_chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER NOT NULL,
  seq INTEGER NOT NULL,           -- Chunk sequence number (0, 1, 2, ...)
  token_start INTEGER NOT NULL,   -- Token offset start in original text
  token_end INTEGER NOT NULL,     -- Token offset end in original text
  text TEXT NOT NULL,             -- Chunk text content (redacted, pre-encryption)
  FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE,
  UNIQUE(memory_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_chunks_memory ON memory_chunks(memory_id);

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

-- Consent-handler fix (2026-08) + S1.2 (2026-08): a shared pending-write
-- inbox for content upsert_memory() would otherwise silently and
-- permanently discard with no record anywhere. Two independent gates feed
-- it, distinguished by `reason`:
--   'privacy_guard' -- privacy_guard.is_sensitive()'s keyword check, when
--     no consent handler is registered (the real headless/API case --
--     distinct from an interactive handler explicitly declining, which is
--     never queued here).
--   'rule_consent' -- memory_rules.yaml's ask_before_store category
--     (requires_consent=true); `privacy_class` records the matched rule's
--     classification for display. Unlike never_store (allow_store=false),
--     which stays an unconditional hard block with no promotion path.
-- Preserves the full original write request so a human can review and
-- approve/deny it later via the consent inbox.
CREATE TABLE IF NOT EXISTS pending_sensitive_writes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  ts TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','denied')),
  resolved_at TEXT,
  resolved_memory_id INTEGER,
  reason TEXT NOT NULL DEFAULT 'privacy_guard',
  privacy_class TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_sensitive_writes_status
  ON pending_sensitive_writes(status, id);

CREATE TABLE IF NOT EXISTS system_flags (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


@dataclass
class CorrectionOutcome:
    """
    What happened to a user's attempt to correct a stored memory.

    `stored=False` on its own is ambiguous -- governance either queued the new
    value for consent (recoverable, waiting in the pending inbox, old value
    still in place) or refused it outright (never storable). Callers must be
    able to tell those apart to say anything truthful about it.
    """

    stored: bool
    memory_id: int | None = None
    queued_for_consent: bool = False
    target_changed: bool = False
    """The record was deleted or replaced while this correction was in
    flight, so the conditional write did not land. Nothing was written and
    nothing was removed: whatever is at that key now is another writer's, or
    the user's deletion, and it stands. Distinct from a governance refusal --
    nothing was rejected, the target simply is no longer the record the user
    was correcting."""


class MemoryStore:
    def __init__(self, db_path: str, blocking_executor: Any | None = None) -> None:
        self.db_path = db_path
        # Optional bartholomew.kernel.blocking_executor.SingleWorkerExecutor
        # (Phase B stage B2; see docs/B2_EVENT_LOOP_ISOLATION.md). When
        # None (the default), the two synchronous, blocking call sites
        # this powers (FTS schema init below, and _handle_chunking()/
        # reembed_memory()) fall back to a one-off asyncio.to_thread() --
        # see run_off_loop()'s docstring. Fully optional, matching this
        # codebase's existing pattern for other injected resources.
        self._blocking_executor = blocking_executor

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)

            # Phase 2c: Migrate existing databases to add summary column
            cursor = await db.execute("PRAGMA table_info(memories)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]

            if "summary" not in column_names:
                await db.execute("ALTER TABLE memories ADD COLUMN summary TEXT")
                logger.info("Migrated memories table: added summary column")

            # S1.2: migrate existing pending_sensitive_writes tables to add
            # the reason/privacy_class columns (CREATE TABLE IF NOT EXISTS
            # above is a no-op against an existing table).
            cursor = await db.execute("PRAGMA table_info(pending_sensitive_writes)")
            psw_columns = [col[1] for col in await cursor.fetchall()]

            if "reason" not in psw_columns:
                await db.execute(
                    "ALTER TABLE pending_sensitive_writes "
                    "ADD COLUMN reason TEXT NOT NULL DEFAULT 'privacy_guard'",
                )
                logger.info("Migrated pending_sensitive_writes table: added reason column")

            if "privacy_class" not in psw_columns:
                await db.execute(
                    "ALTER TABLE pending_sensitive_writes ADD COLUMN privacy_class TEXT",
                )
                logger.info("Migrated pending_sensitive_writes table: added privacy_class column")

            # Seed parking_brake flag if not exists
            cursor = await db.execute("SELECT 1 FROM system_flags WHERE key = 'parking_brake'")
            if not await cursor.fetchone():
                import time

                await db.execute(
                    "INSERT INTO system_flags(key, value, updated_at) VALUES (?, ?, ?)",
                    (
                        "parking_brake",
                        json.dumps({"engaged": False, "scopes": []}),
                        str(int(time.time())),
                    ),
                )
                logger.info("Seeded parking_brake system flag")

            await db.commit()

        # Phase 2e: Initialize FTS5 tables and triggers. Runs off the event
        # loop (Phase B stage B2) since FTSClient is fully synchronous and
        # this is awaited directly from KernelDaemon.start()'s first step.
        try:
            from bartholomew.kernel.blocking_executor import run_off_loop
            from bartholomew.kernel.fts_client import FTSClient

            fts = FTSClient(self.db_path)

            def _init_fts_schema() -> bool:
                # auto_heal=False: skip init_schema()'s own internal
                # migrate_schema() call here -- migrate_schema() never
                # repairs unindexed memories itself (it fails closed and
                # only logs; see its docstring), so running it here would
                # just log a "needs backfill" warning that the governance-
                # aware heal below immediately resolves anyway. Called
                # explicitly, once, after that heal (see below) instead,
                # so its one real repair action -- removing orphaned
                # memory_fts_map rows, which is governance-neutral -- runs
                # after the governance-aware heal has had its turn.
                fts.init_schema(auto_heal=False)
                # Phase 2f: Initialize chunk FTS schema if chunking is enabled
                chunking_engine = get_chunking_engine()
                if chunking_engine.enabled:
                    fts.init_chunk_schema()
                    return True
                return False

            chunk_schema_initialized = await run_off_loop(
                _init_fts_schema,
                executor=self._blocking_executor,
            )
            logger.info("FTS5 schema initialized")
            if chunk_schema_initialized:
                logger.info("Chunk FTS5 schema initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize FTS5 schema: {e}")

        # Self-heal: index any memory a bypass write (or a database
        # migrated from before the last_index_text column existed) left
        # unindexed. Best-effort/non-fatal on failure, matching the
        # FTS-schema-init error handling directly above -- a healing
        # failure must never block startup.
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await self._heal_unindexed_memories(db)
        except Exception as e:
            logger.warning(f"Failed to self-heal FTS index: {e}")

        # Safety net: migrate_schema()'s rowid-consistency check, run last
        # so the governance-aware heal above always gets first crack at
        # indexing anything it can. Safe to call unconditionally --
        # migrate_schema() never repairs unindexed memories itself (it
        # fails closed: reports via a log warning and leaves the index
        # as-is, since it can't tell a bypass write apart from content
        # policy correctly excludes from FTS -- see its own docstring for
        # the full reasoning and the empirical corruption this replaced).
        # Its only repair action is deleting memory_fts_map rows that
        # reference a memory_id no longer in `memories`, which is
        # governance-neutral bookkeeping cleanup, not a content decision.
        try:
            from bartholomew.kernel.blocking_executor import run_off_loop
            from bartholomew.kernel.fts_client import FTSClient

            await run_off_loop(
                FTSClient(self.db_path).migrate_schema,
                executor=self._blocking_executor,
            )
        except Exception as e:
            logger.warning(f"Failed to run FTS rowid-consistency migration: {e}")

    async def _heal_unindexed_memories(self, db: aiosqlite.Connection) -> None:
        """
        Index every memory with no memory_fts_map entry, or whose
        last_index_text is NULL -- i.e. anything that bypassed
        upsert_memory() (a direct SQL write, an older code path, a
        database migrated from before this architecture existed) and so
        never went through the single-writer primitive.

        migrate_schema()'s own LEFT JOIN check (unchanged, trigger-
        independent, called explicitly at the end of init()) detects the
        same unindexed rows, but deliberately does not repair them itself
        -- it can't tell a genuine bypass write apart from content policy
        correctly excludes from FTS, so it only reports (see its
        docstring). This method is what actually does that repair,
        governance-aware: real rule evaluation, redaction, and policy
        gating per memory, via compute_governed_index_text() -- so a
        bypass write gets properly redacted, policy-gated indexing, and a
        policy-denied memory is correctly left alone.
        """
        cursor = await db.execute(
            """
            SELECT m.id, m.kind, m.key, m.value, m.summary, m.ts
            FROM memories m
            LEFT JOIN memory_fts_map fm ON fm.memory_id = m.id
            WHERE fm.memory_id IS NULL OR fm.last_index_text IS NULL
            """,
        )
        rows = await cursor.fetchall()
        if not rows:
            return

        healed = 0
        for memory_id, kind, key, value, summary, ts in rows:
            try:
                plaintext_value = _encryption_module._encryption_engine.try_decrypt_if_envelope(
                    value,
                )
                plaintext_summary = None
                if summary:
                    plaintext_summary = (
                        _encryption_module._encryption_engine.try_decrypt_if_envelope(summary)
                    )

                index_text = compute_governed_index_text(
                    kind,
                    key,
                    plaintext_value,
                    plaintext_summary,
                    ts,
                )
                if index_text is None:
                    continue

                await reindex_memory_fts_async(db, memory_id, index_text)
                healed += 1
            except Exception as e:
                logger.warning(
                    f"Failed to self-heal FTS index for memory {memory_id} ({kind}/{key}): {e}",
                )

        if healed:
            await db.commit()
            logger.info(f"Self-healed FTS index for {healed} memory(ies)")

    async def upsert_memory(
        self,
        kind: str,
        key: str,
        value: str,
        ts: str,
        *,
        skip_privacy_guard: bool = False,
        skip_rule_consent: bool = False,
        summary: str | None = None,
        expected_memory_id: int | None = None,
    ) -> StoreResult:
        """
        `expected_memory_id` makes this a conditional write (compare-and-swap).

        When supplied, the row currently at `(kind, key)` must still have that
        id or nothing is written and the result is
        `outcome="precondition_failed"`. The check runs inside the same
        transaction as the write, under `BEGIN IMMEDIATE`, so no other writer
        can slip between the check and the write.

        This exists because a correction must apply to the *exact record the
        user was looking at*. Checking existence beforehand and compensating
        afterwards cannot be made safe: between the two, another writer may
        delete and recreate the row, and a post-hoc "the id changed" test
        cannot distinguish our own accidental resurrection from someone
        else's newer legitimate write -- the classic ABA problem. Deleting on
        that evidence destroys real data. Refusing the write up front does
        not. Omitted (the default) this parameter changes nothing.
        """
        # Rule evaluation: check governance rules first
        memory_dict = {
            "kind": kind,
            "key": key,
            "value": value,
            "ts": ts,
        }
        evaluated = _rules_engine.evaluate(memory_dict)

        # never_store (allow_store=false): unconditional hard block, no
        # promotion path, ever -- not affected by skip_rule_consent.
        if not evaluated.get("allow_store", True):
            print(f"[Bartholomew] Memory blocked by governance rules: {kind}/{key}")
            return StoreResult(stored=False, outcome="refused")

        # S1.2: ask_before_store (requires_consent=true) -- unlike
        # never_store above, memory_rules.py's should_store() docstring has
        # always said this should use "a separate promotion path" rather
        # than being discarded outright. Queue it in the same
        # pending_sensitive_writes inbox the consent-handler fix built for
        # privacy_guard's gate (reason='rule_consent'), instead of losing it
        # with no record. skip_rule_consent=True (used only by
        # approve_pending_sensitive_write) bypasses this so re-running the
        # pipeline on approval doesn't re-trip the gate and re-queue itself.
        if evaluated.get("requires_consent", False) and not skip_rule_consent:
            pending_id = await self.record_pending_write(
                "rule_consent",
                kind,
                key,
                value,
                ts,
                privacy_class=evaluated.get("privacy_class"),
                evaluated=evaluated,
            )
            print(
                f"[Bartholomew] Memory requires consent, queued for review "
                f"(pending_id={pending_id}); not stored yet: {kind}/{key}",
            )
            return StoreResult(stored=False, outcome="queued_for_consent")

        # Apply redaction if required by rules (Phase 2a)
        redacted_value = value
        if evaluated.get("redact_strategy"):
            redacted_value = apply_redaction(value, evaluated)

        # Phase 2c: Generate summary if required (before encryption).
        #
        # S5.1: `summary` may now be caller-supplied (e.g. a competency
        # record's own plain-text rendering of its structured content --
        # see bartholomew/kernel/competency.py's `to_summary_text()`).
        # `_summarization_engine.summarize()` is a naive sentence-extractor
        # never meant to run over structured (e.g. JSON) content, so a
        # caller-supplied summary is used as-is instead of being
        # auto-generated -- but it is NOT exempt from the governance this
        # method already applies to an auto-generated one; existing callers
        # (none of which pass this argument) see no behaviour change.
        summary_mode = evaluated.get("summary_mode", "summary_also")
        caller_supplied_summary = summary is not None

        # Authoritative "no summary of this content at all" policy signals
        # -- full_always is should_summarize()'s own rule (checked below for
        # the auto-generation case); an explicit summarize:false rule is
        # honoured the same way here. Both must hold regardless of who
        # produced the summary text -- a caller-supplied one must not
        # silently override them.
        if caller_supplied_summary and (
            summary_mode == "full_always" or evaluated.get("summarize") is False
        ):
            summary = None
            caller_supplied_summary = False

        if summary is None and _summarization_engine.should_summarize(
            evaluated,
            redacted_value,
            kind,
        ):
            summary = _summarization_engine.summarize(redacted_value)

        # A caller-supplied summary isn't guaranteed to derive from
        # `redacted_value` the way an auto-generated one always does (it's
        # built from the caller's own pre-redaction representation) --
        # redact it the same way before it can reach storage or FTS, so
        # supplying a ready-made summary can never bypass redaction.
        # Scoped to the caller-supplied case only: an auto-generated
        # summary is already redaction-safe by construction (summarize()
        # runs over redacted_value), so this leaves that path byte-for-byte
        # unchanged.
        if caller_supplied_summary and summary is not None and evaluated.get("redact_strategy"):
            summary = apply_redaction(summary, evaluated)

        # Plaintext, fully-governed summary (redacted, policy-gated) --
        # captured *before* the summary_only substitution below can clear
        # `summary`, so _handle_embeddings() further down still receives it
        # even when summary_only moves this same text into `redacted_value`
        # and nulls the `summary` column. Capturing this after that
        # substitution (as an earlier revision of this fix did) left
        # `resolved_summary` None for the summary_only case, so embeddings
        # fell back to independently re-summarising the original full
        # value -- inconsistent with what was actually stored and
        # FTS-indexed, and capable of encoding content summary_only exists
        # to remove from the embedding, generically for any caller-supplied
        # summary, not just a competency one.
        resolved_summary = summary

        # Handle summary_only mode: whichever source produced `summary`
        # (caller-supplied or just auto-generated above), only the summary
        # is retained as the stored value -- never a separate summary field.
        if summary is not None and summary_mode == "summary_only":
            redacted_value = summary
            summary = None  # Don't store separate summary

        # Phase 2e: Compute FTS index text (before encryption)
        # NEVER index raw/unredacted/blocked content
        # Use summary if available and preferred, otherwise use redacted value
        fts_index_mode = evaluated.get("fts_index_mode", _load_fts_index_mode())
        index_text = (
            summary if summary and fts_index_mode == "summary_preferred" else redacted_value
        )

        # Apply encryption if required by rules (Phase 2b)
        # Start with redacted_value, replace with encrypted if needed
        value_to_store = redacted_value
        cipher = _encryption_module._encryption_engine.encrypt_for_policy(
            redacted_value,
            evaluated,
            {"kind": kind, "key": key, "ts": ts},
        )
        if cipher is not None:
            value_to_store = cipher

        # Encrypt summary if present and encryption is enabled
        cipher_summary = None
        if summary is not None:
            cipher_summary = _encryption_module._encryption_engine.encrypt_for_policy(
                summary,
                evaluated,
                {"kind": kind, "key": key + "::summary", "ts": ts},
            )
            if cipher_summary is not None:
                summary = cipher_summary

        # Privacy guard fallback: check for sensitive content.
        #
        # `request_permission_to_store` is a coroutine and `upsert_memory` is
        # itself `async def`, so this is simply awaited. The previous code
        # called `asyncio.run(...)` here with a `nest_asyncio` fallback for
        # "event loop is already running": because this method only ever runs
        # inside a running loop, `asyncio.run()` raised RuntimeError *every*
        # time, so the fallback branch was always taken -- and `nest_asyncio`
        # is not a declared dependency, so any sensitive-content write raised
        # ModuleNotFoundError even when the user approved it. Awaiting removes
        # both the guaranteed crash and the undeclared dependency; consent
        # stays fail-closed (request_permission_to_store returns False when no
        # consent handler is registered).
        #
        # `skip_privacy_guard` bypasses this block entirely -- used only by
        # approve_pending_sensitive_write() to (re-)store content a human has
        # already reviewed via the consent inbox, without re-tripping this
        # same gate and re-queuing itself.
        #
        # Consent-handler fix (2026-08): `chat.py` registers a real terminal
        # prompt for interactive CLI use -- by design, headless callers (the
        # API/daemon) leave no handler registered and fail closed instead.
        # That's the correct default for a synchronous stdin prompt, but it
        # previously meant sensitive content from the live product (API
        # requests, skills, etc.) was silently and permanently discarded --
        # `stored=False` with no record anywhere. When no handler is
        # registered (the genuine headless case -- NOT an interactive
        # handler explicitly declining, which must never be second-guessed
        # or re-queued), the content is preserved in pending_sensitive_writes
        # for later human review instead of being dropped.
        # `kind` lets the guard skip schema key names for kinds registered as
        # structured, so a record is not flagged on the shape of its schema
        # rather than its content (see privacy_guard's module docstring).
        # Values are always scanned in full, and an unregistered kind keeps
        # the conservative raw scan -- passing `kind` can only ever narrow
        # false positives, never open a bypass.
        if not skip_privacy_guard and is_sensitive(value, kind=kind):
            if get_consent_handler() is None:
                pending_id = await self.record_pending_sensitive_write(
                    kind,
                    key,
                    value,
                    ts,
                    evaluated=evaluated,
                )
                print(
                    f"[Bartholomew] Sensitive content queued for review "
                    f"(pending_id={pending_id}); not stored yet.",
                )
                return StoreResult(stored=False, outcome="queued_for_consent")

            allowed = await request_permission_to_store(value)

            if not allowed:
                print("[Bartholomew] OK, I won't store that kernel memory.")
                return StoreResult(stored=False, outcome="refused")

        # Prepare result object
        result = StoreResult()

        async with aiosqlite.connect(self.db_path) as db:
            if expected_memory_id is not None:
                # BEGIN IMMEDIATE takes the write lock now, so the identity
                # check below and the write that follows are one atomic step
                # against any other writer. A deferred transaction would only
                # take the lock at the INSERT, leaving a window in between --
                # which is precisely the race this parameter exists to close.
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    "SELECT id FROM memories WHERE kind=? AND key=?",
                    (kind, key),
                )
                current = await cursor.fetchone()
                if current is None or current[0] != expected_memory_id:
                    # The record the caller meant is no longer the record
                    # here: deleted, or replaced by a newer write. Either way
                    # this write must not land. Nothing was changed, so there
                    # is nothing to compensate.
                    await db.rollback()
                    return StoreResult(stored=False, outcome="precondition_failed")

            await db.execute(
                "INSERT INTO memories(kind,key,value,summary,ts) "
                "VALUES(?,?,?,?,?) "
                "ON CONFLICT(kind,key) DO UPDATE SET "
                "value=excluded.value, summary=excluded.summary, "
                "ts=excluded.ts",
                (kind, key, value_to_store, summary, ts),
            )

            # Get memory_id for result
            cursor = await db.execute("SELECT id FROM memories WHERE kind=? AND key=?", (kind, key))
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
                    logger.info(f"FTS indexing blocked by policy for memory {result.memory_id}")

                if fts_allowed:
                    # Single-writer primitive: deletes exactly what
                    # memory_fts_map recorded as last indexed (not a guess,
                    # not empty strings), inserts index_text, and updates
                    # the tracking row -- see reindex_memory_fts_async()'s
                    # docstring in fts_client.py for why this is the only
                    # safe way to mutate memory_fts.
                    await reindex_memory_fts_async(db, result.memory_id, index_text)
                    logger.debug(f"FTS index updated in-Tx for memory {result.memory_id}")
                else:
                    # Policy denies indexing: remove from FTS in same Tx
                    await remove_memory_fts_async(db, result.memory_id)
                    logger.debug(
                        f"FTS index removed in-Tx for memory {result.memory_id} (policy denied)",
                    )

            # Commit transaction (includes base row + FTS changes)
            await db.commit()

        # Phase 2f: Handle chunking OUTSIDE async context (after main Tx)
        # This creates chunks for long content and indexes them in FTS
        await self._handle_chunking(
            result=result,
            redacted_value=redacted_value,
            kind=kind,
            evaluated=evaluated,
        )

        # Phase 2d: Generate and persist embeddings OUTSIDE async context
        # This avoids database locking issues on Windows when VectorStore
        # uses synchronous sqlite3 while aiosqlite connection was open.
        await self._handle_embeddings(
            result=result,
            memory_dict=memory_dict,
            evaluated=evaluated,
            kind=kind,
            resolved_summary=resolved_summary,
        )

        return result

    async def _handle_embeddings(
        self,
        result: StoreResult,
        memory_dict: dict,
        evaluated: dict,
        kind: str,
        resolved_summary: str | None = None,
    ) -> None:
        """
        Handle embedding generation and persistence OUTSIDE async context.

        This method is called after the main database transaction is committed
        to avoid database locking issues on Windows when VectorStore uses
        synchronous sqlite3 connections.

        Args:
            result: StoreResult object to populate with embeddings
            memory_dict: Original memory data (for embedding source text)
            evaluated: Evaluated rules metadata
            kind: Memory kind
            resolved_summary: The plaintext, already policy-gated and
                redacted summary upsert_memory() resolved for this write (S5.1;
                None for existing callers -- unchanged default). Preferred
                over re-deriving a summary here from scratch when present,
                generically for any caller-supplied summary, not just a
                competency one.
        """
        global _summary_fallback_warned

        if not result.memory_id:
            return

        # Get embedding components (this may create VectorStore schema --
        # real synchronous sqlite3 I/O; VectorStore has no async methods at
        # all, confirmed by direct read, so this and every other VectorStore
        # call below is routed off the event loop, Phase B stage B8).
        from .blocking_executor import run_off_loop

        embed_engine, vec_store = await run_off_loop(
            _get_embedding_components,
            self.db_path,
            executor=self._blocking_executor,
        )
        if not embed_engine or not vec_store:
            return

        # Check if rule allows embedding
        embed_mode = evaluated.get("embed", "summary")

        # Phase 2d+: embed_store defaults to True when embed != 'none'
        if "embed_store" in evaluated:
            embed_store = evaluated["embed_store"]
        else:
            # Default: True if embeddings configured, else False
            embed_store = embed_mode != "none"

        # Apply policy-based indexing guard for embeddings
        if embed_mode != "none" and not can_index(evaluated):
            embed_mode = "none"
            logger.info(
                f"Vector embedding blocked by policy for memory {result.memory_id}",
            )

        if embed_mode == "none":
            return

        # Determine what to embed
        texts_to_embed = []
        sources = []

        # Use ORIGINAL values before encryption for embedding
        orig_value = memory_dict["value"]
        if evaluated.get("redact_strategy"):
            orig_value = apply_redaction(orig_value, evaluated)

        # Check if we have summary. A caller-supplied summary (already
        # policy-gated and redacted by upsert_memory() -- see
        # `resolved_summary`'s docstring above) is preferred over deriving
        # one from scratch here, exactly mirroring upsert_memory()'s own
        # `if summary is None and should_summarize(): ...` precedence so
        # embedding text and stored/FTS text stay consistent.
        if resolved_summary is not None:
            orig_summary = resolved_summary
        elif _summarization_engine.should_summarize(evaluated, orig_value, kind):
            orig_summary = _summarization_engine.summarize(orig_value)
        else:
            orig_summary = None

        # Build texts list with fallback for missing summary
        if embed_mode in ("summary", "both"):
            if orig_summary:
                texts_to_embed.append(orig_summary)
                sources.append("summary")
            else:
                # Phase 2d+: Fallback to redacted content
                if not _summary_fallback_warned:
                    logger.warning(
                        "Summary missing for embedding; using redacted content as fallback",
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

        if not texts_to_embed:
            return

        try:
            # Embed texts
            vecs = embed_engine.embed_texts(texts_to_embed)

            if not embed_store:
                # Compute-only: return as ephemeral (don't persist)
                for src, vec in zip(sources, vecs, strict=False):
                    result.ephemeral_embeddings.append((src, vec))
                logger.debug(
                    f"Computed {len(vecs)} ephemeral embedding(s) (not persisted)",
                )
                return

            # Record consent for embeddings
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT OR IGNORE INTO memory_consent (memory_id, source) VALUES (?, ?)",
                    (result.memory_id, "upsert_memory"),
                )
                await db.commit()

            # Persist embeddings (Phase B stage B8: off the event loop --
            # see the run_off_loop import note above this method's own
            # VectorStore construction).
            cfg = embed_engine.config

            def _upsert_all() -> None:
                for src, vec in zip(sources, vecs, strict=False):
                    vec_store.upsert(result.memory_id, vec, src, cfg.provider, cfg.model)

            await run_off_loop(_upsert_all, executor=self._blocking_executor)
            logger.debug(f"Stored {len(vecs)} embedding(s) for memory {result.memory_id}")
        except Exception as e:
            logger.error(f"Failed to generate/persist embeddings: {e}")

    async def _handle_chunking(
        self,
        result: StoreResult,
        redacted_value: str,
        kind: str,
        evaluated: dict,
    ) -> None:
        """
        Handle chunking for long content OUTSIDE async context.

        Phase 2f: Splits long content into overlapping chunks for better
        FTS and vector indexing. Chunks are stored in memory_chunks table
        and indexed in chunk_fts via database triggers.

        Args:
            result: StoreResult with memory_id
            redacted_value: Redacted content (pre-encryption) to chunk
            kind: Memory kind
            evaluated: Rules evaluation result
        """
        if not result.memory_id:
            return

        # Check if chunking is allowed by policy
        fts_allowed = evaluated.get("fts_index", True)
        if fts_allowed and not can_index(evaluated):
            fts_allowed = False

        if not fts_allowed:
            logger.debug(
                f"Chunking skipped for memory {result.memory_id} (indexing blocked)",
            )
            return

        # Get chunking engine
        chunking_engine = get_chunking_engine()
        if not chunking_engine.enabled:
            return

        # Check if this content should be chunked
        if not chunking_engine.should_chunk(kind, redacted_value):
            return

        # Generate chunks
        chunks = chunking_engine.chunk_text(redacted_value)
        if len(chunks) <= 1:
            # Single chunk = no benefit from chunking
            return

        # Store chunks (synchronously, to avoid Windows locking issues --
        # off the event loop since Phase B stage B2; see
        # docs/B2_EVENT_LOOP_ISOLATION.md).
        import sqlite3

        from .blocking_executor import run_off_loop

        def _store_chunks() -> None:
            conn = None
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute("PRAGMA foreign_keys = ON")

                # Delete existing chunks for this memory (upsert semantics)
                conn.execute(
                    "DELETE FROM memory_chunks WHERE memory_id = ?",
                    (result.memory_id,),
                )

                # Insert new chunks (triggers will update chunk_fts)
                for chunk in chunks:
                    conn.execute(
                        "INSERT INTO memory_chunks "
                        "(memory_id, seq, token_start, token_end, text) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            result.memory_id,
                            chunk.seq,
                            chunk.token_start,
                            chunk.token_end,
                            chunk.text,
                        ),
                    )

                conn.commit()
                logger.info(
                    f"Stored {len(chunks)} chunks for memory {result.memory_id}",
                )
            except Exception as e:
                logger.error(f"Failed to store chunks: {e}")
            finally:
                if conn:
                    conn.close()

        await run_off_loop(_store_chunks, executor=self._blocking_executor)

    # -------------------------------------------------------------------
    # Pending sensitive/consent writes (consent-handler fix, 2026-08; S1.2)
    # -------------------------------------------------------------------

    async def record_pending_write(
        self,
        reason: str,
        kind: str,
        key: str,
        value: str,
        ts: str,
        privacy_class: str | None = None,
        evaluated: dict | None = None,
    ) -> int:
        """
        Preserve a write that upsert_memory() refused to store, so a human
        can review and approve/deny it later via the consent inbox instead
        of it vanishing with no record. `reason` distinguishes which gate
        queued it: 'privacy_guard' (privacy_guard.is_sensitive(), no handler
        registered) or 'rule_consent' (memory_rules.yaml's ask_before_store,
        requires_consent=true). Returns the new pending_sensitive_writes row
        id.

        `evaluated` (the rules-engine metadata dict, when the caller already
        has one) is used to encrypt the payload at rest with the same
        policy upsert_memory()'s own storage path would apply -- content
        matching ask_before_store patterns like password/bank/auth-code
        rules is exactly the content configured for `encrypt: strong`, and
        it would otherwise sit here as a plaintext duplicate while pending
        (Codex review finding). No `evaluated` (or no encrypt policy on it)
        stores as-is, same as upsert_memory() would for that content.
        """
        value_to_store = value
        if evaluated is not None:
            cipher = _encryption_module._encryption_engine.encrypt_for_policy(
                value,
                evaluated,
                {"kind": kind, "key": key, "ts": ts},
            )
            if cipher is not None:
                value_to_store = cipher

        requested_at = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO pending_sensitive_writes "
                "(kind, key, value, ts, requested_at, reason, privacy_class) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, key, value_to_store, ts, requested_at, reason, privacy_class),
            )
            await db.commit()
            return cursor.lastrowid

    async def record_pending_sensitive_write(
        self,
        kind: str,
        key: str,
        value: str,
        ts: str,
        evaluated: dict | None = None,
    ) -> int:
        """
        Preserve a sensitive-content write that upsert_memory() refused to
        store because no consent handler is registered (see upsert_memory()'s
        privacy-guard block). Thin wrapper over record_pending_write() with
        reason='privacy_guard', kept as its own method since it's the
        original, already-tested call site.
        """
        return await self.record_pending_write(
            "privacy_guard",
            kind,
            key,
            value,
            ts,
            evaluated=evaluated,
        )

    async def list_pending_sensitive_writes(self, limit: int = 50) -> list[dict]:
        """
        Pending (not yet approved/denied) writes, newest first -- from
        either gate (see record_pending_write()'s `reason`), one unified
        inbox. Values encrypted at rest (see record_pending_write()) are
        decrypted here for review; non-envelope values pass through
        unchanged.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, kind, key, value, ts, requested_at, status, reason, privacy_class "
                "FROM pending_sensitive_writes WHERE status = 'pending' "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            entries = [dict(row) for row in rows]
            for entry in entries:
                entry["value"] = _encryption_module._encryption_engine.try_decrypt_if_envelope(
                    entry["value"],
                )
            return entries

    async def _refuse_mutation_if_braked(self, refusal: str) -> None:
        """Raise ParkingBrakeEngagedError if the brake is engaged at all.

        The shared execution boundary behind every mutating memory operation.
        Extracted from `_refuse_consent_resolution_if_braked()` (which now
        delegates here) when user-facing Memory Agency needed the identical
        check for editing and forgetting; its docstring carries the full
        rationale, which applies unchanged to those two.

        In short: Governance decision 2026-08-18, "Parking Brake means
        inspect, but do not mutate". Reading memory stays allowed under a
        halt, because seeing what is stored is inspection and a halt must not
        hide it. Enforced here rather than in an API route because brake scope
        is Governance authority and `DECISIONS.md`'s authority-tiers entry
        requires enforcement below the presentation layer -- a client that is
        bypassed, crashes, or is replaced cannot get around this. Gated on the
        brake being engaged at all rather than on one scope, because the
        user's memory belongs to none of the existing subsystem scopes.
        """
        from bartholomew.orchestrator.safety.governance_store import (
            ParkingBrakeEngagedError,
            engaged_state_fail_closed_off_loop,
        )

        state = await engaged_state_fail_closed_off_loop(self.db_path)
        if state.engaged:
            raise ParkingBrakeEngagedError(refusal, scopes=state.scopes)

    async def _refuse_consent_resolution_if_braked(self, action: str) -> None:
        """Refuse to resolve a pending consent request while the brake is on.

        Governance decision 2026-08-18 ("Parking Brake means inspect, but do
        not mutate" -- `DECISIONS.md`; semantics in `COGNITIVE_RUNTIME.md`'s
        "The kill-switch: `ParkingBrake`"). Listing the inbox stays allowed,
        because seeing what is waiting is inspection and a halt must not hide
        it. Approving and denying are both refused, because both mutate: one
        writes a memory, and the other marks the row denied *and clears its
        payload*, which is irreversible.

        Refusal leaves the request `pending`, so it is still resolvable once
        the brake is released -- the halt defers the decision rather than
        deciding it.

        Enforced here rather than in the API route because brake scope is
        Governance authority, not a UI feature: `DECISIONS.md`'s Parking
        Brake authority-tiers entry requires enforcement to sit below the
        presentation layer at the execution boundary, so a client that is
        bypassed, crashes, or is replaced cannot get around it. This method
        is that boundary for consent resolution.

        Gated on the brake being engaged **at all**, not on one scope:
        resolving consent mutates the user's memory, which belongs to none of
        the existing subsystem scopes (`skills`, `sight`, `voice`,
        `scheduler`, `training`), so picking one would be arbitrary.
        """
        await self._refuse_mutation_if_braked(
            f"Parking brake engaged: cannot {action} a pending consent "
            "request while Bartholomew is halted. The request remains "
            "pending and can be resolved once the brake is released.",
        )

    async def approve_pending_sensitive_write(self, pending_id: int) -> StoreResult:
        """
        Store a pending write for real, using its original kind/key/ts, then
        mark it approved. Uses skip_privacy_guard=True and
        skip_rule_consent=True unconditionally (regardless of `reason`) so
        this doesn't re-trip either gate and re-queue itself.

        For reason='rule_consent' rows, also records a memory_consent row
        for the new memory -- ConsentGate/Retriever re-evaluate
        requires_consent at *retrieval* time too and only include a memory
        with a real memory_consent row (bartholomew/kernel/consent_gate.py),
        so without this the memory would be stored but permanently
        unretrievable.

        Refused while the Parking Brake is engaged -- see
        `_refuse_consent_resolution_if_braked()`.
        """
        await self._refuse_consent_resolution_if_braked("approve")

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT kind, key, value, ts, status, reason "
                "FROM pending_sensitive_writes WHERE id = ?",
                (pending_id,),
            )
            row = await cursor.fetchone()

            if row is None:
                raise ValueError(f"No pending sensitive write with id {pending_id}")
            if row["status"] != "pending":
                raise ValueError(
                    f"Pending sensitive write {pending_id} is already {row['status']}",
                )

            # Atomically claim it (compare-and-swap on status) *before* the
            # slower storage work below, in the same connection as the read
            # above. Previously the equivalent UPDATE ran unconditionally
            # after upsert_memory() completed: if a concurrent
            # deny_pending_sensitive_write() won the race between this read
            # and that UPDATE, its 'denied' status would get silently
            # overwritten back to 'approved' with the memory already stored
            # -- a denial that reports success but doesn't stick (Codex
            # review finding). Checking rowcount here means we bail out
            # before ever calling upsert_memory() if we lost that race.
            resolved_at = datetime.now(timezone.utc).isoformat()
            claim = await db.execute(
                "UPDATE pending_sensitive_writes SET status = 'approved', resolved_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (resolved_at, pending_id),
            )
            await db.commit()
            if claim.rowcount == 0:
                raise ValueError(f"Pending sensitive write {pending_id} is already resolved")

        decrypted_value = _encryption_module._encryption_engine.try_decrypt_if_envelope(
            row["value"],
        )
        result = await self.upsert_memory(
            row["kind"],
            row["key"],
            decrypted_value,
            row["ts"],
            skip_privacy_guard=True,
            skip_rule_consent=True,
        )

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE pending_sensitive_writes SET resolved_memory_id = ? WHERE id = ?",
                (result.memory_id, pending_id),
            )
            if row["reason"] == "rule_consent" and result.memory_id is not None:
                # Upsert, not INSERT OR IGNORE: upsert_memory()'s own
                # embedding flow may have already inserted a memory_consent
                # row for this memory_id (source='upsert_memory') if
                # BARTHO_EMBED_ENABLED is set -- INSERT OR IGNORE would
                # silently no-op and leave that source instead of recording
                # that a human explicitly approved this write.
                await db.execute(
                    "INSERT INTO memory_consent (memory_id, source) VALUES (?, ?) "
                    "ON CONFLICT(memory_id) DO UPDATE SET source = excluded.source",
                    (result.memory_id, "consent_approval"),
                )
            await db.commit()

        return result

    async def deny_pending_sensitive_write(self, pending_id: int) -> None:
        """
        Mark a pending sensitive write reviewed and declined, clearing its
        payload as part of the same atomic update. No storage side effect
        -- upsert_memory() already didn't store it. The inbox already hides
        resolved rows, but the pre-consent payload itself (potentially a
        password, bank detail, or other sensitive content) must not remain
        recoverable in the database after an explicit denial just because
        the row itself is kept for audit purposes (Codex review finding).

        Refused while the Parking Brake is engaged -- see
        `_refuse_consent_resolution_if_braked()`. Denial is a mutation too,
        and a destructive one: it clears the payload irreversibly.
        """
        await self._refuse_consent_resolution_if_braked("deny")

        resolved_at = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE pending_sensitive_writes "
                "SET status = 'denied', resolved_at = ?, value = '' "
                "WHERE id = ? AND status = 'pending'",
                (resolved_at, pending_id),
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise ValueError(
                    f"No pending sensitive write with id {pending_id} in 'pending' status",
                )

    async def get_memory(self, kind: str, key: str) -> dict[str, Any] | None:
        """
        Read one memory by its `(kind, key)` identity, or None if absent.

        Read-only, and deliberately *not* a retrieval/relevance path: it is
        an exact-identity lookup on the existing unique `(kind, key)` index,
        for callers that already know precisely which record they mean.

        Added for S5.2's training seam, which must read a record's current
        `revision` before overwriting it so the supersession can be recorded
        (see `docs/S5_2_TRAINING_KNOWLEDGE_ACQUISITION_DESIGN.md` Sec.13.4).
        That read belongs here rather than in the seam: `MemoryStore` is the
        single memory authority, so the alternative -- the seam opening its
        own connection -- would put a second persistence access point next to
        it. This adds no write path and applies no consent gating, so it must
        not be used to surface memories to a user or a model; use the
        `ConsentGate`-filtered retrieval layer for that.

        Values encrypted at rest are decrypted here, matching
        `list_pending_sensitive_writes()`.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, kind, key, value, summary, ts FROM memories WHERE kind = ? AND key = ?",
                (kind, key),
            )
            row = await cursor.fetchone()
            if row is None:
                return None

            entry = dict(row)
            entry["value"] = _encryption_module._encryption_engine.try_decrypt_if_envelope(
                entry["value"],
            )
            if entry.get("summary"):
                entry["summary"] = _encryption_module._encryption_engine.try_decrypt_if_envelope(
                    entry["summary"],
                )
            return entry

    async def get_memories_by_ids(self, memory_ids: list[int]) -> list[dict[str, Any]]:
        """
        Read several memories by row id, in one query. Read-only.

        Added for S5.3's competency reasoning, which needs the *bodies* of
        records the retrieval layer selected: `RetrievedItem` carries a
        snippet, not the stored value, and S5.3 must record each applied
        record's provenance, classification and confidence
        (`docs/S5_3_EXECUTIVE_COMPETENCY_REASONING_DESIGN.md` Decision E.2).

        **This applies no consent gating of its own and must not be used to
        choose what to surface.** It is a body-loader for ids some
        `ConsentGate`-filtered path already decided are permitted — the
        S5.3 seam calls it only with ids the retriever returned. Use the
        retrieval layer for anything that decides *which* memories a caller
        may see.

        Values encrypted at rest are decrypted here, matching
        `get_memory()` and `list_pending_sensitive_writes()`.
        """
        if not memory_ids:
            return []

        placeholders = ",".join("?" for _ in memory_ids)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT id, kind, key, value, summary, ts FROM memories WHERE id IN ({placeholders})",  # noqa: S608 - placeholders are generated, not interpolated user input
                tuple(memory_ids),
            )
            rows = await cursor.fetchall()

        entries: list[dict[str, Any]] = []
        for row in rows:
            entry = dict(row)
            entry["value"] = _encryption_module._encryption_engine.try_decrypt_if_envelope(
                entry["value"],
            )
            if entry.get("summary"):
                entry["summary"] = _encryption_module._encryption_engine.try_decrypt_if_envelope(
                    entry["summary"],
                )
            entries.append(entry)
        return entries

    # How many rows are pulled from SQLite per batch while scanning for a
    # search match. Bounds peak memory: a search over a large store holds one
    # batch plus the requested window, never the whole store.
    _SEARCH_SCAN_BATCH = 500

    def _decorate_entry(self, row: Any) -> dict[str, Any]:
        """
        Turn one memories row into a user-facing entry: decrypt, mark
        readability, and attach governance metadata.

        Governance metadata is derived from the record's *actual* value. When
        the value cannot be decrypted with a key this process holds, it is not
        derived at all: re-running the rules engine over a blanked value would
        classify an unreadable `user.secure` record as `uncategorised` with no
        privacy class, which is a fabricated classification of exactly the
        material most in need of a truthful one. Those fields are reported as
        None with `governance_known=False` instead.
        """
        entry = dict(row)
        entry["value"] = _encryption_module.decrypt_if_envelope(entry["value"])
        if entry.get("summary"):
            entry["summary"] = _encryption_module.decrypt_if_envelope(entry["summary"])

        entry["readable"] = not _encryption_module.is_envelope(entry["value"])
        if not entry["readable"]:
            entry["value"] = ""
            entry["unreadable_reason"] = (
                "Stored encrypted, and cannot be decrypted with the key this "
                "process holds. Set BME_KEY_STANDARD/BME_KEY_STRONG to a "
                "stable key to keep encrypted memories readable across runs."
            )
            entry["governance_known"] = False
            entry["category"] = None
            entry["matched_categories"] = None
            entry["privacy_class"] = None
            entry["recall_policy"] = None
            entry["always_keep"] = None
            return entry

        evaluated = _rules_engine.evaluate(
            {"kind": entry["kind"], "key": entry["key"], "value": entry["value"]},
        )
        categories = evaluated.get("matched_categories") or []
        entry["governance_known"] = True
        entry["category"] = categories[0] if categories else "uncategorised"
        entry["matched_categories"] = categories
        entry["privacy_class"] = evaluated.get("privacy_class")
        entry["recall_policy"] = evaluated.get("recall_policy")
        entry["always_keep"] = "always_keep" in categories
        return entry

    async def list_memories(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        kind: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """
        List stored memories for the user to read, newest first. Read-only.

        Memory Agency: the user is entitled to see what Bartholomew has
        stored about them. That is a different question from what a *model*
        may be shown -- `get_memory()`'s docstring rightly forbids using it to
        surface memories to a user or a model, because it applies no consent
        gating and so must not decide relevance for a retrieval path. This
        method is not a retrieval path and makes no relevance decision: it is
        the subject of the data reading their own record in full, which is the
        one case consent-gated retrieval is not protecting against.

        It lives here rather than in the API layer for the reason
        `get_memory()` gives: `MemoryStore` is the single memory authority,
        and a route opening its own connection would be a second persistence
        access point beside it.

        Search
        ------
        Values may be encrypted at rest, so a SQL `LIKE` would match
        ciphertext rather than text. Matching therefore happens after
        decryption -- but it is applied to the **whole store**, not to one
        page of it. An earlier version paged in SQL first and filtered the
        page afterwards, which reported a real memory as absent whenever it
        sat outside the fetched window, and paginated over the unfiltered set
        so offsets did not address the filtered results at all.

        The scan reads in batches of `_SEARCH_SCAN_BATCH` and keeps only the
        requested window plus a match counter, so peak memory does not grow
        with the store.

        Returned counts are about the result set the caller asked for:

        * `total`       -- matches when searching; rows in the store (after
          any `kind` filter) when not. Either way, the number `offset` and
          `limit` address.
        * `store_total` -- rows before any search filter, always.
        * `has_more`    -- whether rows remain after this window.
        * `filtered`    -- whether a search filter was applied.
        """
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))

        where: list[str] = []
        params: list[Any] = []
        if kind:
            where.append("m.kind = ?")
            params.append(kind)
        clause = f"WHERE {' AND '.join(where)}" if where else ""

        select_sql = (
            "SELECT m.id, m.kind, m.key, m.value, m.summary, m.ts, "
            "c.consent_at AS consent_at, c.source AS consent_source "
            f"FROM memories m LEFT JOIN memory_consent c ON c.memory_id = m.id {clause} "  # noqa: S608 - clause is fixed fragments; values are bound
            "ORDER BY m.ts DESC, m.id DESC LIMIT ? OFFSET ?"
        )

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            count_cursor = await db.execute(
                f"SELECT COUNT(*) AS n FROM memories m {clause}",  # noqa: S608 - as above
                tuple(params),
            )
            count_row = await count_cursor.fetchone()
            store_total = int(count_row["n"]) if count_row else 0

            if not search:
                cursor = await db.execute(select_sql, (*params, limit, offset))
                entries = [self._decorate_entry(r) for r in await cursor.fetchall()]
                return {
                    "entries": entries,
                    "total": store_total,
                    "store_total": store_total,
                    "limit": limit,
                    "offset": offset,
                    "filtered": False,
                    "has_more": offset + len(entries) < store_total,
                }

            # Searching: scan the whole store in batches, decrypt, match, and
            # apply offset/limit to the matches themselves.
            needle = search.casefold()
            matched = 0
            window: list[dict[str, Any]] = []
            scanned = 0
            while scanned < store_total:
                cursor = await db.execute(
                    select_sql,
                    (*params, self._SEARCH_SCAN_BATCH, scanned),
                )
                # Materialised so the scan can count it: fetchall() is typed
                # as an Iterable, and the batch is bounded by _SEARCH_SCAN_BATCH.
                batch = list(await cursor.fetchall())
                if not batch:
                    break
                scanned += len(batch)
                for row in batch:
                    entry = self._decorate_entry(row)
                    # An unreadable value cannot be matched against; only its
                    # key is searchable. Saying otherwise would claim the
                    # search covered content nothing could read.
                    if needle in str(entry["key"]).casefold() or (
                        entry["readable"] and needle in str(entry["value"]).casefold()
                    ):
                        if offset <= matched < offset + limit:
                            window.append(entry)
                        matched += 1

        return {
            "entries": window,
            "total": matched,
            "store_total": store_total,
            "limit": limit,
            "offset": offset,
            "filtered": True,
            "has_more": offset + len(window) < matched,
        }

    async def list_memory_kinds(self) -> list[dict[str, Any]]:
        """Distinct memory kinds with their counts, for a filter control."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT kind, COUNT(*) AS n FROM memories GROUP BY kind ORDER BY n DESC",
            )
            rows = await cursor.fetchall()
        return [{"kind": r["kind"], "count": int(r["n"])} for r in rows]

    async def correct_memory(self, kind: str, key: str, value: str) -> CorrectionOutcome:
        """
        Replace a stored memory's value on the user's instruction.

        Deliberately a thin wrapper over `upsert_memory()` rather than an
        UPDATE of its own: that is the single governed write path, so a
        correction is subject to exactly the governance the original write
        was. A corrected value that trips `never_store` is refused; one that
        trips `ask_before_store` is queued for consent and not stored.

        The write is conditional on the record still being the one the user
        was looking at (`expected_memory_id`), evaluated inside the write's
        own transaction. If the record was deleted or replaced meanwhile,
        nothing is written and the outcome says so.

        That ordering is what makes a user's deletion win: the row is gone,
        the precondition fails, and the correction simply does not land. An
        earlier version instead wrote unconditionally and then deleted the
        row if its id had changed -- which destroyed newer legitimate writes,
        because a changed id does not prove the row present is our own
        resurrection rather than someone else's newer record.

        Refused outright while the Parking Brake is engaged: editing is a
        mutation, and "inspect, but do not mutate" applies.
        """
        await self._refuse_mutation_if_braked(
            "Parking brake engaged: cannot edit a memory while Bartholomew is "
            "halted. Release the brake and try again.",
        )
        existing = await self.get_memory(kind, key)
        if existing is None:
            raise KeyError(f"no memory {kind}/{key}")

        result = await self.upsert_memory(
            kind,
            key,
            value,
            datetime.now(timezone.utc).isoformat(),
            expected_memory_id=existing["id"],
        )

        if result.stored:
            return CorrectionOutcome(stored=True, memory_id=result.memory_id)

        # The write authority reports why directly -- no inbox diffing.
        if result.outcome == "precondition_failed":
            logger.info(
                "Correction to %s/%s did not apply: the record changed while the "
                "correction was in flight. Nothing was written.",
                kind,
                key,
            )
            return CorrectionOutcome(stored=False, target_changed=True)

        return CorrectionOutcome(
            stored=False,
            queued_for_consent=result.outcome == "queued_for_consent",
        )

    async def forget_memory(self, kind: str, key: str) -> bool:
        """
        Delete a memory on the user's explicit instruction. Permanent.

        `delete_memory()` below is the mechanical primitive -- row plus FTS
        index, one transaction -- and stays exactly that. This is the governed
        user-facing action on top of it, refused while the brake is engaged
        for the same reason `correct_memory()` is. Keeping them separate means
        internal maintenance paths can still use the primitive without
        acquiring a brake dependency they should not have.

        There is no undo, and no soft-delete tier exists in this schema to
        route to. Callers must therefore make the action explicit and
        confirmed at the point of use rather than inferring it.
        """
        await self._refuse_mutation_if_braked(
            "Parking brake engaged: cannot forget a memory while Bartholomew "
            "is halted. Release the brake and try again.",
        )
        return await self.delete_memory(kind, key)

    async def list_memories_by_kind(
        self,
        kinds: list[str],
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """
        Read stored memories of the given kinds, newest first. Read-only.

        Added for the Usable POC's slice 2 scheduler drive, which has to scan
        stored date-bearing facts without a search query to drive retrieval
        with: it is looking for *everything currently due*, not for the
        records most similar to something a user just said. That read belongs
        here for the same reason `get_memory()`'s does -- `MemoryStore` is the
        single memory authority, and the alternative is a scheduler drive
        opening its own connection to the memories table.

        **This applies no consent gating of its own and must not be used to
        choose what to surface.** It is the same posture, and the same
        warning, as `get_memory()` and `get_memories_by_ids()` carry. The
        slice 2 drive passes every row it gets back through the existing
        `ConsentGate` before any of it can reach a notification; anything
        else reading kinds in bulk must do the same or use the
        `ConsentGate`-filtered retrieval layer instead.

        Values encrypted at rest are decrypted here, matching `get_memory()`.
        """
        if not kinds:
            return []

        placeholders = ",".join("?" for _ in kinds)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT id, kind, key, value, summary, ts FROM memories "  # noqa: S608 - placeholders are generated, not interpolated user input
                f"WHERE kind IN ({placeholders}) ORDER BY id DESC LIMIT ?",
                (*kinds, limit),
            )
            rows = await cursor.fetchall()

        entries: list[dict[str, Any]] = []
        for row in rows:
            entry = dict(row)
            entry["value"] = _encryption_module._encryption_engine.try_decrypt_if_envelope(
                entry["value"],
            )
            if entry.get("summary"):
                entry["summary"] = _encryption_module._encryption_engine.try_decrypt_if_envelope(
                    entry["summary"],
                )
            entries.append(entry)
        return entries

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
            # foreign_keys is a per-connection setting, not persistent in the
            # DB file -- without it the memory_chunks ON DELETE CASCADE (and
            # any other FK cascade) silently never fires on this connection.
            await db.execute("PRAGMA foreign_keys = ON")

            # Look up memory_id
            cursor = await db.execute("SELECT id FROM memories WHERE kind=? AND key=?", (kind, key))
            row = await cursor.fetchone()

            if not row:
                return False

            memory_id = row[0]

            # Remove FTS index entry before the base row goes away. No
            # trigger does this anymore (single-writer architecture -- see
            # FTS_SCHEMA's docstring in fts_client.py); remove_memory_fts_async()
            # deletes exactly what memory_fts_map recorded as last indexed
            # (a no-op if the memory was never indexed) and clears the
            # tracking row, all in this same transaction.
            await remove_memory_fts_async(db, memory_id)

            # Delete base row.
            await db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))

            await db.commit()
            logger.debug(
                f"Deleted memory {kind}/{key} (id={memory_id}) with FTS cleanup in same Tx",
            )
            return True

    async def create_nudge(
        self,
        kind: str,
        message: str,
        actions: list[dict[str, Any]],
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
        self,
        nudge_id: int,
        status: str,
        acted_ts: str | None = None,
    ) -> None:
        """Update nudge status to acked or dismissed."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE nudges SET status=?, acted_ts=? WHERE id=?",
                (status, acted_ts, nudge_id),
            )
            await db.commit()

    async def list_pending_nudges(self, limit: int = 50) -> list[dict]:
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

    async def nudges_sent_today_count(self, kind: str, start_utc_iso: str, end_utc_iso: str) -> int:
        """Count nudges of a given kind sent today."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM nudges WHERE kind=? AND created_ts BETWEEN ? AND ?",
                (kind, start_utc_iso, end_utc_iso),
            )
            row = await cur.fetchone()
            return int(row[0] or 0)

    async def last_nudge_ts(self, kind: str) -> str | None:
        """Get the timestamp of the most recent nudge of a kind."""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT created_ts FROM nudges WHERE kind=? ORDER BY created_ts DESC LIMIT 1",
                (kind,),
            )
            row = await cur.fetchone()
            return row[0] if row else None

    async def insert_reflection(
        self,
        kind: str,
        content: str,
        meta: dict[str, Any] | None,
        ts: str,
        pinned: bool = False,
    ) -> int:
        """Insert a reflection entry and return its ID."""
        meta_json = json.dumps(meta) if meta else None
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "INSERT INTO reflections(kind, content, meta, ts, pinned) VALUES(?,?,?,?,?)",
                (kind, content, meta_json, ts, 1 if pinned else 0),
            )
            await db.commit()
            return cur.lastrowid

    async def latest_reflection(self, kind: str) -> dict | None:
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
        sources: list[str] | None = None,
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
        # Phase B stage B8: off the event loop -- see _handle_embeddings()'s
        # own identical VectorStore-construction call for the full rationale.
        from .blocking_executor import run_off_loop

        embed_engine, vec_store = await run_off_loop(
            _get_embedding_components,
            self.db_path,
            executor=self._blocking_executor,
        )
        if not (embed_engine and vec_store):
            return 0

        # Load memory
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT kind, key, value, summary FROM memories WHERE id=?",
                (memory_id,),
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
        if embed_mode != "none" and not can_index(evaluated):
            logger.info(
                "Vector embedding blocked by policy for memory %s in persist_embeddings_for",
                memory_id,
            )
            embed_mode = "none"

        if embed_mode == "none":
            return 0

        # Determine sources to embed
        if sources is None:
            if embed_mode == "both":
                sources = ["summary", "full"]
            elif embed_mode == "summary":
                sources = ["summary"]
            else:  # "full"
                sources = ["full"]

        # Generate embeddings (with summary fallback semantics matching upsert_memory)
        texts_to_embed: list[str] = []
        sources_to_store: list[str] = []

        for src in sources:
            if src == "summary":
                if summary:
                    texts_to_embed.append(summary)
                    sources_to_store.append("summary")
                else:
                    # Fallback: use stored value as a summary substitute (trimmed)
                    global _summary_fallback_warned
                    if not _summary_fallback_warned:
                        logger.warning(
                            "Summary missing for persist_embeddings_for; "
                            "using stored value as summary fallback",
                        )
                        _summary_fallback_warned = True

                    fallback_text = (value or "")[:500].strip()
                    if fallback_text:
                        texts_to_embed.append(fallback_text)
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
                    "INSERT OR IGNORE INTO memory_consent (memory_id, source) VALUES (?, ?)",
                    (memory_id, "persist_embeddings_for"),
                )
                await db.commit()

            # Phase B stage B8: off the event loop (see this method's own
            # VectorStore-construction call above).
            def _upsert_all() -> None:
                for src, vec in zip(sources_to_store, vecs, strict=False):
                    vec_store.upsert(memory_id, vec, src, cfg.provider, cfg.model)

            await run_off_loop(_upsert_all, executor=self._blocking_executor)

            logger.info(f"Persisted {len(vecs)} embedding(s) for memory {memory_id}")
            return len(vecs)
        except Exception as e:
            logger.error(f"Failed to persist embeddings: {e}")
            return 0

    async def reembed_memory(self, memory_id: int, sources: list[str] | None = None) -> int:
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
        # Phase B stage B8: off the event loop -- see _handle_embeddings()'s
        # own identical VectorStore-construction call for the full rationale.
        from .blocking_executor import run_off_loop

        embed_engine, vec_store = await run_off_loop(
            _get_embedding_components,
            self.db_path,
            executor=self._blocking_executor,
        )
        if not (embed_engine and vec_store):
            return 0

        # Phase 2d+: If sources not specified, default to existing sources.
        # Runs off the event loop since Phase B stage B2; see
        # docs/B2_EVENT_LOOP_ISOLATION.md.
        if sources is None:
            import sqlite3

            def _existing_sources() -> list[str] | None:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute("PRAGMA foreign_keys = ON")
                    cursor = conn.execute(
                        "SELECT DISTINCT source FROM memory_embeddings WHERE memory_id=?",
                        (memory_id,),
                    )
                    rows = cursor.fetchall()
                    if rows:
                        return [row[0] for row in rows]
                    # If no existing embeddings, remain None and
                    # persist_embeddings_for will use rule defaults.
                    return None

            sources = await run_off_loop(_existing_sources, executor=self._blocking_executor)

        # Delete existing embeddings (Phase B stage B8: off the event loop,
        # same rationale as this method's own VectorStore-construction call
        # above).
        await run_off_loop(
            vec_store.delete_for_memory,
            memory_id,
            executor=self._blocking_executor,
        )

        # Re-create embeddings
        return await self.persist_embeddings_for(memory_id, sources)

    async def close(self, checkpoint: bool = True) -> None:
        """Clean up global resources, and checkpoint WAL files unless
        checkpoint=False.

        checkpoint=False is for a caller that has determined it's unsafe
        to run right now -- e.g. KernelDaemon.stop() when its
        SchedulerStore didn't drain within its bound, meaning a
        background thread may still be writing to this same db_path; see
        SchedulerStore.close()'s docstring.
        """
        # Clean up global embedding/vector store instances
        global _embedding_engine, _vector_store
        _embedding_engine = None
        _vector_store = None

        if not checkpoint:
            return

        # Checkpoint WAL files to ensure database is clean. Uses this
        # package's own db_ctx (not the API bridge's near-identical copy --
        # reaching into bartholomew_api_bridge_v0_1 via a sys.path.insert(0, ...)
        # hack, as this used to, permanently shadows the top-level "app"
        # module for the rest of the process, since bartholomew_api_bridge_v0_1/
        # services/api/ itself contains a file named app.py).
        try:
            from bartholomew.kernel.db_ctx import wal_checkpoint_truncate

            wal_checkpoint_truncate(self.db_path)
        except Exception as e:
            logger.debug(f"WAL checkpoint failed: {e}")
