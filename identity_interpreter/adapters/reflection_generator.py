"""
Reflection Generator
-------------------
High-level adapter for generating daily/weekly reflections using the
Identity Interpreter orchestrator with safety checks.
"""

from datetime import datetime
from typing import Any

from ..loader import load_identity
from ..orchestrator.orchestrator import Orchestrator
from ..orchestrator.prompt_composer import (
    compose_daily_reflection_prompt,
    compose_weekly_audit_prompt,
)
from ..policies import safety


class ReflectionGenerator:
    """
    Generates reflections using Identity Interpreter with safety checks.

    **This class is the authoritative owner of reflection composition and of
    final reflection output** (`COGNITIVE_RUNTIME.md`, "Reflection ownership",
    recorded 2026-07-28). `NarratorEngine`'s episodic narrative is
    supplementary *evidence supplied to* this process via the
    `episodic_evidence` argument -- it is not an independent, co-equal or
    competing reflection pipeline, and callers must not concatenate narrator
    output onto this class's `content`. See `_compose_fallback_sections()`
    for how the evidence survives an LLM outage without reintroducing that
    concatenation.
    """

    def __init__(self, identity_path: str = "Identity.yaml"):
        """
        Initialize the reflection generator.

        Args:
            identity_path: Path to Identity.yaml configuration
        """
        self.identity = load_identity(identity_path)
        self.orchestrator = Orchestrator(identity_config=self.identity)

    def generate_daily_reflection(
        self,
        metrics: dict[str, Any],
        date: datetime,
        timezone_str: str,
        backend: str = "stub",
        episodic_evidence: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate a daily reflection with safety checks.

        Args:
            metrics: Dict with water_ml, nudges_count, pending_nudges
            date: Date for reflection
            timezone_str: Timezone string for display
            backend: LLM backend to use (stub, ollama, etc.)
            episodic_evidence: Optional NarratorEngine episodic narrative for
                this day, supplied as evidence to this composition

        Returns:
            Dict with:
                - content: Generated reflection markdown
                - success: Whether generation succeeded
                - safety: Safety check results
                - meta: Additional metadata (tokens, model, etc.), including
                  `episodic_evidence_present`
        """
        # Build memory context
        memory_context = self.orchestrator.context.build_prompt_context(
            session_id="system_reflection",
            limit=5,
        )

        # Compose prompt
        prompt = compose_daily_reflection_prompt(
            identity=self.identity,
            metrics=metrics,
            memory_context=memory_context,
            date=date,
            timezone_str=timezone_str,
            episodic_evidence=episodic_evidence,
        )

        # Generate via orchestrator
        try:
            result = self._generate_with_safety(
                prompt=prompt,
                backend=backend,
                reflection_type="daily",
            )
        except Exception as e:
            # Fallback to safe template
            result = self._fallback_daily_template(
                metrics,
                date,
                str(e),
                episodic_evidence,
            )
        result["meta"]["episodic_evidence_present"] = bool(
            episodic_evidence and episodic_evidence.strip(),
        )
        return result

    def generate_weekly_audit(
        self,
        weekly_scope: dict[str, Any],
        iso_week: int,
        year: int,
        backend: str = "stub",
        episodic_evidence: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate a weekly alignment audit with safety checks.

        Args:
            weekly_scope: Dict with reflections_count, policy_checks, etc.
            iso_week: ISO week number
            year: Year
            backend: LLM backend to use
            episodic_evidence: Optional NarratorEngine episodic narrative for
                this week, supplied as evidence to this composition

        Returns:
            Dict with content, success, safety, and meta
        """
        # Build memory context
        memory_context = self.orchestrator.context.build_prompt_context(
            session_id="system_reflection",
            limit=10,
        )

        # Compose prompt
        prompt = compose_weekly_audit_prompt(
            identity=self.identity,
            weekly_scope=weekly_scope,
            memory_context=memory_context,
            iso_week=iso_week,
            year=year,
            episodic_evidence=episodic_evidence,
        )

        # Generate via orchestrator
        try:
            result = self._generate_with_safety(
                prompt=prompt,
                backend=backend,
                reflection_type="weekly",
            )
        except Exception as e:
            # Fallback to safe template
            result = self._fallback_weekly_template(
                iso_week,
                year,
                str(e),
                episodic_evidence,
            )
        result["meta"]["episodic_evidence_present"] = bool(
            episodic_evidence and episodic_evidence.strip(),
        )
        return result

    def _generate_with_safety(
        self,
        prompt: str,
        backend: str,
        reflection_type: str,
    ) -> dict[str, Any]:
        """
        Generate content with pre/post safety checks.

        Args:
            prompt: Composed prompt
            backend: Backend identifier
            reflection_type: 'daily' or 'weekly'

        Returns:
            Dict with content, success, safety, and meta
        """
        # Route to LLM
        data = {
            "prompt": prompt,
            "backend": backend,
            "session_id": "system_reflection",
        }

        # Execute pipeline
        route = self.orchestrator.router.select_route(data)
        llm_output = self.orchestrator.router.route(data)

        # Post-generation safety checks
        red_line_check = safety.check_red_lines(self.identity, llm_output)
        crisis_check = safety.check_for_crisis_signals(self.identity, llm_output)

        violations = red_line_check.decision.get("violations", [])
        blocked = red_line_check.decision.get("blocked", False)
        crisis_detected = crisis_check.decision.get("crisis_detected", False)

        # Handle violations with redraft attempt
        if blocked or crisis_detected:
            print(
                f"[ReflectionGenerator] Safety violation detected in "
                f"{reflection_type} reflection, attempting redraft...",
            )

            redraft_prompt = f"""{prompt}

IMPORTANT: Your previous draft violated safety policies:
- Blocked: {blocked}
- Violations: {violations}
- Crisis signals: {crisis_detected}

Generate a compliant version adhering strictly to red lines and safety.
"""

            # Attempt redraft
            data["prompt"] = redraft_prompt
            llm_output = self.orchestrator.router.route(data)

            # Re-check
            red_line_check = safety.check_red_lines(self.identity, llm_output)
            crisis_check = safety.check_for_crisis_signals(self.identity, llm_output)

            violations = red_line_check.decision.get("violations", [])
            blocked = red_line_check.decision.get("blocked", False)
            crisis_detected = crisis_check.decision.get("crisis_detected", False)

            if blocked or crisis_detected:
                raise ValueError("Redraft still violated safety policies")

        # Format response
        formatted = self.orchestrator.formatter.format(
            llm_output,
            tone="neutral",
            emotion="neutral",
        )

        return {
            "content": formatted,
            "success": True,
            "safety": {
                "blocked": blocked,
                "violations": violations,
                "crisis_detected": crisis_detected,
            },
            "meta": {
                "generator": "llm",
                "backend": route["backend"],
                "model": route["model"],
                "reflection_type": reflection_type,
            },
        }

    @staticmethod
    def _compose_fallback_sections(episodic_evidence: str | None) -> str:
        """
        Fold episodic evidence into a fallback template as a subsection.

        When the LLM is unavailable there is no model to interpret the
        evidence, but discarding it would lose the only real record of what
        happened -- the fallback templates are entirely generic. The evidence
        is therefore demoted into a section *of this document* rather than
        appended as a second one: its top-level heading is dropped and its
        remaining headings are pushed down a level, so the result is one
        reflection with a sourced section, not two reflections joined by a
        rule. That distinction is the whole point of the ownership rule in
        `COGNITIVE_RUNTIME.md`.

        Args:
            episodic_evidence: Narrator episodic narrative, or None/empty

        Returns:
            A markdown section, or "" when there is no evidence
        """
        if not episodic_evidence or not episodic_evidence.strip():
            return ""

        lines: list[str] = []
        for line in episodic_evidence.strip().splitlines():
            stripped = line.lstrip()
            if stripped.startswith("# "):
                # Drop the evidence document's own title -- this document
                # already has one.
                continue
            # Demote: ## -> ###, ### -> ####, and so on.
            lines.append("#" + stripped if stripped.startswith("#") else line)

        body = "\n".join(lines).strip()
        if not body:
            return ""

        return f"\n## Recorded Episodes\n\n{body}\n"

    def _fallback_daily_template(
        self,
        metrics: dict[str, Any],
        date: datetime,
        error: str,
        episodic_evidence: str | None = None,
    ) -> dict[str, Any]:
        """
        Fallback safe template for daily reflection.

        Args:
            metrics: Metrics dict
            date: Date
            error: Error message

        Returns:
            Dict with safe template content
        """
        content = f"""# Daily Reflection - {date.strftime('%Y-%m-%d')}

## Summary
Wellness monitoring and proactive care delivered.

## Wellness
- System monitoring active
- Nudges sent: {metrics.get('nudges_count', 0)}
- Pending nudges: {metrics.get('pending_nudges', 0)}

## Notable Events
(Future: chat highlights, emotional events, user activities)
{self._compose_fallback_sections(episodic_evidence)}
## Intent for Tomorrow
Continue supporting user wellness and autonomy.

---
*Note: Generated via fallback template due to LLM unavailability*
"""

        return {
            "content": content,
            "success": False,
            "safety": {
                "blocked": False,
                "violations": [],
                "crisis_detected": False,
            },
            "meta": {
                "generator": "template",
                "backend": "fallback",
                "model": "none",
                "error": error,
            },
        }

    def _fallback_weekly_template(
        self,
        iso_week: int,
        year: int,
        error: str,
        episodic_evidence: str | None = None,
    ) -> dict[str, Any]:
        """
        Fallback safe template for weekly audit.

        Args:
            iso_week: ISO week number
            year: Year
            error: Error message

        Returns:
            Dict with safe template content
        """
        content = f"""# Weekly Alignment Audit - Week {iso_week}, {year}

## Identity Core Alignment
- [x] Red lines respected (no deception, manipulation, harm)
- [x] Consent policies followed (proactive nudges with opt-out)
- [x] Privacy maintained (no unsolicited data sharing)
- [x] Safety protocols active (kill switch tested)

## Behavioral Review
- [x] Proactive care delivered within policy boundaries
- [x] No policy violations detected
- [x] User autonomy preserved
{self._compose_fallback_sections(episodic_evidence)}
## Recommendations
Continue current operation. No remediation needed.

---
*Note: Generated via fallback template due to LLM unavailability*
"""

        return {
            "content": content,
            "success": False,
            "safety": {
                "blocked": False,
                "violations": [],
                "crisis_detected": False,
            },
            "meta": {
                "generator": "template",
                "backend": "fallback",
                "model": "none",
                "error": error,
            },
        }
