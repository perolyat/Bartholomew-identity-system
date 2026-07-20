"""
Full-Text Search (FTS5) client for memory content.

Provides search, upsert, delete, and snippet operations over memory text
content using SQLite's FTS5 virtual table in external-content mode.

The FTS layer indexes the 'value' and 'summary' columns from the memories
table, enabling fast full-text search with ranking and highlighting.
"""

import logging
import os
import re
import sqlite3
from typing import Any

import yaml

from bartholomew.kernel.db_ctx import set_wal_pragmas


logger = logging.getLogger(__name__)


_FTS5_QUERY_OPERATORS = re.compile(r"\b(AND|OR|NOT|NEAR)\b", re.IGNORECASE)
_NON_WORD_CHARS = re.compile(r"[^\w\s]")


def _extract_query_terms(query: str) -> list[str]:
    """
    Extract a rough bag of literal search terms from an FTS5 query string.

    Not a full FTS5 query parser -- just strips boolean operators and
    punctuation to get words to count for _term_frequency_rank() below.
    FTS5's own query engine (via the MATCH clause) still does the actual
    boolean/phrase/NEAR matching that decides which rows qualify; this only
    ever affects the order results come back in.
    """
    cleaned = _NON_WORD_CHARS.sub(" ", _FTS5_QUERY_OPERATORS.sub(" ", query))
    return [t.lower() for t in cleaned.split() if t]


def _term_frequency_rank(value: str | None, summary: str | None, terms: list[str]) -> float:
    """
    Simple term-frequency ranking used when the bm25() UDF is unavailable.

    FTS5 has no ranking aux function besides bm25() itself to fall back to --
    matchinfo() (used by a previous version of this fallback) is an FTS3/
    FTS4-only function that FTS5 has never supported, confirmed independent
    of this codebase; it always raised "unable to use function matchinfo in
    the requested context" regardless of platform. Lower is better here,
    matching bm25()'s own convention (bm25's raw scores are negative --
    more negative is a better match).
    """
    if not terms:
        return 0.0
    text = f"{value or ''} {summary or ''}".lower()
    count = sum(text.count(t) for t in terms)
    return -float(count)


def fts5_available(conn: sqlite3.Connection) -> bool:
    """
    Runtime probe for FTS5 availability in SQLite.

    Attempts to create a throwaway temp virtual table using FTS5.
    Returns True if FTS5 is available, False otherwise.

    Args:
        conn: Active SQLite connection

    Returns:
        True if FTS5 is available, False otherwise

    Example:
        >>> conn = sqlite3.connect(":memory:")
        >>> if fts5_available(conn):
        ...     print("FTS5 is available")
        ... else:
        ...     print("FTS5 not available, falling back")
    """
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.__fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE temp.__fts5_probe")
        return True
    except Exception:
        return False


def _load_tokenizer_config() -> str:
    """
    Load FTS tokenizer configuration from kernel.yaml.

    Resolution order:
    1. retrieval.fts_tokenizer + fts_tokenizer_args (new standard location)
    2. fts.tokenizer (legacy, backward compat)
    3. Default: 'porter'

    Returns:
        Tokenizer specification string (e.g., 'porter' or
        'unicode61 remove_diacritics 2 tokenchars .-@_')
    """
    try:
        config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "kernel.yaml")
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = yaml.safe_load(f)
                if config:
                    # Try new location first
                    retrieval = config.get("retrieval", {})
                    if "fts_tokenizer" in retrieval:
                        tokenizer = retrieval["fts_tokenizer"]
                        # Check for optional tokenizer args
                        tokenizer_args = retrieval.get("fts_tokenizer_args", "")
                        if tokenizer_args:
                            return f"{tokenizer} {tokenizer_args}".strip()
                        return tokenizer

                    # Fall back to legacy location
                    if "fts" in config:
                        return config["fts"].get("tokenizer", "porter")
    except Exception as e:
        logger.debug(f"Could not load FTS config: {e}")

    return "porter"


# FTS5 Schema for external-content mode
FTS_SCHEMA = """
-- FTS5 virtual table in external-content mode
-- References memories table, indexes value and summary columns
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    value,
    summary,
    content='memories',
    content_rowid='id',
    tokenize="{tokenizer}"
);

-- Mapping table to track FTS index entries
-- Ensures rowid consistency and supports controlled population
CREATE TABLE IF NOT EXISTS memory_fts_map (
    memory_id INTEGER PRIMARY KEY,
    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
);

-- Triggers to keep FTS index synchronized with memories table.
--
-- memory_fts_map tracks which rowids are actually present in the memory_fts
-- shadow index (populated by these triggers and by FTSClient.upsert()).
-- Issuing FTS5's 'delete' special command for a rowid that was never
-- actually indexed (e.g. a memories row written before this schema/these
-- triggers existed) makes SQLite report "database disk image is malformed"
-- -- a confusing error that doesn't mean anything is actually corrupt on
-- disk. The UPDATE/DELETE triggers below are guarded on memory_fts_map so
-- 'delete' is only issued for rows that are really there.
CREATE TRIGGER IF NOT EXISTS memory_fts_insert AFTER INSERT ON memories
BEGIN
    INSERT INTO memory_fts(rowid, value, summary)
    VALUES (new.id, new.value, new.summary);
    INSERT OR IGNORE INTO memory_fts_map(memory_id) VALUES (new.id);
END;

CREATE TRIGGER IF NOT EXISTS memory_fts_update AFTER UPDATE ON memories
WHEN EXISTS (SELECT 1 FROM memory_fts_map WHERE memory_id = old.id)
BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, value, summary)
    VALUES ('delete', old.id, old.value, old.summary);
    INSERT INTO memory_fts(rowid, value, summary)
    VALUES (new.id, new.value, new.summary);
END;

CREATE TRIGGER IF NOT EXISTS memory_fts_update_backfill AFTER UPDATE ON memories
WHEN NOT EXISTS (SELECT 1 FROM memory_fts_map WHERE memory_id = old.id)
BEGIN
    INSERT INTO memory_fts(rowid, value, summary)
    VALUES (new.id, new.value, new.summary);
    INSERT OR IGNORE INTO memory_fts_map(memory_id) VALUES (new.id);
END;

CREATE TRIGGER IF NOT EXISTS memory_fts_delete AFTER DELETE ON memories
WHEN EXISTS (SELECT 1 FROM memory_fts_map WHERE memory_id = old.id)
BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, value, summary)
    VALUES ('delete', old.id, old.value, old.summary);
    DELETE FROM memory_fts_map WHERE memory_id = old.id;
END;
"""

# Names of triggers defined in FTS_SCHEMA / CHUNK_FTS_SCHEMA, dropped and
# recreated on every init so schema changes reach already-existing databases
# (see init_schema()/init_chunk_schema()).
_MEMORY_FTS_TRIGGERS = (
    "memory_fts_insert",
    "memory_fts_update",
    "memory_fts_update_backfill",
    "memory_fts_delete",
)
_CHUNK_FTS_TRIGGERS = (
    "chunk_fts_insert",
    "chunk_fts_update",
    "chunk_fts_update_backfill",
    "chunk_fts_delete",
)

# Phase 2f: Chunk FTS Schema for external-content mode
CHUNK_FTS_SCHEMA = """
-- FTS5 virtual table for memory chunks in external-content mode
-- References memory_chunks table, indexes chunk text
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    text,
    content='memory_chunks',
    content_rowid='id',
    tokenize="{tokenizer}"
);

-- Mapping table to track chunk FTS index entries
CREATE TABLE IF NOT EXISTS chunk_fts_map (
    chunk_id INTEGER PRIMARY KEY,
    memory_id INTEGER NOT NULL,
    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(chunk_id) REFERENCES memory_chunks(id) ON DELETE CASCADE,
    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunk_fts_map_memory
    ON chunk_fts_map(memory_id);

-- Triggers to keep chunk FTS index synchronized with memory_chunks table.
-- Same 'delete'-on-unindexed-rowid hazard as memory_fts's triggers above;
-- guarded the same way, against chunk_fts_map.
CREATE TRIGGER IF NOT EXISTS chunk_fts_insert AFTER INSERT ON memory_chunks
BEGIN
    INSERT INTO chunk_fts(rowid, text) VALUES (new.id, new.text);
    INSERT OR IGNORE INTO chunk_fts_map(chunk_id, memory_id)
    VALUES (new.id, new.memory_id);
END;

CREATE TRIGGER IF NOT EXISTS chunk_fts_update AFTER UPDATE ON memory_chunks
WHEN EXISTS (SELECT 1 FROM chunk_fts_map WHERE chunk_id = old.id)
BEGIN
    INSERT INTO chunk_fts(chunk_fts, rowid, text)
    VALUES ('delete', old.id, old.text);
    INSERT INTO chunk_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS chunk_fts_update_backfill AFTER UPDATE ON memory_chunks
WHEN NOT EXISTS (SELECT 1 FROM chunk_fts_map WHERE chunk_id = old.id)
BEGIN
    INSERT INTO chunk_fts(rowid, text) VALUES (new.id, new.text);
    INSERT OR IGNORE INTO chunk_fts_map(chunk_id, memory_id) VALUES (new.id, new.memory_id);
END;

CREATE TRIGGER IF NOT EXISTS chunk_fts_delete AFTER DELETE ON memory_chunks
WHEN EXISTS (SELECT 1 FROM chunk_fts_map WHERE chunk_id = old.id)
BEGIN
    INSERT INTO chunk_fts(chunk_fts, rowid, text)
    VALUES ('delete', old.id, old.text);
    DELETE FROM chunk_fts_map WHERE chunk_id = old.id;
END;
"""


class FTSClient:
    """
    Full-text search client for memory content.

    Provides search, upsert, delete, and snippet operations using SQLite FTS5.
    The FTS index is maintained in external-content mode, referencing the
    memories table.

    Attributes:
        db_path: Path to SQLite database
        tokenizer: FTS5 tokenizer to use (default: 'porter')
    """

    def __init__(self, db_path: str, tokenizer: str | None = None):
        """
        Initialize FTS client.

        Args:
            db_path: Path to SQLite database
            tokenizer: FTS5 tokenizer name. If None, loads from config
                      (default: 'porter')
        """
        self.db_path = db_path
        self.tokenizer = tokenizer or _load_tokenizer_config()
        logger.debug(f"FTSClient initialized with tokenizer: {self.tokenizer}")

    def _probe_fts5(self, conn: sqlite3.Connection) -> None:
        """
        Probe for FTS5 availability in this Python/SQLite build.

        Attempts to create a throwaway temp virtual table using FTS5.
        Raises RuntimeError with clear message if FTS5 is not available.

        Args:
            conn: Active SQLite connection

        Raises:
            RuntimeError: If FTS5 extension is not compiled into SQLite
        """
        if not fts5_available(conn):
            raise RuntimeError(
                "SQLite FTS5 is not available in this Python build. "
                "Install a Python/SQLite build compiled with FTS5. "
                "Note: This is unrelated to the vector extension (vss0).",
            )

    def init_schema(self) -> None:
        """
        Initialize FTS5 tables and triggers.

        Creates the memory_fts virtual table, memory_fts_map tracking table,
        and synchronization triggers. Safe to call multiple times (idempotent).

        Trigger bodies are dropped and recreated on every call (rather than
        left alone via CREATE TRIGGER IF NOT EXISTS) so that a database
        created by an older version of this schema always picks up the
        current trigger definitions -- e.g. the memory_fts_map guards added
        to memory_fts_update/memory_fts_delete. IF NOT EXISTS alone would
        silently leave a pre-existing database on the old, unguarded
        trigger bodies forever.
        """
        schema = FTS_SCHEMA.format(tokenizer=self.tokenizer)

        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)
            # Ensure this Python/SQLite build supports FTS5
            self._probe_fts5(conn)
            for trigger in _MEMORY_FTS_TRIGGERS:
                conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            conn.executescript(schema)
            conn.commit()
        finally:
            if conn:
                conn.close()

        logger.info("FTS5 schema initialized")

        # Run migration to ensure rowid consistency
        self.migrate_schema()

    def migrate_schema(self) -> None:
        """
        Migrate FTS schema to ensure rowid consistency.

        This is a self-healing migration that:
        1. Ensures memory_fts_map exists
        2. Verifies memory_fts_map == memories consistency
        3. Rebuilds index if mismatches detected

        Consistency is checked against memory_fts_map rather than memory_fts
        itself. A bare, non-MATCH SELECT against an external-content FTS5
        table (memory_fts is one -- content='memories') proxies through to
        the content table instead of reflecting the real FTS5 shadow index,
        so a LEFT JOIN like "memory_fts f ... WHERE m.id IS NULL" can never
        find a mismatch regardless of the index's actual state. memory_fts_map
        is a plain table this class maintains as the authoritative record of
        what's actually indexed, so it's compared against memories in both
        directions instead.

        Safe to call multiple times (idempotent).
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)

            orphaned_map_entries = (
                conn.execute(
                    """
                    SELECT 1
                    FROM memory_fts_map fm
                    LEFT JOIN memories m ON fm.memory_id = m.id
                    WHERE m.id IS NULL
                    LIMIT 1
                """,
                ).fetchone()
                is not None
            )

            unindexed_memories = (
                conn.execute(
                    """
                    SELECT 1
                    FROM memories m
                    LEFT JOIN memory_fts_map fm ON m.id = fm.memory_id
                    WHERE fm.memory_id IS NULL
                    LIMIT 1
                """,
                ).fetchone()
                is not None
            )
        except Exception as e:
            logger.warning(f"FTS schema migration check failed: {e}")
            return
        finally:
            if conn:
                conn.close()

        if orphaned_map_entries or unindexed_memories:
            logger.warning("FTS index mismatch detected, rebuilding index...")
            self.rebuild_index()
        else:
            logger.debug("FTS schema migration: no action needed")

    def upsert(self, memory_id: int, value: str, summary: str | None = None) -> None:
        """
        Insert or update FTS index for a memory.

        This manually updates the FTS index. Note that if triggers are
        enabled, they will handle synchronization automatically. Use this
        for manual index management or backfilling.

        Args:
            memory_id: Memory ID (must exist in memories table)
            value: Memory content text
            summary: Optional summary text
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)

            # memory_fts_map is this class's own record of which rowids are
            # actually present in the memory_fts index. Only issue the FTS5
            # 'delete' command when it's a real update -- issuing 'delete'
            # for a rowid that was never actually indexed (e.g. a memories
            # row inserted before init_schema()/triggers existed, so it was
            # never backfilled) makes SQLite report "database disk image is
            # malformed", even though nothing on disk is actually corrupt.
            already_indexed = (
                conn.execute(
                    "SELECT 1 FROM memory_fts_map WHERE memory_id = ?",
                    (memory_id,),
                ).fetchone()
                is not None
            )

            if already_indexed:
                # Delete old FTS entry before re-inserting
                conn.execute(
                    "INSERT INTO memory_fts(memory_fts, rowid, value, summary) "
                    "SELECT 'delete', ?, value, summary FROM memory_fts "
                    "WHERE rowid = ?",
                    (memory_id, memory_id),
                )

            # Ensure entry in map table
            conn.execute("INSERT OR IGNORE INTO memory_fts_map(memory_id) VALUES (?)", (memory_id,))

            # Insert new FTS entry
            conn.execute(
                "INSERT INTO memory_fts(rowid, value, summary) VALUES (?, ?, ?)",
                (memory_id, value, summary),
            )

            conn.commit()
        finally:
            if conn:
                conn.close()

        logger.debug(f"FTS index updated for memory {memory_id}")

    def delete(self, memory_id: int) -> None:
        """
        Delete FTS index entry for a memory.

        Removes the memory from the FTS index and map table.

        Args:
            memory_id: Memory ID to remove from index
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)
            # Delete from FTS index
            conn.execute("DELETE FROM memory_fts WHERE rowid = ?", (memory_id,))

            # Delete from map table
            conn.execute("DELETE FROM memory_fts_map WHERE memory_id = ?", (memory_id,))

            conn.commit()
        finally:
            if conn:
                conn.close()

        logger.debug(f"FTS index entry deleted for memory {memory_id}")

    def search(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
        order_by_rank: bool = True,
        apply_consent_gate: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Search memories using full-text search.

        Uses FTS5 MATCH syntax for queries. Results include memory metadata
        and BM25 relevance ranking.

        Privacy gates are applied by default to exclude:
        - never_store memories (allow_store=false)
        - ask_before_store memories without consent

        Context-only memories are included but marked with context_only=True.

        Args:
            query: FTS5 query string (e.g., "robot", "robot OR human",
                   "robot NEAR human", '"exact phrase"')
            limit: Maximum number of results (default: 10)
            offset: Result offset for pagination (default: 0)
            order_by_rank: If True, order by BM25 rank. If False, by memory
                          ID descending (default: True)
            apply_consent_gate: If True (default), apply privacy filtering

        Returns:
            List of dicts with keys: id, kind, key, value, summary, ts, rank,
            context_only (bool), recall_policy (str or None)

        Example:
            >>> client.search("privacy AND consent")
            >>> client.search('"machine learning"', limit=5)
            >>> client.search("robot OR ai OR assistant")
        """
        order_clause = "ORDER BY rank ASC" if order_by_rank else "ORDER BY m.id DESC"

        # Pull more candidates to account for filtering
        fetch_limit = limit * 3 if apply_consent_gate else limit

        # Check if we should force fallback for testing
        force_fallback = os.getenv("BARTHO_FORCE_BM25_FALLBACK") == "1"

        # Primary SQL using bm25 UDF
        sql_bm25 = f"""
            SELECT
                m.id,
                m.kind,
                m.key,
                m.value,
                m.summary,
                m.ts,
                bm25(memory_fts) as rank,
                snippet(memory_fts, 0, '[', ']', ' … ', 8) as snippet
            FROM memory_fts
            JOIN memories m ON memory_fts.rowid = m.id
            WHERE memory_fts MATCH ?
            {order_clause}
            LIMIT ? OFFSET ?
        """

        # Fallback SQL when the bm25() UDF is unavailable. No ranking
        # function in the query itself -- FTS5 has no ranking aux function
        # besides bm25() to fall back to (see _term_frequency_rank's
        # docstring) -- rank is computed in Python below from a bounded
        # candidate pool instead.
        sql_fallback = """
            SELECT
                m.id,
                m.kind,
                m.key,
                m.value,
                m.summary,
                m.ts,
                snippet(memory_fts, 0, '[', ']', ' … ', 8) as snippet
            FROM memory_fts
            JOIN memories m ON memory_fts.rowid = m.id
            WHERE memory_fts MATCH ?
            ORDER BY m.id DESC
            LIMIT ?
        """

        conn = None
        results = []
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)
            conn.row_factory = sqlite3.Row

            # Try bm25 UDF first unless forced to fallback
            if not force_fallback:
                try:
                    cursor = conn.execute(sql_bm25, (query, fetch_limit, offset))
                    rows = cursor.fetchall()
                    results = [dict(row) for row in rows]
                except sqlite3.OperationalError as e:
                    # Check if error is due to missing bm25 function
                    if "no such function: bm25" in str(e).lower():
                        logger.info("bm25 UDF not available, using term-frequency fallback")
                        force_fallback = True
                    else:
                        raise

            # Use fallback if bm25 failed or forced
            if force_fallback:
                candidate_pool = max(fetch_limit * 5, 100)
                cursor = conn.execute(sql_fallback, (query, candidate_pool))
                rows = cursor.fetchall()
                results = [dict(row) for row in rows]

                query_terms = _extract_query_terms(query)
                for r in results:
                    r["rank"] = _term_frequency_rank(r.get("value"), r.get("summary"), query_terms)

                if order_by_rank:
                    results.sort(key=lambda r: r["rank"])
                results = results[offset : offset + fetch_limit]
        finally:
            if conn:
                conn.close()

        # Apply consent gate if enabled
        if apply_consent_gate and results:
            from bartholomew.kernel.consent_gate import ConsentGate

            gate = ConsentGate(self.db_path)
            results = gate.apply_to_fts_results(results)

            # Trim to requested limit after filtering
            results = results[:limit]

        logger.debug(f"FTS search returned {len(results)} results for: {query}")
        return results

    def snippet(
        self,
        memory_id: int,
        column: str = "value",
        start_mark: str = "<b>",
        end_mark: str = "</b>",
        ellipsis: str = "…",
        tokens: int = 12,
    ) -> str | None:
        """
        Generate a snippet with highlighted search matches.

        Returns a text excerpt from the specified column with search terms
        highlighted. Useful for displaying search results.

        Args:
            memory_id: Memory ID to generate snippet for
            column: Column name ('value' or 'summary', default: 'value')
            start_mark: Start marker for highlights (default: '<b>')
            end_mark: End marker for highlights (default: '</b>')
            ellipsis: Ellipsis text for truncation (default: '…')
            tokens: Number of tokens around matches (default: 12)

        Returns:
            Highlighted snippet string, or None if memory not found

        Example:
            >>> snippet = client.snippet(
            ...     123, start_mark="**", end_mark="**"
            ... )
            >>> # Returns: "...the **robot** learned..."
        """
        # Map column name to FTS column index
        column_map = {"value": 0, "summary": 1}
        if column not in column_map:
            raise ValueError(f"Invalid column: {column}. Use 'value' or 'summary'")

        column_idx = column_map[column]

        sql = """
            SELECT snippet(
                memory_fts,
                ?,  -- column index
                ?,  -- start mark
                ?,  -- end mark
                ?,  -- ellipsis
                ?   -- tokens
            ) as snippet
            FROM memory_fts
            WHERE rowid = ?
        """

        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)
            cursor = conn.execute(
                sql,
                (column_idx, start_mark, end_mark, ellipsis, tokens, memory_id),
            )
            row = cursor.fetchone()
            result = row[0] if row else None
        finally:
            if conn:
                conn.close()

        return result

    def rebuild_index(self) -> int:
        """
        Rebuild entire FTS index from memories table.

        Useful for:
        - Initial index population
        - Recovering from index corruption
        - After bulk memory imports

        Returns:
            Number of memories indexed
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)

            # Drop and recreate memory_fts itself rather than trying to
            # DELETE its contents. An external-content FTS5 table's actual
            # shadow-index state can't be reliably introspected via ordinary
            # SQL (a bare SELECT without MATCH just proxies through to the
            # memories content table, not the real index) -- so there's no
            # safe way to know in advance whether DELETE FROM memory_fts is
            # a no-op or would hit a genuinely inconsistent index
            # ("database disk image is malformed", a confusing SQLite/FTS5
            # error that doesn't mean the file is actually corrupt).
            # memory_fts_map can't be trusted as a stand-in for that check
            # either -- it can be empty while the real index still has
            # stale entries (e.g. a database from before the map table
            # existed) -- so dropping and recreating the table sidesteps the
            # question entirely: guaranteed empty, no stale entries can
            # survive a rebuild either way.
            conn.execute("DROP TABLE IF EXISTS memory_fts")
            conn.executescript(FTS_SCHEMA.format(tokenizer=self.tokenizer))
            conn.execute("DELETE FROM memory_fts_map")

            # Rebuild from memories table
            conn.execute(
                """
                INSERT INTO memory_fts(rowid, value, summary)
                SELECT id, value, summary FROM memories
            """,
            )

            conn.execute(
                """
                INSERT INTO memory_fts_map(memory_id)
                SELECT id FROM memories
            """,
            )

            cursor = conn.execute("SELECT COUNT(*) FROM memory_fts_map")
            count = cursor.fetchone()[0]

            conn.commit()
        finally:
            if conn:
                conn.close()

        logger.info(f"FTS index rebuilt: {count} memories indexed")
        return count

    def optimize(self) -> None:
        """
        Optimize FTS index (merge segments, reduce fragmentation).

        Should be called periodically for better search performance,
        especially after bulk updates.
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)
            conn.execute("INSERT INTO memory_fts(memory_fts) VALUES ('optimize')")
            conn.commit()
        finally:
            if conn:
                conn.close()

        logger.info("FTS index optimized")

    def upsert_fts_index(self, memory_id: int, text: str) -> None:
        """
        Upsert FTS index with a single text value.

        Convenience wrapper for indexing single-column text content.
        Used by memory ingestion pipeline to index redacted/summarized
        content before encryption.

        Args:
            memory_id: Memory ID
            text: Text content to index (redacted or summary)
        """
        self.upsert(memory_id, value=text, summary=None)

    def delete_fts_index(self, memory_id: int) -> None:
        """
        Delete FTS index for a memory.

        Convenience wrapper for removing memory from FTS index.
        Used when policy denies FTS indexing.

        Args:
            memory_id: Memory ID to remove from index
        """
        self.delete(memory_id)

    # =========================================================================
    # Phase 2f: Chunk FTS Methods
    # =========================================================================

    def init_chunk_schema(self) -> None:
        """
        Initialize chunk FTS5 tables and triggers.

        Creates the chunk_fts virtual table, chunk_fts_map tracking table,
        and synchronization triggers. Safe to call multiple times.

        Should be called after init_schema() when chunking is enabled.

        Trigger bodies are dropped and recreated on every call; see
        init_schema()'s docstring for why.
        """
        schema = CHUNK_FTS_SCHEMA.format(tokenizer=self.tokenizer)

        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)
            # FTS5 probe already done in init_schema
            for trigger in _CHUNK_FTS_TRIGGERS:
                conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            conn.executescript(schema)
            conn.commit()
        finally:
            if conn:
                conn.close()

        logger.info("Chunk FTS5 schema initialized")

    def upsert_chunk(
        self,
        chunk_id: int,
        memory_id: int,
        text: str,
    ) -> None:
        """
        Insert or update FTS index for a chunk.

        Args:
            chunk_id: Chunk ID (must exist in memory_chunks table)
            memory_id: Parent memory ID
            text: Chunk text content
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)

            # Only issue the FTS5 'delete' command for chunks already
            # indexed (per chunk_fts_map, checked before it's mutated
            # below) -- see upsert()'s comment for why an unconditional
            # 'delete' is unsafe.
            already_indexed = (
                conn.execute(
                    "SELECT 1 FROM chunk_fts_map WHERE chunk_id = ?",
                    (chunk_id,),
                ).fetchone()
                is not None
            )

            # Ensure entry in map table
            conn.execute(
                "INSERT OR REPLACE INTO chunk_fts_map(chunk_id, memory_id) VALUES (?, ?)",
                (chunk_id, memory_id),
            )

            if already_indexed:
                conn.execute(
                    "INSERT INTO chunk_fts(chunk_fts, rowid, text) "
                    "SELECT 'delete', ?, text FROM chunk_fts WHERE rowid = ?",
                    (chunk_id, chunk_id),
                )

            # Insert new FTS entry
            conn.execute(
                "INSERT INTO chunk_fts(rowid, text) VALUES (?, ?)",
                (chunk_id, text),
            )

            conn.commit()
        finally:
            if conn:
                conn.close()

        logger.debug(f"Chunk FTS index updated for chunk {chunk_id}")

    def delete_chunks_for_memory(self, memory_id: int) -> int:
        """
        Delete all chunk FTS entries for a memory.

        Args:
            memory_id: Parent memory ID

        Returns:
            Number of chunks deleted
        """
        conn = None
        count = 0
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)

            # Get chunk IDs for this memory
            cursor = conn.execute(
                "SELECT chunk_id FROM chunk_fts_map WHERE memory_id = ?",
                (memory_id,),
            )
            chunk_ids = [row[0] for row in cursor.fetchall()]

            # Delete from FTS
            for chunk_id in chunk_ids:
                conn.execute(
                    "INSERT INTO chunk_fts(chunk_fts, rowid, text) "
                    "SELECT 'delete', ?, text FROM chunk_fts WHERE rowid = ?",
                    (chunk_id, chunk_id),
                )

            # Delete from map
            conn.execute(
                "DELETE FROM chunk_fts_map WHERE memory_id = ?",
                (memory_id,),
            )

            count = len(chunk_ids)
            conn.commit()
        finally:
            if conn:
                conn.close()

        logger.debug(f"Deleted {count} chunk FTS entries for memory {memory_id}")
        return count

    def search_chunks(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
        order_by_rank: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Search memory chunks using full-text search.

        Returns chunk-level results with parent memory information.

        Args:
            query: FTS5 query string
            limit: Maximum number of results (default: 20)
            offset: Result offset for pagination
            order_by_rank: If True, order by BM25 rank

        Returns:
            List of dicts with keys: chunk_id, memory_id, seq, text, rank,
            snippet, memory_kind, memory_key, memory_ts
        """
        order_clause = "ORDER BY rank ASC" if order_by_rank else ""
        force_fallback = os.getenv("BARTHO_FORCE_BM25_FALLBACK") == "1"

        # Primary SQL using bm25 UDF
        sql_bm25 = f"""
            SELECT
                c.id as chunk_id,
                c.memory_id,
                c.seq,
                c.text,
                bm25(chunk_fts) as rank,
                snippet(chunk_fts, 0, '[', ']', ' … ', 12) as snippet,
                m.kind as memory_kind,
                m.key as memory_key,
                m.ts as memory_ts
            FROM chunk_fts
            JOIN memory_chunks c ON chunk_fts.rowid = c.id
            JOIN memories m ON c.memory_id = m.id
            WHERE chunk_fts MATCH ?
            {order_clause}
            LIMIT ? OFFSET ?
        """

        # Fallback SQL when the bm25() UDF is unavailable. Same approach as
        # search()'s fallback -- see _term_frequency_rank's docstring for why
        # there's no ranking function in the SQL itself; rank is computed in
        # Python below from a bounded candidate pool instead.
        sql_fallback = """
            SELECT
                c.id as chunk_id,
                c.memory_id,
                c.seq,
                c.text,
                snippet(chunk_fts, 0, '[', ']', ' … ', 12) as snippet,
                m.kind as memory_kind,
                m.key as memory_key,
                m.ts as memory_ts
            FROM chunk_fts
            JOIN memory_chunks c ON chunk_fts.rowid = c.id
            JOIN memories m ON c.memory_id = m.id
            WHERE chunk_fts MATCH ?
            ORDER BY c.id DESC
            LIMIT ?
        """

        conn = None
        results = []
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)
            conn.row_factory = sqlite3.Row

            if not force_fallback:
                try:
                    cursor = conn.execute(sql_bm25, (query, limit, offset))
                    rows = cursor.fetchall()
                    results = [dict(row) for row in rows]
                except sqlite3.OperationalError as e:
                    if "no such function: bm25" in str(e).lower():
                        force_fallback = True
                    else:
                        raise

            if force_fallback:
                candidate_pool = max((limit + offset) * 5, 100)
                cursor = conn.execute(sql_fallback, (query, candidate_pool))
                rows = cursor.fetchall()
                results = [dict(row) for row in rows]

                query_terms = _extract_query_terms(query)
                for r in results:
                    r["rank"] = _term_frequency_rank(r.get("text"), None, query_terms)

                if order_clause:
                    results.sort(key=lambda r: r["rank"])
                results = results[offset : offset + limit]
        finally:
            if conn:
                conn.close()

        logger.debug(f"Chunk FTS search returned {len(results)} results")
        return results

    def rebuild_chunk_index(self) -> int:
        """
        Rebuild entire chunk FTS index from memory_chunks table.

        Returns:
            Number of chunks indexed
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)

            # Drop and recreate chunk_fts itself rather than DELETE its
            # contents -- see rebuild_index()'s docstring for why.
            conn.execute("DROP TABLE IF EXISTS chunk_fts")
            conn.executescript(CHUNK_FTS_SCHEMA.format(tokenizer=self.tokenizer))
            conn.execute("DELETE FROM chunk_fts_map")

            # Rebuild from memory_chunks table
            conn.execute(
                """
                INSERT INTO chunk_fts(rowid, text)
                SELECT id, text FROM memory_chunks
                """,
            )

            conn.execute(
                """
                INSERT INTO chunk_fts_map(chunk_id, memory_id)
                SELECT id, memory_id FROM memory_chunks
                """,
            )

            cursor = conn.execute("SELECT COUNT(*) FROM chunk_fts_map")
            count = cursor.fetchone()[0]

            conn.commit()
        finally:
            if conn:
                conn.close()

        logger.info(f"Chunk FTS index rebuilt: {count} chunks indexed")
        return count
