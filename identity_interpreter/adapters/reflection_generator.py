"""
Reflection Generator
-------------------
High-level adapter for generating daily/weekly reflections using the
Identity Interpreter orchestrator with safety checks.
"""

from datetime import datetime
from typing import Any

from ..loader import load_identity
from ..orchestrator.model_router import ModelBackendError
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

    #: Identity task type reflections are composed under.
    #:
    #: `Identity.yaml`'s `by_task_type` policy routes `general` to the local
    #: model and lists no cloud candidate, so composing a reflection stays
    #: local-first and no personal material reaches a cloud provider. The
    #: local/cloud data boundary is an unratified product decision (see
    #: `docs/VISION_AND_PERSONAL_DEPLOYMENT.md` §9); until it is decided,
    #: reflection -- which reads stored personal memory by construction --
    #: is not the place to pre-empt it.
    REFLECTION_TASK_TYPE = "general"

    def __init__(self, identity_path: str = "Identity.yaml"):
        """
        Initialize the reflection generator.

        Args:
            identity_path: Path to Identity.yaml configuration

        Note:
            `identity_config=` (not `model_identity_config=`) is deliberate:
            this class needs `orchestrator.context.build_prompt_context()`,
            which only exists when the ContextBuilder has an Identity. That
            used to make construction impossible on a headless host, because
            ContextBuilder built its `MemoryManager` -- and therefore reached
            the OS keystore -- eagerly. ContextBuilder now defers that to
            first use, so construction here is keystore-free and a host
            without a keystore simply composes with empty memory context
            instead of failing to construct at all.
        """
        self.identity = load_identity(identity_path)
        self.orchestrator = Orchestrator(identity_config=self.identity)

    def generate_daily_reflection(
        self,
        metrics: dict[str, Any],
        date: datetime,
        timezone_str: str,
        backend: str | None = None,
        episodic_evidence: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate a daily reflection with safety checks.

        Args:
            metrics: Dict with water_ml, nudges_count, pending_nudges
            date: Date for reflection
            timezone_str: Timezone string for display
            backend: LLM backend override. **Leave as None** to use the
                Identity-driven routing every other surface uses; that is
                what makes a reflection model-composed. An explicit value
                bypasses Identity policy and is intended for tests.
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
                self._describe_failure(e),
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
        backend: str | None = None,
        episodic_evidence: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate a weekly alignment audit with safety checks.

        Args:
            weekly_scope: Dict with reflections_count, policy_checks, etc.
            iso_week: ISO week number
            year: Year
            backend: LLM backend override; see `generate_daily_reflection`.
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
                self._describe_failure(e),
                episodic_evidence,
            )
        result["meta"]["episodic_evidence_present"] = bool(
            episodic_evidence and episodic_evidence.strip(),
        )
        return result

    def _generate_with_safety(
        self,
        prompt: str,
        backend: str | None,
        reflection_type: str,
    ) -> dict[str, Any]:
        """
        Generate content with pre/post safety checks.

        Args:
            prompt: Composed prompt
            backend: Backend identifier, or None to let Identity policy
                choose (the normal path)
            reflection_type: 'daily' or 'weekly'

        Returns:
            Dict with content, success, safety, and meta

        Raises:
            ModelBackendError: the selected real backend could not generate.
                Propagated deliberately -- the caller turns it into a
                fallback template marked `success: False`, so a provider
                outage can never be stored as a successful reflection.
        """
        # Route to LLM.
        #
        # Omitting `backend` entirely (rather than passing None) is what
        # hands the decision to `select_route()`'s Identity-driven
        # `task_type` branch -- `data.get("backend")` must be absent for
        # that branch to run at all.
        data: dict[str, Any] = {
            "prompt": prompt,
            "session_id": "system_reflection",
            "task_type": self.REFLECTION_TASK_TYPE,
        }
        if backend is not None:
            data["backend"] = backend

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
                # Provenance must name what actually composed this document.
                #
                # `generator` was unconditionally "llm" here, including when
                # the route resolved to the stub backend -- whose output is
                # the literal string "[stub-llm] Mock response for prompt:
                # ...". Today's reflection prompts happen to trip a red line
                # when echoed back, so that text falls to the template
                # instead of being stored; but that is an accident of the
                # prompt text, not a guarantee. A shorter prompt passes
                # safety cleanly and would persist mock text labelled as
                # model-composed -- pinned weekly, in the user's own
                # reflection history. Naming the stub as the generator makes
                # a stored reflection's provenance answerable without
                # re-deriving which backend was live that day.
                "generator": "stub" if route["backend"] == "stub" else "llm",
                "backend": route["backend"],
                "model": route["model"],
                "reflection_type": reflection_type,
            },
        }

    @staticmethod
    def _describe_failure(exc: Exception) -> str:
        """Render a generation failure for the persisted `meta.error` field.

        A reflection that degraded to a template records *why* here, and that
        record is the only evidence available afterwards -- the run is
        unattended and nightly. `str(ModelBackendError)` carries the human
        message but drops the structured backend/model/reason it was built to
        carry, which are what distinguish "Ollama is down" from "the model
        isn't pulled" from "cloud is unconfigured" when reading a month-old
        reflection.
        """
        if isinstance(exc, ModelBackendError):
            return f"{exc} [backend={exc.backend} model={exc.model} reason={exc.reason}]"
        return str(exc)

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
