"""
Prometheus metrics endpoint for kernel observability.

Uses shared metrics registry with duplicate collector protection
to prevent registration errors during module reloads.

Stage 3 metrics include:
- Experience Kernel: affect, drives, goals, attention
- Global Workspace: events, subscribers
- Working Memory: items, tokens, evictions
- Narrator: episodes
- Persona: switches, active persona
"""

import os
import sys
import time

from fastapi import APIRouter, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    generate_latest,
)


# Import the shared metrics registry
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "bartholomew",
        "kernel",
    ),
)
from metrics_registry import get_metrics_registry  # noqa: E402


router = APIRouter(tags=["metrics"])

# Get shared registry (singleton, safe for reloads)
REGISTRY = get_metrics_registry()

# Module-level flag to ensure metrics are only registered once
_metrics_registered = False

# =============================================================================
# Metrics Holders (will be initialized once)
# =============================================================================

# Existing metrics
KERNEL_TICKS_TOTAL = None
BARTHOLOMEW_TICKS_TOTAL = None
KERNEL_UPTIME_SECONDS = None

# Stage 3: Experience Kernel metrics
AFFECT_VALENCE = None
AFFECT_AROUSAL = None
AFFECT_ENERGY = None
DRIVE_ACTIVATION = None
GOALS_ACTIVE = None
ATTENTION_INTENSITY = None
SNAPSHOTS_PERSISTED_TOTAL = None

# Stage 3: Global Workspace metrics
WORKSPACE_EVENTS_TOTAL = None
WORKSPACE_SUBSCRIBERS = None

# Stage 3: Working Memory metrics
WORKING_MEMORY_ITEMS = None
WORKING_MEMORY_TOKENS = None
WORKING_MEMORY_EVICTIONS_TOTAL = None

# Stage 3: Narrator metrics
EPISODES_TOTAL = None
EPISODES_PERSISTED = None

# Stage 3: Persona metrics
PERSONA_SWITCHES_TOTAL = None
PERSONA_ACTIVE = None


def _init_metrics_once():
    """
    Initialize metrics collectors exactly once.

    Handles duplicate registration gracefully by catching ValueError
    when metrics are already registered (can happen when module is
    imported via different paths in tests).
    """
    global _metrics_registered
    global KERNEL_TICKS_TOTAL, BARTHOLOMEW_TICKS_TOTAL, KERNEL_UPTIME_SECONDS
    global AFFECT_VALENCE, AFFECT_AROUSAL, AFFECT_ENERGY
    global DRIVE_ACTIVATION, GOALS_ACTIVE, ATTENTION_INTENSITY
    global SNAPSHOTS_PERSISTED_TOTAL
    global WORKSPACE_EVENTS_TOTAL, WORKSPACE_SUBSCRIBERS
    global WORKING_MEMORY_ITEMS, WORKING_MEMORY_TOKENS
    global WORKING_MEMORY_EVICTIONS_TOTAL
    global EPISODES_TOTAL, EPISODES_PERSISTED
    global PERSONA_SWITCHES_TOTAL, PERSONA_ACTIVE

    if _metrics_registered:
        return

    try:
        # =====================================================================
        # Existing metrics
        # =====================================================================
        KERNEL_TICKS_TOTAL = Counter(
            "kernel_ticks_total",
            "Total kernel ticks observed by API bridge, by drive.",
            ["drive"],
            registry=REGISTRY,
        )

        BARTHOLOMEW_TICKS_TOTAL = Counter(
            "bartholomew_ticks_total",
            "Total Bartholomew ticks observed by API bridge, by drive.",
            ["drive"],
            registry=REGISTRY,
        )

        KERNEL_UPTIME_SECONDS = Gauge(
            "kernel_uptime_seconds",
            "Process uptime in seconds since API bridge start.",
            registry=REGISTRY,
        )

        # =====================================================================
        # Stage 3: Experience Kernel metrics
        # =====================================================================
        AFFECT_VALENCE = Gauge(
            "bartholomew_affect_valence",
            "Current emotional valence (-1 negative to 1 positive).",
            registry=REGISTRY,
        )

        AFFECT_AROUSAL = Gauge(
            "bartholomew_affect_arousal",
            "Current emotional arousal (0 calm to 1 activated).",
            registry=REGISTRY,
        )

        AFFECT_ENERGY = Gauge(
            "bartholomew_affect_energy",
            "Current energy level (0 depleted to 1 full).",
            registry=REGISTRY,
        )

        DRIVE_ACTIVATION = Gauge(
            "bartholomew_drive_activation",
            "Current activation level per drive (0 to 1).",
            ["drive_id"],
            registry=REGISTRY,
        )

        GOALS_ACTIVE = Gauge(
            "bartholomew_goals_active",
            "Number of currently active goals.",
            registry=REGISTRY,
        )

        ATTENTION_INTENSITY = Gauge(
            "bartholomew_attention_intensity",
            "Current attention intensity (0 to 1).",
            registry=REGISTRY,
        )

        SNAPSHOTS_PERSISTED_TOTAL = Counter(
            "bartholomew_snapshots_persisted_total",
            "Total experience snapshots persisted to database.",
            registry=REGISTRY,
        )

        # =====================================================================
        # Stage 3: Global Workspace metrics
        # =====================================================================
        WORKSPACE_EVENTS_TOTAL = Counter(
            "bartholomew_workspace_events_total",
            "Total events published to global workspace.",
            ["channel", "event_type"],
            registry=REGISTRY,
        )

        WORKSPACE_SUBSCRIBERS = Gauge(
            "bartholomew_workspace_subscribers",
            "Number of active subscribers per channel.",
            ["channel"],
            registry=REGISTRY,
        )

        # =====================================================================
        # Stage 3: Working Memory metrics
        # =====================================================================
        WORKING_MEMORY_ITEMS = Gauge(
            "bartholomew_working_memory_items",
            "Current number of items in working memory.",
            registry=REGISTRY,
        )

        WORKING_MEMORY_TOKENS = Gauge(
            "bartholomew_working_memory_tokens",
            "Current token usage in working memory.",
            registry=REGISTRY,
        )

        WORKING_MEMORY_EVICTIONS_TOTAL = Counter(
            "bartholomew_working_memory_evictions_total",
            "Total working memory evictions by overflow policy.",
            ["policy"],
            registry=REGISTRY,
        )

        # =====================================================================
        # Stage 3: Narrator metrics
        # =====================================================================
        EPISODES_TOTAL = Counter(
            "bartholomew_episodes_total",
            "Total episodic entries created.",
            ["episode_type", "tone"],
            registry=REGISTRY,
        )

        EPISODES_PERSISTED = Gauge(
            "bartholomew_episodes_persisted",
            "Total number of episodes in the database.",
            registry=REGISTRY,
        )

        # =====================================================================
        # Stage 3: Persona metrics
        # =====================================================================
        PERSONA_SWITCHES_TOTAL = Counter(
            "bartholomew_persona_switches_total",
            "Total persona pack switches.",
            ["from_pack", "to_pack", "trigger"],
            registry=REGISTRY,
        )

        PERSONA_ACTIVE = Gauge(
            "bartholomew_persona_active",
            "Currently active persona (1 for active, 0 otherwise).",
            ["pack_id"],
            registry=REGISTRY,
        )

    except ValueError:
        # Metrics already registered (module re-imported via different path)
        # This is expected in test environments; silently ignore
        pass

    _metrics_registered = True


# Initialize metrics on module load
_init_metrics_once()


def _update_stage3_metrics(request: Request) -> None:
    """
    Update Stage 3 metrics by pulling current values from kernel modules.

    Called on each /metrics scrape to ensure fresh values.
    """
    # Get kernel from app state
    kernel = getattr(request.app.state, "kernel", None)
    if kernel is None:
        return

    # =========================================================================
    # Experience Kernel metrics
    # =========================================================================
    experience = getattr(kernel, "experience", None)
    if experience is not None:
        # Affect state
        affect = getattr(experience, "_affect", None)
        if affect is not None:
            if AFFECT_VALENCE is not None:
                AFFECT_VALENCE.set(getattr(affect, "valence", 0))
            if AFFECT_AROUSAL is not None:
                AFFECT_AROUSAL.set(getattr(affect, "arousal", 0))
            if AFFECT_ENERGY is not None:
                AFFECT_ENERGY.set(getattr(affect, "energy", 0))

        # Attention state
        attention = getattr(experience, "_attention", None)
        if attention is not None and ATTENTION_INTENSITY is not None:
            ATTENTION_INTENSITY.set(getattr(attention, "intensity", 0))

        # Goals
        active_goals = getattr(experience, "_active_goals", set())
        if GOALS_ACTIVE is not None:
            GOALS_ACTIVE.set(len(active_goals))

        # Drives
        drives = getattr(experience, "_drives", {})
        if DRIVE_ACTIVATION is not None:
            for drive_id, drive in drives.items():
                activation = getattr(drive, "activation", 0)
                DRIVE_ACTIVATION.labels(drive_id=drive_id).set(activation)

    # =========================================================================
    # Global Workspace metrics
    # =========================================================================
    workspace = getattr(kernel, "workspace", None)
    if workspace is not None and WORKSPACE_SUBSCRIBERS is not None:
        subscriptions = getattr(workspace, "_subscriptions", {})
        for channel, subs in subscriptions.items():
            WORKSPACE_SUBSCRIBERS.labels(channel=channel).set(len(subs))

    # =========================================================================
    # Working Memory metrics
    # =========================================================================
    working_memory = getattr(kernel, "working_memory", None)
    if working_memory is not None:
        if WORKING_MEMORY_ITEMS is not None:
            items = getattr(working_memory, "_items", {})
            WORKING_MEMORY_ITEMS.set(len(items))

        if WORKING_MEMORY_TOKENS is not None:
            if hasattr(working_memory, "get_token_usage"):
                tokens = working_memory.get_token_usage()
            else:
                tokens = 0
            WORKING_MEMORY_TOKENS.set(tokens)

    # =========================================================================
    # Narrator metrics
    # =========================================================================
    narrator = getattr(kernel, "narrator", None)
    if narrator is not None and EPISODES_PERSISTED is not None:
        # Get episode count if method exists
        if hasattr(narrator, "get_episode_count"):
            try:
                count = narrator.get_episode_count()
                EPISODES_PERSISTED.set(count)
            except Exception:
                pass

    # =========================================================================
    # Persona metrics
    # =========================================================================
    persona_manager = getattr(kernel, "persona_manager", None)
    if persona_manager is not None and PERSONA_ACTIVE is not None:
        active_id = None
        if hasattr(persona_manager, "get_active_pack_id"):
            active_id = persona_manager.get_active_pack_id()

        all_packs = []
        if hasattr(persona_manager, "get_all_packs"):
            all_packs = persona_manager.get_all_packs()

        for pack in all_packs:
            pack_id = getattr(pack, "pack_id", None)
            if pack_id:
                is_active = 1 if pack_id == active_id else 0
                PERSONA_ACTIVE.labels(pack_id=pack_id).set(is_active)


@router.get("/metrics")
def metrics(request: Request) -> Response:
    """
    Prometheus text exposition format endpoint.

    Exposes kernel metrics including Stage 3 experience/persona state.
    Uses shared registry with duplicate collector protection.
    """
    # Ensure metrics are initialized (idempotent)
    _init_metrics_once()

    # Update uptime gauge right before scrape (cheap and fresh)
    if KERNEL_UPTIME_SECONDS is not None:
        start = getattr(request.app.state, "start_monotonic", None)
        if start is not None:
            KERNEL_UPTIME_SECONDS.set(max(0.0, time.monotonic() - start))

    # Update Stage 3 metrics from kernel state
    _update_stage3_metrics(request)

    payload = generate_latest(REGISTRY)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


# =============================================================================
# Event-driven metric updates (called by kernel modules)
# =============================================================================


def increment_workspace_event(channel: str, event_type: str) -> None:
    """
    Increment workspace events counter.

    Called by GlobalWorkspace.publish().
    """
    if WORKSPACE_EVENTS_TOTAL is not None:
        try:
            WORKSPACE_EVENTS_TOTAL.labels(
                channel=channel,
                event_type=event_type,
            ).inc()
        except Exception:
            pass


def increment_episode_created(episode_type: str, tone: str) -> None:
    """
    Increment episodes counter.

    Called by NarratorEngine.persist_episode().
    """
    if EPISODES_TOTAL is not None:
        try:
            EPISODES_TOTAL.labels(
                episode_type=episode_type,
                tone=tone,
            ).inc()
        except Exception:
            pass


def increment_persona_switch(
    from_pack: str,
    to_pack: str,
    trigger: str,
) -> None:
    """
    Increment persona switches counter.

    Called by PersonaPackManager.switch_pack().
    """
    if PERSONA_SWITCHES_TOTAL is not None:
        try:
            PERSONA_SWITCHES_TOTAL.labels(
                from_pack=from_pack,
                to_pack=to_pack,
                trigger=trigger,
            ).inc()
        except Exception:
            pass


def increment_snapshot_persisted() -> None:
    """
    Increment snapshots counter.

    Called by ExperienceKernel.persist_snapshot().
    """
    if SNAPSHOTS_PERSISTED_TOTAL is not None:
        try:
            SNAPSHOTS_PERSISTED_TOTAL.inc()
        except Exception:
            pass


def increment_working_memory_eviction(policy: str) -> None:
    """
    Increment evictions counter.

    Called by WorkingMemoryManager._evict_items().
    """
    if WORKING_MEMORY_EVICTIONS_TOTAL is not None:
        try:
            WORKING_MEMORY_EVICTIONS_TOTAL.labels(policy=policy).inc()
        except Exception:
            pass
