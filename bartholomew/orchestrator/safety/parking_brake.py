"""
Parking Brake: Runtime wiring for fail-closed safety gate.

One global brake with optional scopes: global, skills, sight, voice, scheduler.
Fail-closed: when engaged, gated components refuse to start/execute.
"""
from dataclasses import dataclass
from typing import Set
import json
import time
import sqlite3
import asyncio


@dataclass(frozen=True)
class BrakeState:
    """Parking brake state snapshot"""
    engaged: bool
    scopes: Set[str]  # e.g., {"global", "skills"}


class BrakeStorage:
    """
    Storage adapter for parking brake persistence and audit.
    
    Uses system_flags table for brake state and MemoryStore for audit trail.
    """
    def __init__(self, db_path: str, memory_store=None):
        """
        Initialize storage adapter.
        
        Args:
            db_path: Path to SQLite database
            memory_store: Optional MemoryStore instance for audit trail.
                         If None, audit logging is skipped.
        """
        self.db_path = db_path
        self.memory_store = memory_store
    
    def fetch_flag(self, key: str) -> str:
        """
        Fetch a system flag value (synchronous).
        
        Args:
            key: Flag key
            
        Returns:
            JSON string value or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT value FROM system_flags WHERE key = ?",
                (key,)
            )
            row = cursor.fetchone()
            return row[0] if row else None
    
    def upsert_flag(self, key: str, value: str, updated_at: int) -> None:
        """
        Upsert a system flag (synchronous).
        
        Args:
            key: Flag key
            value: JSON string value
            updated_at: Unix timestamp (epoch seconds)
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO system_flags(key, value, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value, updated_at=excluded.updated_at",
                (key, value, str(updated_at))
            )
            conn.commit()
    
    def append_memory(self, kind: str, value: dict) -> None:
        """
        Append audit entry via MemoryStore (asynchronous wrapped).
        
        Args:
            kind: Memory kind (e.g., "safety.audit")
            value: Dict payload to serialize
        """
        if not self.memory_store:
            return
        
        # Create unique key using timestamp + action
        ts = int(time.time())
        key = f"{ts}::{value.get('action', 'unknown')}"
        
        # Serialize value
        value_str = json.dumps(value)
        
        # Run async operation (handles event loop complexities)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Create task for later
                asyncio.create_task(
                    self.memory_store.upsert_memory(
                        kind=kind,
                        key=key,
                        value=value_str,
                        ts=str(ts)
                    )
                )
            else:
                # Run directly
                loop.run_until_complete(
                    self.memory_store.upsert_memory(
                        kind=kind,
                        key=key,
                        value=value_str,
                        ts=str(ts)
                    )
                )
        except Exception:
            # Fallback: run in new loop
            asyncio.run(
                self.memory_store.upsert_memory(
                    kind=kind,
                    key=key,
                    value=value_str,
                    ts=str(ts)
                )
            )


class ParkingBrake:
    """
    Parking brake controller for fail-closed safety gating.
    
    Manages engaged/disengaged state with optional scopes.
    Persists state and generates audit trail.
    """
    def __init__(self, storage: BrakeStorage):
        """
        Initialize parking brake controller.
        
        Args:
            storage: BrakeStorage adapter for persistence and audit
        """
        self._storage = storage
        self._cache = self._load()
    
    def _load(self) -> BrakeState:
        """Load brake state from storage."""
        row = self._storage.fetch_flag("parking_brake")
        data = json.loads(row or '{"engaged": false, "scopes": []}')
        return BrakeState(
            bool(data.get("engaged")),
            set(data.get("scopes", []))
        )
    
    def state(self) -> BrakeState:
        """Get current brake state."""
        return self._cache
    
    def engage(self, *scopes: str) -> None:
        """
        Engage parking brake with specified scopes.
        
        Args:
            *scopes: Component scopes to block. If empty, defaults to "global".
        """
        scopes_set = set(scopes) if scopes else {"global"}
        self._write(True, scopes_set)
    
    def disengage(self) -> None:
        """Disengage parking brake (allow all components)."""
        self._write(False, set())
    
    def _write(self, engaged: bool, scopes: Set[str]) -> None:
        """
        Write brake state to storage and update cache.
        
        Args:
            engaged: Whether brake is engaged
            scopes: Set of blocked scopes
        """
        payload = json.dumps({
            "engaged": engaged,
            "scopes": sorted(scopes)
        })
        self._storage.upsert_flag("parking_brake", payload, int(time.time()))
        self._cache = self._load()
        self._audit("engaged" if engaged else "disengaged", scopes)
    
    def is_blocked(self, scope: str) -> bool:
        """
        Check if a component scope is blocked.
        
        Args:
            scope: Component scope to check (e.g., "skills", "scheduler")
            
        Returns:
            True if blocked, False if allowed
        """
        st = self._cache
        return st.engaged and ("global" in st.scopes or scope in st.scopes)
    
    def _audit(self, action: str, scopes: Set[str]) -> None:
        """
        Record audit entry for brake state change.
        
        Args:
            action: Action performed ("engaged" or "disengaged")
            scopes: Scopes affected
        """
        self._storage.append_memory(
            kind="safety.audit",
            value={"action": action, "scopes": sorted(scopes)}
        )
