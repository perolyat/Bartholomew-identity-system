"""
Consent Gate for Memory Retrieval
Implements privacy-aware filtering for FTS and vector search results
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from bartholomew.kernel.memory_rules import MemoryRulesEngine


logger = logging.getLogger(__name__)


class ConsentGate:
    """
    Privacy gate for memory retrieval

    Enforces consent and privacy rules by:
    - Excluding never_store memories (allow_store=false)
    - Excluding ask_before_store memories without consent
    - Marking context_only memories (recall_policy=context_only)
    """

    def __init__(self, db_path: str, rules_engine: MemoryRulesEngine | None = None):
        """
        Initialize consent gate

        Args:
            db_path: Path to SQLite database
            rules_engine: Memory rules engine (uses singleton if None)
        """
        self.db_path = db_path

        if rules_engine is None:
            from bartholomew.kernel.memory_rules import _rules_engine

            self.rules_engine = _rules_engine
        else:
            self.rules_engine = rules_engine

    def get_consented_memory_ids(self) -> set[int]:
        """
        Get set of memory IDs with explicit consent

        Returns:
            Set of memory IDs that have consent records
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("SELECT memory_id FROM memory_consent")
            rows = cursor.fetchall()
            return {row[0] for row in rows}
        except Exception as e:
            logger.error(f"Failed to load consented memory IDs: {e}")
            return set()
        finally:
            if conn:
                conn.close()

    def load_memory_metadata(self, memory_ids: list[int]) -> dict[int, dict[str, Any]]:
        """
        Load memory metadata for rule evaluation

        Args:
            memory_ids: List of memory IDs to load

        Returns:
            Dict mapping memory_id to memory metadata dict
        """
        if not memory_ids:
            return {}

        placeholders = ",".join("?" * len(memory_ids))
        query = f"""
            SELECT id, kind, key, value, summary, ts
            FROM memories
            WHERE id IN ({placeholders})
        """

        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, memory_ids)
            rows = cursor.fetchall()

            metadata = {}
            for row in rows:
                metadata[row["id"]] = {
                    "id": row["id"],
                    "kind": row["kind"],
                    "key": row["key"],
                    "value": row["value"],
                    "summary": row["summary"],
                    "ts": row["ts"],
                }

            return metadata
        except Exception as e:
            logger.error(f"Failed to load memory metadata: {e}")
            return {}
        finally:
            if conn:
                conn.close()

    def filter_memory_ids(
        self,
        memory_ids: list[int],
        consented_ids: set[int] | None = None,
    ) -> dict[int, dict[str, Any]]:
        """
        Filter memory IDs based on consent and privacy rules

        Returns dict with filtered memory IDs and their policy metadata:
        {
            memory_id: {
                "include": bool,           # Include in results
                "context_only": bool,      # Mark as context-only
                "recall_policy": str,      # Recall policy from rules
                "privacy_class": str       # Privacy class from rules
            }
        }

        Args:
            memory_ids: List of memory IDs to filter
            consented_ids: Optional pre-loaded set of consented IDs

        Returns:
            Dict mapping memory_id to policy metadata
        """
        if not memory_ids:
            return {}

        # Load consented IDs if not provided
        if consented_ids is None:
            consented_ids = self.get_consented_memory_ids()

        # Load memory metadata
        metadata = self.load_memory_metadata(memory_ids)

        # Evaluate rules for each memory
        results = {}
        for memory_id in memory_ids:
            mem_data = metadata.get(memory_id)
            if mem_data is None:
                # Memory not found, exclude
                results[memory_id] = {
                    "include": False,
                    "context_only": False,
                    "recall_policy": None,
                    "privacy_class": None,
                }
                continue

            # Evaluate rules
            evaluated = self.rules_engine.evaluate(mem_data)

            # Check if should be included
            include = True

            # Rule 1: never_store (allow_store=false)
            if not evaluated.get("allow_store", True):
                include = False
                logger.debug(f"Excluding memory {memory_id}: never_store policy")

            # Rule 2: ask_before_store without consent
            if evaluated.get("requires_consent", False):
                if memory_id not in consented_ids:
                    include = False
                    logger.debug(
                        f"Excluding memory {memory_id}: requires_consent without consent record",
                    )

            # Extract policy metadata
            recall_policy = evaluated.get("recall_policy")
            context_only = recall_policy == "context_only"

            results[memory_id] = {
                "include": include,
                "context_only": context_only,
                "recall_policy": recall_policy,
                "privacy_class": evaluated.get("privacy_class"),
            }

        return results

    def apply_to_fts_results(
        self,
        fts_results: list[dict[str, Any]],
        consented_ids: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Apply consent gate to FTS search results

        Filters results and marks context_only memories.

        Args:
            fts_results: List of FTS result dicts with 'id' key
            consented_ids: Optional pre-loaded set of consented IDs

        Returns:
            Filtered list of FTS results with context_only flag added
        """
        if not fts_results:
            return []

        # Extract memory IDs
        memory_ids = [r["id"] for r in fts_results]

        # Filter based on consent
        policy_data = self.filter_memory_ids(memory_ids, consented_ids)

        # Apply filtering and marking
        filtered = []
        for result in fts_results:
            memory_id = result["id"]
            policy = policy_data.get(memory_id, {})

            if not policy.get("include", True):
                continue

            # Add context_only flag
            result["context_only"] = policy.get("context_only", False)
            result["recall_policy"] = policy.get("recall_policy")

            filtered.append(result)

        logger.debug(
            f"Consent gate: {len(fts_results)} -> {len(filtered)} "
            f"(filtered {len(fts_results) - len(filtered)})",
        )

        return filtered

    def apply_to_vector_results(
        self,
        vector_results: list[tuple],
        consented_ids: set[int] | None = None,
    ) -> list[tuple]:
        """
        Apply consent gate to vector search results

        Filters (memory_id, score) tuples based on consent rules.
        Note: context_only marking happens in retrieval layer.

        Args:
            vector_results: List of (memory_id, score) tuples
            consented_ids: Optional pre-loaded set of consented IDs

        Returns:
            Filtered list of (memory_id, score) tuples
        """
        if not vector_results:
            return []

        # Extract memory IDs
        memory_ids = [r[0] for r in vector_results]

        # Filter based on consent
        policy_data = self.filter_memory_ids(memory_ids, consented_ids)

        # Apply filtering
        filtered = []
        for memory_id, score in vector_results:
            policy = policy_data.get(memory_id, {})

            if policy.get("include", True):
                filtered.append((memory_id, score))

        logger.debug(
            f"Consent gate: {len(vector_results)} -> {len(filtered)} "
            f"(filtered {len(vector_results) - len(filtered)})",
        )

        return filtered

    def get_memory_policy(
        self,
        memory_id: int,
        consented_ids: set[int] | None = None,
    ) -> dict[str, Any]:
        """
        Get policy metadata for a single memory

        Args:
            memory_id: Memory ID to check
            consented_ids: Optional pre-loaded set of consented IDs

        Returns:
            Policy dict with keys: include, context_only, recall_policy,
            privacy_class
        """
        results = self.filter_memory_ids([memory_id], consented_ids)
        return results.get(
            memory_id,
            {"include": False, "context_only": False, "recall_policy": None, "privacy_class": None},
        )
