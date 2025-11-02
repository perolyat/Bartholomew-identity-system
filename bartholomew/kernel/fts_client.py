"""
Full-Text Search (FTS5) client for memory content.

Provides search, upsert, delete, and snippet operations over memory text
content using SQLite's FTS5 virtual table in external-content mode.

The FTS layer indexes the 'value' and 'summary' columns from the memories
table, enabling fast full-text search with ranking and highlighting.
"""

import logging
import os
import sqlite3
import struct
from typing import Any

import yaml

from bartholomew.kernel.db_ctx import set_wal_pragmas


logger = logging.getLogger(__name__)


def _rank_pcx(matchinfo_blob: bytes) -> float:
    """
    Compute approximate BM25-like ranking using matchinfo('pcx').

    Fallback ranking function when bm25 UDF is not available.

    The matchinfo('pcx') format provides:
    - p: number of matchable phrases in query
    - c: number of user-defined columns
    - x: array of 3*p*c values (hits_this_row, hits_all_rows, docs_with_hits)

    This computes a simplified tf-idf-like score:
    score = sum(hits_this_row / (docs_with_hits + 1)) across all terms/columns

    Args:
        matchinfo_blob: Binary matchinfo blob from FTS5

    Returns:
        Float score (higher is better)
    """
    if not matchinfo_blob:
        return 0.0

    try:
        # Unpack native-endian 32-bit unsigned ints
        num_ints = len(matchinfo_blob) // 4
        ints = struct.unpack(f"{num_ints}I", matchinfo_blob)

        if len(ints) < 2:
            return 0.0

        p, c = ints[0], ints[1]
        idx = 2
        score = 0.0

        # Iterate over phrases and columns
        for _ in range(p):
            for _ in range(c):
                if idx + 2 >= len(ints):
                    break

                hits_this_row = ints[idx]
                # hits_all_rows = ints[idx + 1]  # Not used in simple scoring
                docs_with_hits = ints[idx + 2]
                idx += 3

                # Simple tf-idf-like: term frequency weighted by inverse
                # document frequency (rarer terms score higher)
                if docs_with_hits > 0:
                    score += float(hits_this_row) / (docs_with_hits + 1)

        return score
    except Exception as e:
        logger.debug(f"Failed to compute rank_pcx: {e}")
        return 0.0


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
    tokenize='{tokenizer}'
);

-- Mapping table to track FTS index entries
-- Ensures rowid consistency and supports controlled population
CREATE TABLE IF NOT EXISTS memory_fts_map (
    memory_id INTEGER PRIMARY KEY,
    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
);

-- Triggers to keep FTS index synchronized with memories table
CREATE TRIGGER IF NOT EXISTS memory_fts_insert AFTER INSERT ON memories
BEGIN
    INSERT INTO memory_fts(rowid, value, summary)
    VALUES (new.id, new.value, new.summary);
    INSERT OR IGNORE INTO memory_fts_map(memory_id) VALUES (new.id);
END;

CREATE TRIGGER IF NOT EXISTS memory_fts_update AFTER UPDATE ON memories
BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, value, summary)
    VALUES ('delete', old.id, old.value, old.summary);
    INSERT INTO memory_fts(rowid, value, summary)
    VALUES (new.id, new.value, new.summary);
END;

CREATE TRIGGER IF NOT EXISTS memory_fts_delete AFTER DELETE ON memories
BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, value, summary)
    VALUES ('delete', old.id, old.value, old.summary);
    DELETE FROM memory_fts_map WHERE memory_id = old.id;
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
        """
        schema = FTS_SCHEMA.format(tokenizer=self.tokenizer)

        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)
            # Ensure this Python/SQLite build supports FTS5
            self._probe_fts5(conn)
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
        2. Verifies FTS rowid == memory id consistency
        3. Rebuilds index if mismatches detected

        Safe to call multiple times (idempotent).
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            set_wal_pragmas(conn)

            # Check for rowid mismatches (orphaned FTS entries)
            cursor = conn.execute(
                """
                SELECT 1
                FROM memory_fts f
                LEFT JOIN memories m ON f.rowid = m.id
                WHERE m.id IS NULL
                LIMIT 1
            """,
            )

            has_mismatch = cursor.fetchone() is not None

            if has_mismatch:
                logger.warning("FTS rowid mismatch detected, rebuilding index...")

                # Clear and rebuild
                conn.execute("DELETE FROM memory_fts")
                conn.execute("DELETE FROM memory_fts_map")

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

                conn.commit()
                logger.info("FTS index rebuilt for rowid consistency")
            else:
                logger.debug("FTS schema migration: no action needed")

        except Exception as e:
            logger.warning(f"FTS schema migration check failed: {e}")
        finally:
            if conn:
                conn.close()

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
            # Ensure entry in map table
            conn.execute("INSERT OR IGNORE INTO memory_fts_map(memory_id) VALUES (?)", (memory_id,))

            # Delete old FTS entry if exists
            conn.execute(
                "INSERT INTO memory_fts(memory_fts, rowid, value, summary) "
                "SELECT 'delete', ?, value, summary FROM memory_fts "
                "WHERE rowid = ?",
                (memory_id, memory_id),
            )

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

        # Fallback SQL using matchinfo('pcx')
        # Note: Negation to maintain "lower is better" ordering
        sql_fallback = f"""
            SELECT
                m.id,
                m.kind,
                m.key,
                m.value,
                m.summary,
                m.ts,
                -rank_pcx(matchinfo(memory_fts, 'pcx')) as rank,
                snippet(memory_fts, 0, '[', ']', ' … ', 8) as snippet
            FROM memory_fts
            JOIN memories m ON memory_fts.rowid = m.id
            WHERE memory_fts MATCH ?
            {order_clause}
            LIMIT ? OFFSET ?
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
                        logger.info("bm25 UDF not available, using matchinfo fallback")
                        force_fallback = True
                    else:
                        raise

            # Use fallback if bm25 failed or forced
            if force_fallback:
                # Register Python rank_pcx function
                conn.create_function("rank_pcx", 1, _rank_pcx)

                cursor = conn.execute(sql_fallback, (query, fetch_limit, offset))
                rows = cursor.fetchall()
                results = [dict(row) for row in rows]
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
            # Clear existing FTS data
            conn.execute("DELETE FROM memory_fts")
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
