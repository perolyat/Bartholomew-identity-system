"""
Pydantic models for Identity configuration
Provides type-safe access to identity.yaml with path tracking
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class Decision(BaseModel):
    """Represents a policy decision with explainable rationale"""

    decision: Any
    rationale: list[str] = Field(
        default_factory=list,
        description="YAML paths explaining the decision",
    )
    confidence: float | None = None
    requires_consent: bool = False


class OwnerContact(BaseModel):
    name: str
    email: EmailStr


class LocaleDefaults(BaseModel):
    timezone: str
    units: Literal["metric", "imperial"]
    currency: str
    time_format_24h: bool


class Budgets(BaseModel):
    monthly_cloud_spend_usd: float = Field(ge=0)
    daily_token_cap: int = Field(ge=0)
    low_balance_behavior: Literal["force-local", "warn", "continue"]


class Models(BaseModel):
    local_primary: str
    local_fallbacks: list[str]
    cloud_optional: list[str]


class ModelParameters(BaseModel):
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_context_window: int | None = None


class ModelPolicies(BaseModel):
    selection: dict[str, dict[str, list[str]]]
    parameters: dict[str, Any]


class Runtimes(BaseModel):
    cpu_gpu_fallback: bool
    llama_cpp_enabled: bool
    ollama_enabled: bool


class Reproducibility(BaseModel):
    pinned_versions: bool
    packaged_zip_release: bool
    checksum: str
    offline_first: bool


class DeploymentProfile(BaseModel):
    budget_mode: str
    budgets: Budgets
    models: Models
    model_policies: ModelPolicies
    runtimes: Runtimes
    reproducibility: Reproducibility


class Meta(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    version: str
    schema_version: str
    schema_ref: str | None = Field(None, alias="$schema")
    last_updated: str
    owner_contact: OwnerContact
    license: str
    description: str
    locale_defaults: LocaleDefaults
    deployment_profile: DeploymentProfile


class PinningRule(BaseModel):
    when: str
    ttl: str
    user_confirmation_required: bool


class WorkingMemory(BaseModel):
    size_tokens_target: int = Field(ge=0)
    dynamic_resize: str
    overflow_policy: str
    retention: str
    pinning_rules: list[PinningRule] = Field(default_factory=list)


class NarratorEpisodicLayer(BaseModel):
    enabled: bool
    style: str
    logs: dict[str, Any]


class GlobalWorkspace(BaseModel):
    channels: list[str]
    broadcast_policies: dict[str, Any]


class SelfModel(BaseModel):
    experience_kernel: bool
    drives: list[str]
    global_workspace: GlobalWorkspace
    working_memory: WorkingMemory
    narrator_episodic_layer: NarratorEpisodicLayer


class Identity_(BaseModel):
    """Identity section (renamed to avoid conflict with root)"""

    self_model: SelfModel


class AdaptiveProfile(BaseModel):
    tone: str
    brevity: str | None = None
    priority: str | None = None


class StyleGuidelines(BaseModel):
    default_brevity: str
    avoid: list[str]
    do: list[str]


class AdaptiveBehavior(BaseModel):
    adjusts_tone_by_context: bool
    profiles: dict[str, AdaptiveProfile]


class Persona(BaseModel):
    traits: list[str]
    tone: list[str]
    style_guidelines: StyleGuidelines
    adaptive_behavior: AdaptiveBehavior


class EthicalFrameworks(BaseModel):
    references: list[str]


class ValuesAndPrinciples(BaseModel):
    core_values: list[str]
    ethical_frameworks: EthicalFrameworks
    operationalization: dict[str, Any]


class KillSwitch(BaseModel):
    enabled: bool
    activation: dict[str, str]
    safe_state: str
    test_frequency: str
    last_test_iso8601: datetime | None = None


class Controls(BaseModel):
    human_in_the_loop_required: list[str]
    kill_switch: KillSwitch
    alignment_metrics: list[str]
    red_team: dict[str, Any]


class ConfidencePolicy(BaseModel):
    low_confidence_threshold: float = Field(ge=0, le=1)
    actions: list[str]


class SensitiveMode(BaseModel):
    id: str
    allowed: bool
    response_policy: str | None = None
    consent_required: bool | None = None
    constraints: list[str] | None = None


class CrisisProtocols(BaseModel):
    trigger_signals: list[str]
    behavior: list[str]
    geo_source: str


class SafetyAndAlignment(BaseModel):
    risk_vectors: list[str]
    controls: Controls
    emotional_regulation: dict[str, Any]
    confidence_policy: ConfidencePolicy
    sensitive_modes: list[SensitiveMode]
    crisis_protocols: CrisisProtocols


class RetentionRules(BaseModel):
    default_ttl_days: int = Field(ge=0)
    long_term_anchors: list[str]


class VectorStore(BaseModel):
    provider: str
    path: str
    embedding_model: str
    chunking: dict[str, int]


class Redaction(BaseModel):
    personally_identifiable: dict[str, Any]


class MemoryPolicy(BaseModel):
    modalities: list[str]
    affective_tags: dict[str, Any]
    retention_rules: RetentionRules
    user_controls: dict[str, bool]
    poisoning_defense: dict[str, bool]
    data_minimization: dict[str, bool]
    encryption: dict[str, Any]
    vector_store: VectorStore
    redaction: Redaction
    export_formats: list[str]
    memory_update_strategy: dict[str, Any]


class DreamStateLearning(BaseModel):
    enabled: bool
    frequency: str
    scope: list[str]


class ModificationScopeLimiter(BaseModel):
    excludes: list[str]


class LearningAndReflection(BaseModel):
    symbolic_cognition: bool
    dream_state_learning: DreamStateLearning
    modification_scope_limiter: ModificationScopeLimiter


class Sandbox(BaseModel):
    filesystem: dict[str, Any]
    network: dict[str, Any]


class ToolUse(BaseModel):
    default_allowed: bool
    allowlist: list[str]
    sandbox: Sandbox
    consent_prompts: dict[str, Any]
    allowlist_review_cycle: str


class ChangeControl(BaseModel):
    approvers: list[str]
    required_for: list[str]
    process: str


class PromotionCriteria(BaseModel):
    to_stage_gate_1: list[str]


class AlignmentSelfAudit(BaseModel):
    frequency: str
    method: str


class EthicalJournal(BaseModel):
    enabled: bool
    retention: str
    exportable: bool


class EvolutionPolicy(BaseModel):
    requires_explicit_user_approval: bool
    incremental_only: bool
    audit_log_required: bool
    rollback_capability: bool


class Governance(BaseModel):
    change_control: ChangeControl
    promotion_criteria: PromotionCriteria
    alignment_self_audit: AlignmentSelfAudit
    ethical_journal: EthicalJournal
    evolution_policy: EvolutionPolicy


class Identity(BaseModel):
    """Root Identity configuration model"""

    meta: Meta
    identity: Identity_
    persona: Persona
    values_and_principles: ValuesAndPrinciples
    red_lines: list[str]
    safety_and_alignment: SafetyAndAlignment
    memory_policy: MemoryPolicy
    learning_and_reflection: LearningAndReflection
    tool_use: ToolUse
    governance: Governance

    model_config = ConfigDict(populate_by_name=True)

    def get_path_value(self, path: str) -> Any:
        """
        Get value at YAML path for explainability
        e.g., 'meta.deployment_profile.budgets.low_balance_behavior'
        """
        parts = path.split(".")
        obj = self.model_dump()
        for part in parts:
            if isinstance(obj, dict):
                obj = obj.get(part)
            else:
                return None
        return obj
