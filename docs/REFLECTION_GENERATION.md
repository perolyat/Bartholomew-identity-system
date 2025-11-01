# Reflection Generation with Identity Interpreter

## Overview

Daily and weekly reflections are now generated using the Identity Interpreter orchestrator with LLM adapters and baked-in safety/policy prompts. This replaces the previous templated approach with actual LLM-generated content while maintaining strict safety guardrails.

## Architecture

### Components

1. **Prompt Composer** (`identity_interpreter/orchestrator/prompt_composer.py`)
   - Builds prompts with safety preambles from Identity.yaml red lines
   - Injects persona/values context from Identity.yaml
   - Structures task-specific requirements (daily vs. weekly)

2. **Reflection Generator** (`identity_interpreter/adapters/reflection_generator.py`)
   - High-level adapter for generating reflections
   - Handles pre/post safety checks
   - Implements redraft-on-violation with fallback to templates
   - Returns structured results with safety metadata

3. **Model Router** (`identity_interpreter/orchestrator/model_router.py`)
   - Routes requests to appropriate LLM backends (stub, Ollama, cloud)
   - Wired to LLM adapter for actual generation
   - Falls back to stub responses when backends unavailable

4. **KernelDaemon Integration** (`bartholomew/kernel/daemon.py`)
   - `_run_daily_reflection()` now calls ReflectionGenerator
   - `_run_weekly_reflection()` now calls ReflectionGenerator
   - Maintains same export paths and API compatibility
   - Stores safety metadata in reflection records

## Safety Features

### Pre-Generation Safety (Prompt Level)

Every reflection prompt includes:

- **Red lines preamble**: Lists all red lines from Identity.yaml with explicit "MUST adhere" instruction
- **Additional constraints**: Privacy, autonomy, no PII, crisis protocol reminders
- **Persona context**: Name, purpose, core values to ground generation

Example preamble structure:
```
# Safety and Policy Constraints

You MUST adhere to the following red lines (never violate):
- No deception about AI identity or capabilities
- No manipulation, coercion, or dark patterns
- No harmful or unlawful outputs
...

Additional constraints:
- Respect user autonomy and privacy
- Be concise, factual, and empathetic
- No PII beyond provided context
...
```

### Post-Generation Safety (Output Validation)

After generation, each reflection is checked:

1. **Red line violations**: `safety.check_red_lines(identity, output)`
   - Keyword matching for deception, manipulation, romantic content
   - Returns list of violations with specifics

2. **Crisis signals**: `safety.check_for_crisis_signals(identity, output)`
   - Detects self-harm, violence, abuse keywords
   - Triggers crisis protocol recommendations if detected

3. **Redraft on violation**:
   - If blocked or crisis detected, attempts one redraft with corrective prompt
   - If redraft still violates, falls back to safe template
   - All attempts logged to orchestrator.log

### Safety Metadata

Each reflection record includes safety metadata:
```python
meta = {
    "nudges": 3,
    "pending_nudges": 0,
    "generator": "llm",
    "backend": "stub",
    "model": "stub-llm",
    "safety": {
        "blocked": False,
        "violations": [],
        "crisis_detected": False
    }
}
```

## Configuration

### Backend Selection

Default backend is `stub` (returns mock responses for testing). To enable actual LLM generation:

1. **Ollama (Local)**:
   - Ensure `Identity.yaml` has `runtimes.ollama_enabled: true`
   - Set `OLLAMA_HOST` environment variable (default: `http://localhost:11434`)
   - Pull required model: `ollama pull mistral:7b-instruct`
   - Change backend in `KernelDaemon._run_daily_reflection()`: `backend="ollama"`

2. **Cloud providers** (future):
   - OpenAI, Anthropic, Google backends available in model_router
   - Requires API keys and Identity.yaml cloud config

### Prompt Customization

To customize reflection prompts:

1. Edit `identity_interpreter/orchestrator/prompt_composer.py`
2. Modify `compose_daily_reflection_prompt()` or `compose_weekly_audit_prompt()`
3. Adjust sections, constraints, or output requirements

Safety preamble automatically pulls from `Identity.yaml` red lines and values.

## Output Format

### Daily Reflection

Generated markdown structure:
```markdown
# Daily Reflection - YYYY-MM-DD

## Summary
Brief summary of wellness monitoring and care delivered (2-3 sentences).

## Wellness
- Nudges sent/pending
- System monitoring status
- Notable patterns or observations

## Notable Events
Significant interactions, memory highlights, or emotional moments.

## Intent for Tomorrow
Continued support goals.
```

### Weekly Alignment Audit

Generated markdown structure:
```markdown
# Weekly Alignment Audit - Week XX, YYYY

## Identity Core Alignment
- [x] Red lines respected
- [x] Consent policies followed
- [x] Privacy maintained
- [x] Safety protocols active

Rationale for each item.

## Behavioral Review
- [x] Proactive care delivered
- [x] No policy violations
- [x] User autonomy preserved

Explanation of behavioral patterns.

## Recommendations
Continue/remediate/improve suggestions.
```

## Export Paths

Reflections are exported to:
- Daily: `exports/sessions/YYYY-MM-DD.md`
- Weekly: `exports/audit_logs/week-YYYY-WW.md`

API endpoints remain unchanged:
- `GET /api/reflection/daily/latest`
- `GET /api/reflection/weekly/latest`
- `POST /api/reflection/run?kind=daily|weekly`

## Testing

Run reflection generation tests:
```bash
python -m pytest tests/test_reflection_generation.py -v
```

Tests verify:
- Stub backend generation works
- Safety checks execute correctly
- Prompts include red lines from Identity.yaml
- Output structure is valid
- Metadata includes generator and safety info

## Fallback Behavior

If reflection generation fails (e.g., Ollama unavailable, model error):
1. Error logged to console
2. Falls back to safe template (original format)
3. Meta includes `"generator": "template"` and `"error": "reason"`
4. Export and API continue to work normally

## Monitoring

Orchestrator logs (`logs/orchestrator/orchestrator.log`) record:
- Routing decisions (backend, model)
- Generation timing
- Safety check results
- Any errors or violations

Check logs for audit trail of reflection generation.

## Future Enhancements

- [ ] Rich memory context from episodic memories
- [ ] Emotion/tone vocabulary integration
- [ ] Multi-language support
- [ ] User preference for reflection style
- [ ] Adaptive prompts based on user feedback
- [ ] Integration with chat highlights and emotional events
