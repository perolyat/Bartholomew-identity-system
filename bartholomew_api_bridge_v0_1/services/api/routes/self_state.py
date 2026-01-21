"""
Stage 3.6 API Routes: Self-State, Episodes, and Persona Management
-------------------------------------------------------------------
Exposes the Experience Kernel, Narrator, and Persona Pack systems via REST API.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


router = APIRouter(prefix="/api", tags=["self-state"])


# =============================================================================
# Request/Response Models
# =============================================================================


class AffectUpdate(BaseModel):
    """Request model for updating affect state."""

    valence: float | None = None
    arousal: float | None = None
    energy: float | None = None


class AttentionUpdate(BaseModel):
    """Request model for setting attention."""

    target: str
    attention_type: str = "focused"
    intensity: float = 0.7
    context_tags: list[str] | None = None


class PersonaSwitchRequest(BaseModel):
    """Request model for switching persona."""

    pack_id: str
    trigger: str = "manual"
    context_tags: list[str] | None = None


# =============================================================================
# Helper to get kernel from app state
# =============================================================================


def _get_kernel():
    """Get the kernel daemon from app state."""
    # Import here to avoid circular imports
    from bartholomew_api_bridge_v0_1.services.api.app import _kernel

    if _kernel is None:
        raise HTTPException(503, "Kernel not initialized")
    return _kernel


# =============================================================================
# Self-State Endpoints
# =============================================================================


@router.get("/self")
async def get_self_snapshot() -> dict[str, Any]:
    """
    Get the current self-snapshot including affect, attention, drives, and goals.
    """
    kernel = _get_kernel()

    snapshot = kernel.experience.self_snapshot()
    return {
        "snapshot": snapshot.to_dict(),
        "active_persona": kernel.persona_manager.get_active_pack_id(),
        "working_memory_tokens": kernel.working_memory.get_token_usage(),
    }


@router.get("/self/affect")
async def get_affect() -> dict[str, Any]:
    """Get current affect state."""
    kernel = _get_kernel()
    affect = kernel.experience._affect
    return affect.to_dict()


@router.put("/self/affect")
async def update_affect(body: AffectUpdate) -> dict[str, Any]:
    """Update affect state."""
    kernel = _get_kernel()

    kernel.experience.update_affect(
        valence=body.valence,
        arousal=body.arousal,
        energy=body.energy,
    )

    return {
        "ok": True,
        "affect": kernel.experience._affect.to_dict(),
    }


@router.get("/self/attention")
async def get_attention() -> dict[str, Any]:
    """Get current attention state."""
    kernel = _get_kernel()
    attention = kernel.experience._attention
    return attention.to_dict()


@router.put("/self/attention")
async def set_attention(body: AttentionUpdate) -> dict[str, Any]:
    """Set attention focus."""
    kernel = _get_kernel()

    kernel.experience.set_attention(
        target=body.target,
        attention_type=body.attention_type,
        intensity=body.intensity,
        context_tags=body.context_tags or [],
    )

    return {
        "ok": True,
        "attention": kernel.experience._attention.to_dict(),
    }


@router.delete("/self/attention")
async def clear_attention() -> dict[str, Any]:
    """Clear attention (set to idle)."""
    kernel = _get_kernel()
    kernel.experience.clear_attention()
    return {
        "ok": True,
        "attention": kernel.experience._attention.to_dict(),
    }


@router.get("/self/drives")
async def get_drives() -> dict[str, Any]:
    """Get all drives with effective activation levels."""
    kernel = _get_kernel()
    drives = kernel.experience.get_top_drives(limit=100)
    return {
        "drives": [d.to_dict() for d in drives],
    }


@router.get("/self/drives/top")
async def get_top_drives(limit: int = 5) -> dict[str, Any]:
    """Get top N drives by effective activation."""
    kernel = _get_kernel()
    drives = kernel.experience.get_top_drives(limit=limit)
    return {
        "drives": [d.to_dict() for d in drives],
    }


@router.post("/self/drives/{drive_id}/activate")
async def activate_drive(drive_id: str, amount: float = 0.2) -> dict[str, Any]:
    """Activate a drive by increasing its activation level."""
    kernel = _get_kernel()

    try:
        kernel.experience.activate_drive(drive_id, amount=amount)
        drive = kernel.experience.get_drive(drive_id)
        return {
            "ok": True,
            "drive": drive.to_dict() if drive else None,
        }
    except ValueError as e:
        raise HTTPException(404, str(e)) from None


@router.post("/self/drives/{drive_id}/satisfy")
async def satisfy_drive(drive_id: str, amount: float = 0.3) -> dict[str, Any]:
    """Satisfy a drive by decreasing its activation level."""
    kernel = _get_kernel()

    try:
        kernel.experience.satisfy_drive(drive_id, amount=amount)
        drive = kernel.experience.get_drive(drive_id)
        return {
            "ok": True,
            "drive": drive.to_dict() if drive else None,
        }
    except ValueError as e:
        raise HTTPException(404, str(e)) from None


@router.get("/self/goals")
async def get_goals() -> dict[str, Any]:
    """Get active goals."""
    kernel = _get_kernel()
    goals = kernel.experience._active_goals
    return {"goals": list(goals)}


@router.post("/self/goals")
async def add_goal(goal: str) -> dict[str, Any]:
    """Add a new goal."""
    kernel = _get_kernel()
    added = kernel.experience.add_goal(goal)
    return {
        "ok": True,
        "added": added,
        "goals": list(kernel.experience._active_goals),
    }


@router.delete("/self/goals/{goal}")
async def complete_goal(goal: str) -> dict[str, Any]:
    """Mark a goal as completed."""
    kernel = _get_kernel()
    completed = kernel.experience.complete_goal(goal)
    return {
        "ok": True,
        "completed": completed,
        "goals": list(kernel.experience._active_goals),
    }


# =============================================================================
# Episode Endpoints
# =============================================================================


@router.get("/episodes/recent")
async def get_recent_episodes(limit: int = 20) -> dict[str, Any]:
    """Get recent episodic entries."""
    kernel = _get_kernel()
    episodes = kernel.narrator.get_recent_episodes(limit=limit)
    return {
        "episodes": [ep.to_dict() for ep in episodes],
        "count": len(episodes),
    }


@router.get("/episodes/{episode_id}")
async def get_episode(episode_id: str) -> dict[str, Any]:
    """Get a specific episode by ID."""
    kernel = _get_kernel()
    episode = kernel.narrator.get_episode(episode_id)
    if not episode:
        raise HTTPException(404, f"Episode {episode_id} not found")
    return {"episode": episode.to_dict()}


@router.get("/episodes/by-type/{episode_type}")
async def get_episodes_by_type(
    episode_type: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Get episodes filtered by type."""
    kernel = _get_kernel()
    episodes = kernel.narrator.get_episodes_by_type(episode_type, limit=limit)
    return {
        "episodes": [ep.to_dict() for ep in episodes],
        "count": len(episodes),
        "type": episode_type,
    }


@router.get("/episodes/by-tag/{tag}")
async def get_episodes_by_tag(tag: str, limit: int = 20) -> dict[str, Any]:
    """Get episodes filtered by tag."""
    kernel = _get_kernel()
    episodes = kernel.narrator.get_episodes_by_tag(tag, limit=limit)
    return {
        "episodes": [ep.to_dict() for ep in episodes],
        "count": len(episodes),
        "tag": tag,
    }


# =============================================================================
# Persona Endpoints
# =============================================================================


@router.get("/persona/current")
async def get_current_persona() -> dict[str, Any]:
    """Get the currently active persona pack."""
    kernel = _get_kernel()
    pack = kernel.persona_manager.get_active_pack()
    if not pack:
        return {"active": False, "pack": None}
    return {
        "active": True,
        "pack": pack.to_dict(),
    }


@router.get("/persona/list")
async def list_personas() -> dict[str, Any]:
    """List all available persona packs."""
    kernel = _get_kernel()
    packs = kernel.persona_manager.get_all_packs()
    active_id = kernel.persona_manager.get_active_pack_id()
    return {
        "packs": [
            {
                **pack.to_dict(),
                "is_active": pack.pack_id == active_id,
            }
            for pack in packs
        ],
        "active_pack_id": active_id,
    }


@router.post("/persona/switch")
async def switch_persona(body: PersonaSwitchRequest) -> dict[str, Any]:
    """Switch to a different persona pack."""
    kernel = _get_kernel()

    success = kernel.persona_manager.switch_pack(
        pack_id=body.pack_id,
        trigger=body.trigger,
        context_tags=body.context_tags,
    )

    if not success:
        raise HTTPException(404, f"Persona pack '{body.pack_id}' not found")

    pack = kernel.persona_manager.get_active_pack()
    return {
        "ok": True,
        "pack": pack.to_dict() if pack else None,
    }


@router.get("/persona/history")
async def get_persona_history(limit: int = 20) -> dict[str, Any]:
    """Get persona switch history."""
    kernel = _get_kernel()
    history = kernel.persona_manager.get_switch_history(limit=limit)
    return {
        "history": [record.to_dict() for record in history],
        "count": len(history),
        "total_switches": kernel.persona_manager.get_switch_count(),
    }


@router.get("/persona/{pack_id}")
async def get_persona(pack_id: str) -> dict[str, Any]:
    """Get a specific persona pack by ID."""
    kernel = _get_kernel()
    pack = kernel.persona_manager.get_pack(pack_id)
    if not pack:
        raise HTTPException(404, f"Persona pack '{pack_id}' not found")
    return {"pack": pack.to_dict()}


# =============================================================================
# Working Memory Endpoints
# =============================================================================


@router.get("/working_memory")
async def get_working_memory() -> dict[str, Any]:
    """Get current working memory contents."""
    kernel = _get_kernel()
    items = kernel.working_memory.get_all()
    return {
        "items": [item.to_dict() for item in items],
        "count": len(items),
        "token_usage": kernel.working_memory.get_token_usage(),
        "token_budget": kernel.working_memory._token_budget,
        "overflow_policy": kernel.working_memory._overflow_policy.value,
    }


@router.get("/working_memory/context")
async def get_working_memory_context() -> dict[str, Any]:
    """Get working memory rendered as context string."""
    kernel = _get_kernel()
    context = kernel.working_memory.get_context_string()
    return {
        "context": context,
        "token_usage": kernel.working_memory.get_token_usage(),
    }


@router.delete("/working_memory")
async def clear_working_memory() -> dict[str, Any]:
    """Clear all working memory items."""
    kernel = _get_kernel()
    kernel.working_memory.clear()
    return {"ok": True, "cleared": True}
