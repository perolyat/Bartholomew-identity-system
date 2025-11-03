"""
Storage Adapter
Handles audit logs and ethical journal storage
Now integrated with comprehensive memory management
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory_manager import MemoryManager


class StorageAdapter:
    """Handles persistent storage of logs, journals, and memories"""

    def __init__(
        self,
        identity_config,
        output_dir: str = "./exports",
    ):
        """
        Initialize storage adapter

        Args:
            identity_config: Identity configuration object
            output_dir: Base directory for outputs
        """
        self.identity = identity_config
        self.output_dir = Path(output_dir)
        self.audit_dir = self.output_dir / "audit_logs"
        self.journal_dir = self.output_dir / "ethical_journal"
        self.sessions_dir = self.output_dir / "sessions"

        # Create directories
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        # Initialize memory manager
        self.memory = MemoryManager(identity_config, data_dir="./data")

    def write_audit_log(
        self,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """
        Write audit log entry

        Args:
            event_type: Type of event
            data: Event data
        """
        timestamp = datetime.now(timezone.utc)
        entry = {
            "timestamp": timestamp.isoformat(),
            "event_type": event_type,
            "data": data,
        }

        # Append to daily log file
        filename = f"audit_{timestamp.strftime('%Y%m%d')}.jsonl"
        filepath = self.audit_dir / filename

        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def write_journal_entry(
        self,
        entry_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Write ethical journal entry

        Args:
            entry_type: Type of journal entry
            content: Entry content
            metadata: Optional metadata
        """
        timestamp = datetime.now(timezone.utc)
        entry = {
            "timestamp": timestamp.isoformat(),
            "type": entry_type,
            "content": content,
            "metadata": metadata or {},
        }

        # Append to monthly journal file
        filename = f"journal_{timestamp.strftime('%Y%m')}.jsonl"
        filepath = self.journal_dir / filename

        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def create_session_snapshot(
        self,
        session_id: str,
        data: dict[str, Any],
    ) -> Path:
        """
        Create session snapshot

        Args:
            session_id: Session identifier
            data: Session data

        Returns:
            Path to snapshot file
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"session_{session_id}_{timestamp}.json"
        filepath = self.sessions_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        return filepath
