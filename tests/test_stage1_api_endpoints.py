"""
Tests for Stage 1 API Endpoints
--------------------------------
Tests the nudge and reflection API endpoints required for Stage 1 Console/UI integration.

Stage 1 Exit Criteria (from ROADMAP.md):
- API endpoints stable and documented
- Basic UI/console can safely perform: list/ack/dismiss nudges; fetch latest reflections
- No "Act" capability beyond these actions
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_kernel():
    """Create a mock kernel with memory store."""
    kernel = MagicMock()
    kernel.mem = MagicMock()
    kernel.state = MagicMock()
    kernel.state.now = datetime.now(timezone.utc)
    kernel.mem.db_path = ":memory:"
    return kernel


@pytest.fixture
def mock_nudges():
    """Sample nudge data."""
    return [
        {
            "id": 1,
            "kind": "hydration",
            "message": "Time for some water!",
            "ts": "2026-01-20T10:00:00Z",
            "status": "pending",
        },
        {
            "id": 2,
            "kind": "posture",
            "message": "Check your posture",
            "ts": "2026-01-20T11:00:00Z",
            "status": "pending",
        },
    ]


@pytest.fixture
def mock_daily_reflection():
    """Sample daily reflection data."""
    return {
        "id": 1,
        "kind": "daily_journal",
        "ts": "2026-01-20T21:30:00Z",
        "content": "# Daily Reflection\n\nHydration: 2000ml\nNudges sent: 5\nNudges acked: 3",
        "pinned": False,
    }


@pytest.fixture
def mock_weekly_reflection():
    """Sample weekly reflection data."""
    return {
        "id": 2,
        "kind": "weekly_alignment_audit",
        "ts": "2026-01-19T21:30:00Z",
        "content": "# Weekly Alignment Audit\n\n- [x] No deception\n- [x] No manipulation\n- [x] No harm",
        "pinned": True,
    }


class TestNudgeEndpoints:
    """Tests for /api/nudges/* endpoints."""

    @pytest.mark.asyncio
    async def test_get_pending_nudges_returns_list(self, mock_kernel, mock_nudges):
        """Test GET /api/nudges/pending returns list of pending nudges."""
        mock_kernel.mem.list_pending_nudges = AsyncMock(return_value=mock_nudges)

        # Import after mocking to avoid kernel initialization
        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import get_pending_nudges

            # Patch the global _kernel
            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", mock_kernel):
                result = await get_pending_nudges(limit=50)

        assert "nudges" in result
        assert len(result["nudges"]) == 2
        assert result["nudges"][0]["kind"] == "hydration"
        assert result["nudges"][1]["kind"] == "posture"

    @pytest.mark.asyncio
    async def test_get_pending_nudges_respects_limit(self, mock_kernel, mock_nudges):
        """Test GET /api/nudges/pending respects limit parameter."""
        mock_kernel.mem.list_pending_nudges = AsyncMock(return_value=mock_nudges[:1])

        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import get_pending_nudges

            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", mock_kernel):
                await get_pending_nudges(limit=1)

        mock_kernel.mem.list_pending_nudges.assert_called_once_with(limit=1)

    @pytest.mark.asyncio
    async def test_get_pending_nudges_empty(self, mock_kernel):
        """Test GET /api/nudges/pending returns empty list when no nudges."""
        mock_kernel.mem.list_pending_nudges = AsyncMock(return_value=[])

        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import get_pending_nudges

            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", mock_kernel):
                result = await get_pending_nudges()

        assert result["nudges"] == []

    @pytest.mark.asyncio
    async def test_ack_nudge_updates_status(self, mock_kernel):
        """Test POST /api/nudges/{id}/ack updates nudge status to acked."""
        mock_kernel.mem.set_nudge_status = AsyncMock()

        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import ack_nudge

            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", mock_kernel):
                result = await ack_nudge(nudge_id=1)

        assert result["ok"] is True
        assert result["nudge_id"] == 1
        assert result["status"] == "acked"
        mock_kernel.mem.set_nudge_status.assert_called_once()
        call_args = mock_kernel.mem.set_nudge_status.call_args
        assert call_args[0][0] == 1
        assert call_args[0][1] == "acked"

    @pytest.mark.asyncio
    async def test_dismiss_nudge_updates_status(self, mock_kernel):
        """Test POST /api/nudges/{id}/dismiss updates nudge status to dismissed."""
        mock_kernel.mem.set_nudge_status = AsyncMock()

        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import dismiss_nudge

            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", mock_kernel):
                result = await dismiss_nudge(nudge_id=2)

        assert result["ok"] is True
        assert result["nudge_id"] == 2
        assert result["status"] == "dismissed"
        mock_kernel.mem.set_nudge_status.assert_called_once()
        call_args = mock_kernel.mem.set_nudge_status.call_args
        assert call_args[0][0] == 2
        assert call_args[0][1] == "dismissed"


class TestReflectionEndpoints:
    """Tests for /api/reflection/* endpoints."""

    @pytest.mark.asyncio
    async def test_get_daily_reflection_returns_content(self, mock_kernel, mock_daily_reflection):
        """Test GET /api/reflection/daily/latest returns reflection content."""
        mock_kernel.mem.latest_reflection = AsyncMock(return_value=mock_daily_reflection)

        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import (
                get_latest_daily_reflection,
            )

            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", mock_kernel):
                result = await get_latest_daily_reflection()

        assert "reflection" in result
        assert result["reflection"]["kind"] == "daily_journal"
        assert "Daily Reflection" in result["reflection"]["content"]

    @pytest.mark.asyncio
    async def test_get_weekly_reflection_returns_content(self, mock_kernel, mock_weekly_reflection):
        """Test GET /api/reflection/weekly/latest returns reflection content."""
        mock_kernel.mem.latest_reflection = AsyncMock(return_value=mock_weekly_reflection)

        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import (
                get_latest_weekly_reflection,
            )

            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", mock_kernel):
                result = await get_latest_weekly_reflection()

        assert "reflection" in result
        assert result["reflection"]["kind"] == "weekly_alignment_audit"
        assert "No deception" in result["reflection"]["content"]

    @pytest.mark.asyncio
    async def test_get_daily_reflection_404_when_none(self, mock_kernel):
        """Test GET /api/reflection/daily/latest returns 404 when no reflection exists."""
        from fastapi import HTTPException

        mock_kernel.mem.latest_reflection = AsyncMock(return_value=None)

        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import (
                get_latest_daily_reflection,
            )

            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", mock_kernel):
                with pytest.raises(HTTPException) as exc_info:
                    await get_latest_daily_reflection()

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_trigger_daily_reflection(self, mock_kernel):
        """Test POST /api/reflection/run?kind=daily triggers daily reflection."""
        mock_kernel.handle_command = AsyncMock()

        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import trigger_reflection

            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", mock_kernel):
                result = await trigger_reflection(kind="daily")

        assert result["ok"] is True
        assert result["kind"] == "daily"
        assert result["triggered"] is True
        mock_kernel.handle_command.assert_called_once_with("reflection_run_daily")

    @pytest.mark.asyncio
    async def test_trigger_weekly_reflection(self, mock_kernel):
        """Test POST /api/reflection/run?kind=weekly triggers weekly reflection."""
        mock_kernel.handle_command = AsyncMock()

        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import trigger_reflection

            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", mock_kernel):
                result = await trigger_reflection(kind="weekly")

        assert result["ok"] is True
        assert result["kind"] == "weekly"
        mock_kernel.handle_command.assert_called_once_with("reflection_run_weekly")

    @pytest.mark.asyncio
    async def test_trigger_reflection_invalid_kind(self, mock_kernel):
        """Test POST /api/reflection/run with invalid kind returns 400."""
        from fastapi import HTTPException

        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import trigger_reflection

            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", mock_kernel):
                with pytest.raises(HTTPException) as exc_info:
                    await trigger_reflection(kind="invalid")

        assert exc_info.value.status_code == 400


class TestHealthEndpoint:
    """Tests for /api/health endpoint Stage 1 requirements."""

    @pytest.mark.asyncio
    async def test_health_includes_kernel_status(self, mock_kernel, mock_nudges):
        """Test /api/health includes kernel_online status."""
        mock_kernel.mem.list_pending_nudges = AsyncMock(return_value=mock_nudges)
        mock_kernel.mem.latest_reflection = AsyncMock(return_value=None)

        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import health

            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", mock_kernel):
                result = await health()

        assert result["kernel_online"] is True
        assert "nudges_pending_count" in result
        assert result["nudges_pending_count"] == 2

    @pytest.mark.asyncio
    async def test_health_kernel_offline(self):
        """Test /api/health reports kernel_online=False when kernel not initialized."""
        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import health

            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", None):
                result = await health()

        assert result["kernel_online"] is False


class TestKernelCommandEndpoint:
    """Tests for /kernel/command/{cmd} endpoint."""

    @pytest.mark.asyncio
    async def test_kernel_command_reflection_daily(self, mock_kernel):
        """Test /kernel/command/reflection_run_daily executes command."""
        mock_kernel.handle_command = AsyncMock()

        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import kernel_command

            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", mock_kernel):
                result = await kernel_command("reflection_run_daily")

        assert result["ok"] is True
        mock_kernel.handle_command.assert_called_once_with("reflection_run_daily")

    @pytest.mark.asyncio
    async def test_kernel_command_503_when_no_kernel(self):
        """Test /kernel/command returns 503 when kernel not initialized."""
        from fastapi import HTTPException

        with patch.dict("sys.modules", {"bartholomew.kernel.daemon": MagicMock()}):
            from bartholomew_api_bridge_v0_1.services.api.app import kernel_command

            with patch("bartholomew_api_bridge_v0_1.services.api.app._kernel", None):
                with pytest.raises(HTTPException) as exc_info:
                    await kernel_command("test")

        assert exc_info.value.status_code == 503
