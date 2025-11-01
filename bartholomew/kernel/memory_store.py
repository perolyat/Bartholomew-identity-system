from __future__ import annotations
import aiosqlite
import asyncio
import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from bartholomew.kernel.memory.privacy_guard import is_sensitive, request_permission_to_store

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,      -- 'fact' | 'event' | 'preference'
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  ts TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_kind_key ON memories(kind, key);

CREATE TABLE IF NOT EXISTS nudges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  actions TEXT,  -- JSON array of action objects
  status TEXT CHECK(status IN ('pending','acked','dismissed')) DEFAULT 'pending',
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
CREATE INDEX IF NOT EXISTS idx_reflections_kind_ts ON reflections(kind, ts);
"""


class MemoryStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def upsert_memory(
        self, kind: str, key: str, value: str, ts: str
    ) -> None:
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
                return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO memories(kind,key,value,ts) VALUES(?,?,?,?) "
                "ON CONFLICT(kind,key) DO UPDATE SET "
                "value=excluded.value, ts=excluded.ts",
                (kind, key, value, ts),
            )
            await db.commit()

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

    async def close(self) -> None:
        """Checkpoint and clean up WAL files."""
        try:
            # Import here to avoid circular dependency
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
