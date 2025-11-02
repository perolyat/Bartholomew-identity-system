#!/usr/bin/env python3
"""
FTS Index Backfill Script for Bartholomew

This one-time utility re-indexes all existing memories into the SQLite FTS5
full-text search index using the same "summary-preferred then redacted" rule
that applies during normal memory ingestion.

Usage:
    bartholomew-backfill-fts --db /path/to/bartholomew.db
    bartholomew-backfill-fts --db ./data/memories.db --dry-run --verbose
    bartholomew-backfill-fts --db ./data/memories.db --batch 100 --no-optimize

Features:
    - Respects memory governance rules (fts_index allow/deny)
    - Applies summary-preferred indexing mode
    - Decrypts encrypted values/summaries best-effort
    - Safe: Read-only on memories table; only writes to FTS tables
    - Supports dry-run mode for preview
    - Progress logging with statistics

Requirements:
    - Database must exist with memories table
    - FTS schema will be created if not present
    - Encryption keys must be available if content is encrypted
"""

import argparse
import logging
import sqlite3
import sys
from typing import Optional

# Import Bartholomew components
from bartholomew.kernel.fts_client import FTSClient
from bartholomew.kernel.memory_rules import _rules_engine
from bartholomew.kernel.encryption_engine import _encryption_engine
from bartholomew.kernel.redaction_engine import apply_redaction
from bartholomew.kernel.memory_store import _load_fts_index_mode


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class BackfillStats:
    """Track backfill statistics"""
    def __init__(self):
        self.total = 0
        self.indexed = 0
        self.skipped = 0
        self.deleted = 0
        self.errors = 0
    
    def report(self) -> str:
        """Generate summary report"""
        return (
            f"\n{'='*60}\n"
            f"FTS Backfill Complete\n"
            f"{'='*60}\n"
            f"Total memories:     {self.total}\n"
            f"Indexed:            {self.indexed}\n"
            f"Skipped (no text):  {self.skipped}\n"
            f"Deleted (denied):   {self.deleted}\n"
            f"Errors:             {self.errors}\n"
            f"{'='*60}"
        )


class ProgressBar:
    """Simple progress bar for terminal output"""
    def __init__(self, total: int, width: int = 40):
        self.total = total
        self.width = width
        self.current = 0
    
    def update(self, indexed: int, skipped: int, deleted: int, errors: int):
        """Update progress bar with current stats"""
        self.current = indexed + skipped + deleted + errors
        if self.total == 0:
            percent = 0.0
        else:
            percent = (self.current / self.total) * 100
        
        filled = int(
            self.width * self.current / self.total
        ) if self.total > 0 else 0
        bar = '█' * filled + '░' * (self.width - filled)
        
        # Write to stdout with carriage return to overwrite
        sys.stdout.write(
            f'\r[{bar}] {percent:.1f}% | '
            f'✓{indexed} ⊘{skipped} ✗{deleted} ⚠{errors}'
        )
        sys.stdout.flush()
    
    def finish(self):
        """Complete progress bar and move to next line"""
        sys.stdout.write('\n')
        sys.stdout.flush()


def backfill_memory(
    memory_id: int,
    kind: str,
    key: str,
    value: str,
    summary: Optional[str],
    ts: str,
    conn: sqlite3.Connection,
    dry_run: bool = False
) -> tuple[str, Optional[str]]:
    """
    Backfill FTS index for a single memory
    
    Args:
        memory_id: Memory ID
        kind: Memory kind
        key: Memory key
        value: Stored value (potentially encrypted)
        summary: Stored summary (potentially encrypted)
        ts: Memory timestamp
        conn: SQLite connection for FTS operations
        dry_run: If True, skip actual writes
    
    Returns:
        Tuple of (action, reason) where action is:
        - 'indexed': Successfully indexed
        - 'skipped': Skipped (no indexable text)
        - 'deleted': Removed from index (policy denied)
        - 'error': Error occurred
    """
    try:
        # Step 1: Decrypt value and summary best-effort
        plaintext_value = _encryption_engine.try_decrypt_if_envelope(value)
        plaintext_summary = None
        if summary:
            plaintext_summary = _encryption_engine.try_decrypt_if_envelope(summary)
        
        # Step 2: Evaluate rules with plaintext value
        memory_dict = {
            "kind": kind,
            "key": key,
            "value": plaintext_value,
            "ts": ts,
        }
        evaluated = _rules_engine.evaluate(memory_dict)
        
        # Step 3: Check if FTS indexing is allowed
        fts_allowed = evaluated.get("fts_index", True)
        
        if not fts_allowed:
            # Policy denies indexing: delete from FTS
            if not dry_run:
                conn.execute(
                    "INSERT INTO memory_fts(memory_fts, rowid, value, "
                    "summary) VALUES ('delete', ?, '', '')",
                    (memory_id,)
                )
                conn.execute(
                    "DELETE FROM memory_fts_map WHERE memory_id = ?",
                    (memory_id,)
                )
            logger.debug(
                f"Memory {memory_id} ({kind}/{key}): deleted (policy denied)"
            )
            return ('deleted', 'policy denied')
        
        # Step 4: Apply redaction to compute redacted_value
        # CRITICAL: Apply same redaction as ingestion to avoid indexing
        # raw/unredacted content
        redacted_value = plaintext_value
        if evaluated.get("redact_strategy"):
            redacted_value = apply_redaction(plaintext_value, evaluated)
        
        # Step 5: Choose index text using EXACT same rule as ingestion
        # index_text = summary if (summary and fts_index_mode ==  # noqa
        #              "summary_preferred") else redacted_value
        fts_index_mode = evaluated.get(
            "fts_index_mode", _load_fts_index_mode()
        )
        
        index_text = None
        if plaintext_summary and fts_index_mode == "summary_preferred":
            index_text = plaintext_summary
            source = "summary"
        else:
            index_text = redacted_value
            source = "redacted_value"
        
        # Step 6: Validate we have indexable text
        if not index_text or not index_text.strip():
            logger.warning(
                f"Memory {memory_id} ({kind}/{key}): no indexable text "
                f"(empty {source})"
            )
            return ('skipped', f'empty {source}')
        
        # Step 7: Upsert to FTS index (using raw SQL for transaction control)
        if not dry_run:
            # Ensure entry in map table
            conn.execute(
                "INSERT OR IGNORE INTO memory_fts_map(memory_id) VALUES (?)",
                (memory_id,)
            )
            
            # Delete old FTS entry if exists
            conn.execute(
                "INSERT INTO memory_fts(memory_fts, rowid, value, "
                "summary) VALUES ('delete', ?, '', '')",
                (memory_id,)
            )
            
            # Insert sanitized index_text (never raw/unredacted)
            conn.execute(
                "INSERT INTO memory_fts(rowid, value, summary) "
                "VALUES (?, ?, NULL)",
                (memory_id, index_text)
            )
        
        logger.debug(
            f"Memory {memory_id} ({kind}/{key}): indexed "
            f"({len(index_text)} chars from {source})"
        )
        return ('indexed', f'{len(index_text)} chars from {source}')
    
    except Exception as e:
        logger.error(
            f"Memory {memory_id} ({kind}/{key}): error - {e}",
            exc_info=True
        )
        return ('error', str(e))


def backfill_fts(
    db_path: str,
    batch_size: int = 500,
    optimize: bool = True,
    dry_run: bool = False,
    verbose: bool = False
) -> int:
    """
    Backfill FTS index for all memories
    
    Args:
        db_path: Path to SQLite database
        batch_size: Number of memories to process per batch (transaction size)
        optimize: Whether to optimize FTS index after backfill
        dry_run: If True, don't write changes
        verbose: Enable verbose logging
    
    Returns:
        Exit code (0 for success, non-zero for errors)
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    stats = BackfillStats()
    progress = None
    
    try:
        # Initialize FTS client and schema
        fts = FTSClient(db_path)
        if not dry_run:
            logger.info("Initializing FTS schema...")
            fts.init_schema()
        else:
            logger.info("DRY RUN MODE - No changes will be written")
        
        # Connect to database for reading
        logger.info(f"Opening database: {db_path}")
        read_conn = sqlite3.connect(db_path)
        read_conn.row_factory = sqlite3.Row
        
        # Count total memories
        cursor = read_conn.execute("SELECT COUNT(*) FROM memories")
        stats.total = cursor.fetchone()[0]
        logger.info(f"Found {stats.total} memories to process")
        
        if stats.total == 0:
            logger.info("No memories to backfill")
            read_conn.close()
            return 0
        
        # Initialize progress bar
        progress = ProgressBar(stats.total)
        
        # Open separate connection for FTS writes (transaction batching)
        write_conn = sqlite3.connect(db_path) if not dry_run else None
        
        # Fetch all memories
        cursor = read_conn.execute(
            "SELECT id, kind, key, value, summary, ts "
            "FROM memories ORDER BY id"
        )
        rows = cursor.fetchall()
        read_conn.close()
        
        # Process in batches with transactions
        batch_count = 0
        for i, row in enumerate(rows):
            # Start new transaction at batch boundaries
            if batch_count == 0 and write_conn:
                write_conn.execute("BEGIN IMMEDIATE")
            
            action, reason = backfill_memory(
                memory_id=row['id'],
                kind=row['kind'],
                key=row['key'],
                value=row['value'],
                summary=row['summary'],
                ts=row['ts'],
                conn=write_conn if write_conn else read_conn,
                dry_run=dry_run
            )
            
            # Update statistics
            if action == 'indexed':
                stats.indexed += 1
            elif action == 'skipped':
                stats.skipped += 1
            elif action == 'deleted':
                stats.deleted += 1
            elif action == 'error':
                stats.errors += 1
            
            batch_count += 1
            
            # Update progress bar
            progress.update(
                stats.indexed, stats.skipped, stats.deleted, stats.errors
            )
            
            # Commit batch transaction
            is_last_row = (i == len(rows) - 1)
            if (batch_count >= batch_size or is_last_row) and write_conn:
                write_conn.commit()
                batch_count = 0
                
                # Log milestone
                if not is_last_row:
                    logger.debug(
                        f"Committed batch at {i+1}/{stats.total} rows"
                    )
        
        # Finish progress bar
        if progress:
            progress.finish()
        
        # Close write connection
        if write_conn:
            write_conn.close()
        
        # Optimize FTS index
        if optimize and not dry_run:
            logger.info("Optimizing FTS index...")
            fts.optimize()
            logger.info("FTS index optimized")
        
        # Print final report
        logger.info(stats.report())
        
        # Return success if no errors
        return 0 if stats.errors == 0 else 1
    
    except Exception as e:
        logger.error(f"Fatal error during backfill: {e}", exc_info=True)
        if progress:
            progress.finish()
        return 1


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Backfill FTS index for Bartholomew memories',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        '--db',
        required=True,
        help='Path to SQLite database file'
    )
    
    parser.add_argument(
        '--batch',
        type=int,
        default=500,
        help='Batch size for progress logging (default: 500)'
    )
    
    parser.add_argument(
        '--optimize',
        action='store_true',
        default=True,
        help='Optimize FTS index after backfill (default: enabled)'
    )
    
    parser.add_argument(
        '--no-optimize',
        action='store_false',
        dest='optimize',
        help='Skip FTS index optimization'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without writing to database'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose debug logging'
    )
    
    args = parser.parse_args()
    
    # Validate database exists
    import os
    if not os.path.exists(args.db):
        logger.error(f"Database not found: {args.db}")
        return 1
    
    # Run backfill
    exit_code = backfill_fts(
        db_path=args.db,
        batch_size=args.batch,
        optimize=args.optimize,
        dry_run=args.dry_run,
        verbose=args.verbose
    )
    
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
