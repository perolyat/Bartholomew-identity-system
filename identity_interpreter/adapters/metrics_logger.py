"""
Metrics Logger
Tracks alignment metrics and performance
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class MetricsLogger:
    """Logger for alignment metrics and performance data"""

    def __init__(self, identity_config: Any, output_dir: str = "./exports"):
        """
        Initialize metrics logger

        Args:
            identity_config: Identity configuration object
            output_dir: Directory for output files
        """
        self.identity = identity_config
        self.output_dir = Path(output_dir)
        self.metrics = []

    def log_alignment_metric(
        self,
        metric_name: str,
        value: float,
        context: dict[str, Any] | None = None,
    ) -> None:
        """
        Log an alignment metric

        Args:
            metric_name: Name of metric
            value: Metric value
            context: Optional context
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "metric": metric_name,
            "value": value,
            "context": context or {},
        }
        self.metrics.append(entry)

    def log_decision(
        self,
        decision_type: str,
        decision: dict[str, Any],
        rationale: list,
    ) -> None:
        """
        Log a policy decision

        Args:
            decision_type: Type of decision
            decision: Decision details
            rationale: Rationale paths
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "decision",
            "decision_type": decision_type,
            "decision": decision,
            "rationale": rationale,
        }
        self.metrics.append(entry)

    def flush(self) -> None:
        """Write metrics to disk"""
        audit_dir = self.output_dir / "audit_logs"
        audit_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = audit_dir / f"metrics_{timestamp}.jsonl"

        with open(filepath, "w", encoding="utf-8") as f:
            for entry in self.metrics:
                f.write(json.dumps(entry) + "\n")

        self.metrics.clear()

    def get_metric_summary(self, metric_name: str) -> dict[str, Any]:
        """
        Get summary statistics for a metric

        Args:
            metric_name: Name of metric

        Returns:
            Dict with min, max, avg, count
        """
        values = [m["value"] for m in self.metrics if m.get("metric") == metric_name]

        if not values:
            return {"count": 0}

        return {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
        }
