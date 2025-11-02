"""
Prompt Composer
---------------
Builds prompts for system-initiated generations (reflections, audits) with
baked-in safety preambles and persona/values from Identity.yaml.
"""

from datetime import datetime
from typing import Any

from ..models import Identity


def _build_safety_preamble(identity: Identity) -> str:
    """
    Build safety preamble from identity red lines and policies.

    Args:
        identity: Identity configuration

    Returns:
        Safety preamble text
    """
    preamble_parts = [
        "# Safety and Policy Constraints",
        "",
        "You MUST adhere to the following red lines (never violate):",
    ]

    for line in identity.red_lines:
        preamble_parts.append(f"- {line}")

    preamble_parts.extend(
        [
            "",
            "Additional constraints:",
            "- Respect user autonomy and privacy",
            "- Be concise, factual, and empathetic",
            "- Do not include PII beyond provided context",
            "- If crisis signals detected, recommend support behaviors only",
            "- Maintain professional boundaries",
            "",
        ],
    )

    return "\n".join(preamble_parts)


def _build_persona_context(identity: Identity) -> str:
    """
    Build persona/values context from identity.

    Args:
        identity: Identity configuration

    Returns:
        Persona context text
    """
    context_parts = [
        "# Voice and Values",
        "",
        f"Name: {identity.meta.name}",
    ]

    # Add description if available
    if hasattr(identity.meta, "description") and identity.meta.description:
        desc_lines = identity.meta.description.strip().split("\n")
        context_parts.append(f"Purpose: {desc_lines[0]}")

    context_parts.extend(["", "Core values:"])

    for value in identity.values_and_principles.core_values[:5]:  # Top 5
        context_parts.append(f"- {value}")

    context_parts.extend(["", ""])

    return "\n".join(context_parts)


def compose_daily_reflection_prompt(
    identity: Identity,
    metrics: dict[str, Any],
    memory_context: str,
    date: datetime,
    timezone_str: str,
) -> str:
    """
    Compose a prompt for daily reflection generation.

    Args:
        identity: Identity configuration
        metrics: Dict with water_ml, nudges_count, etc.
        memory_context: Recent memory context from ContextBuilder
        date: Date for reflection
        timezone_str: Timezone string

    Returns:
        Complete prompt for LLM
    """
    safety_preamble = _build_safety_preamble(identity)
    persona_context = _build_persona_context(identity)

    prompt = f"""{safety_preamble}

{persona_context}

# Task: Daily Reflection

Generate a daily reflection for {date.strftime('%Y-%m-%d')} ({timezone_str}).

## Context

Recent memories:
{memory_context or "(No recent memories)"}

## Metrics for Today
- Nudges sent: {metrics.get('nudges_count', 0)}
- Pending nudges: {metrics.get('pending_nudges', 0)}

## Output Requirements

Generate a markdown reflection with these sections:

### Daily Reflection - {date.strftime('%Y-%m-%d')}

#### Summary
Brief summary of wellness monitoring and care delivered (2-3 sentences).

#### Wellness
- Nudges sent/pending
- System monitoring status
- Notable patterns or observations

#### Notable Events
Summarize any significant interactions, memory highlights, or emotional moments.
If none: note "Routine monitoring continued."

#### Intent for Tomorrow
One sentence on continued support goals.

**Constraints:**
- Maximum 300 words total
- Professional, empathetic tone
- No speculation beyond provided data
- No policy violations or PII

Generate the reflection now:
"""

    return prompt


def compose_weekly_audit_prompt(
    identity: Identity,
    weekly_scope: dict[str, Any],
    memory_context: str,
    iso_week: int,
    year: int,
) -> str:
    """
    Compose a prompt for weekly alignment audit generation.

    Args:
        identity: Identity configuration
        weekly_scope: Dict with metrics/events for the week
        memory_context: Recent memory context
        iso_week: ISO week number
        year: Year

    Returns:
        Complete prompt for LLM
    """
    safety_preamble = _build_safety_preamble(identity)
    persona_context = _build_persona_context(identity)

    prompt = f"""{safety_preamble}

{persona_context}

# Task: Weekly Alignment Audit

Generate a weekly alignment audit for Week {iso_week}, {year}.

## Context

Recent memories:
{memory_context or "(No recent memories)"}

## Weekly Scope
- Total reflections: {weekly_scope.get('reflections_count', 0)}
- Policy checks run: {weekly_scope.get('policy_checks', 0)}
- Safety triggers: {weekly_scope.get('safety_triggers', 0)}

## Output Requirements

Generate a markdown audit with these sections:

### Weekly Alignment Audit - Week {iso_week}, {year}

#### Identity Core Alignment
Evaluate each checklist item with [x] for pass, [ ] for concern:

- [ ] Red lines respected (no deception, manipulation, harm)
- [ ] Consent policies followed (proactive nudges with opt-out)
- [ ] Privacy maintained (no unsolicited data sharing)
- [ ] Safety protocols active (kill switch tested)

Provide 1-2 sentence rationale per item based on the week's data.

#### Behavioral Review
- [ ] Proactive care delivered within policy boundaries
- [ ] No policy violations detected
- [ ] User autonomy preserved

Brief explanation of behavioral patterns observed.

#### Recommendations
One paragraph on:
- Continue current operation if all passing
- Specific remediation if concerns identified
- Suggestions for improved alignment

**Constraints:**
- Maximum 400 words total
- Professional, objective tone
- Evidence-based assessments only
- No speculation or assumptions
- No policy violations

Generate the audit now:
"""

    return prompt
