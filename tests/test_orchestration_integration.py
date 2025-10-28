"""
Tests for Orchestration Integration
------------------------------------
Tests the orchestrator pipeline, routing, formatting, and logging.
"""
import pytest
import json
from pathlib import Path
from identity_interpreter.orchestrator import (
    Orchestrator,
    ModelRouter,
    ResponseFormatter,
    ContextBuilder,
    StateManager
)


@pytest.fixture
def orchestrator():
    """Create a test orchestrator instance."""
    return Orchestrator(log_dir="logs/orchestrator")


@pytest.fixture
def router():
    """Create a test model router."""
    return ModelRouter()


@pytest.fixture
def formatter():
    """Create a test response formatter."""
    return ResponseFormatter()


def test_context_injection(orchestrator):
    """Test that context injection creates a prompt."""
    result = orchestrator.handle_input("Hello Bartholomew")
    assert result is not None
    assert "stub-llm" in result or "Mock" in result


def test_session_id_persistence(orchestrator):
    """Test that session ID persists across requests."""
    session1 = orchestrator.state.session_id
    orchestrator.handle_input("Ping")
    assert session1 == orchestrator.state.session_id


def test_router_selection():
    """Test model router selection logic."""
    router = ModelRouter()
    
    # Default routing
    data = {}
    route = router.select_route(data)
    assert route["backend"] == "stub"
    assert route["model"] == "stub-llm"
    assert "temperature" in route["parameters"]
    
    # Specific backend
    data = {"backend": "openai"}
    route = router.select_route(data)
    assert route["backend"] == "openai"
    assert route["model"] == "gpt-4o-mini"


def test_router_execution():
    """Test router executes and returns response."""
    router = ModelRouter()
    data = {"user_input": "test query", "prompt": "test query"}
    response = router.route(data)
    assert response is not None
    assert isinstance(response, str)


def test_formatter_tags_mode():
    """Test formatter applies tone and emotion tags."""
    formatter = ResponseFormatter(mode="tags")
    
    # With tone only
    result = formatter.format("Hello", tone="empathetic")
    assert "[tone: empathetic]" in result
    assert "Hello" in result
    
    # With emotion only
    result = formatter.format("Hello", emotion="warm")
    assert "[emotion: warm]" in result
    
    # With both
    result = formatter.format("Hello", tone="neutral", emotion="serious")
    assert "[tone: neutral]" in result
    assert "[emotion: serious]" in result
    
    # With neither
    result = formatter.format("Hello")
    assert result == "Hello"


def test_formatter_structured_mode():
    """Test formatter returns structured dict."""
    formatter = ResponseFormatter(mode="structured")
    
    result = formatter.format(
        "Hello",
        tone="authoritative",
        emotion="enthusiastic"
    )
    
    assert isinstance(result, dict)
    assert result["text"] == "Hello"
    assert result["tone"] == "authoritative"
    assert result["emotion"] == "enthusiastic"
    assert "metadata" in result


def test_formatter_invalid_values():
    """Test formatter handles invalid tone/emotion values."""
    formatter = ResponseFormatter(mode="structured")
    
    result = formatter.format(
        "Hello",
        tone="invalid_tone",
        emotion="invalid_emotion"
    )
    
    assert result["tone"] == "neutral"
    assert result["emotion"] == "neutral"


def test_orchestrator_with_tone_emotion(orchestrator):
    """Test orchestrator respects tone and emotion state."""
    orchestrator.state.set("tone", "empathetic")
    orchestrator.state.set("emotion", "warm")
    
    result = orchestrator.handle_input("How are you?")
    
    # Should have tags in response
    assert "[tone: empathetic]" in result
    assert "[emotion: warm]" in result


def test_logging_creates_file(orchestrator):
    """Test that logs are written to file."""
    log_file = orchestrator.log_file
    
    # Clear log if exists
    if log_file.exists():
        log_file.unlink()
    
    # Process request
    orchestrator.handle_input("Test input")
    
    # Verify log file created
    assert log_file.exists()
    
    # Verify JSONL format
    with open(log_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
        assert len(lines) > 0
        
        # Each line should be valid JSON
        for line in lines:
            entry = json.loads(line)
            assert "ts" in entry
            assert "session_id" in entry
            assert "step" in entry
            assert "event" in entry


def test_logging_tracks_steps(orchestrator):
    """Test that logging tracks pipeline steps."""
    log_file = orchestrator.log_file
    
    # Clear log if exists
    if log_file.exists():
        log_file.unlink()
    
    # Process request
    orchestrator.handle_input("Test input")
    
    # Read logs
    with open(log_file, "r", encoding="utf-8") as f:
        logs = [json.loads(line) for line in f.readlines()]
    
    steps = [log["step"] for log in logs]
    
    # Should see orchestrator start/end and pipeline steps
    assert "orchestrator" in steps
    assert "inject_memory_context" in steps or any(
        "inject" in s for s in steps
    )


def test_context_builder():
    """Test context builder creates prompt context."""
    builder = ContextBuilder()
    session_id = "test-session"
    
    # Should not crash even with no memories
    context = builder.build_prompt_context(session_id, limit=5)
    assert isinstance(context, str)
    
    # Should inject into input
    injected = builder.inject_context("Hello", session_id)
    assert "Hello" in injected
    assert "User:" in injected


def test_state_manager():
    """Test state manager maintains session state."""
    state = StateManager()
    
    # Has session ID
    assert state.session_id is not None
    
    # Set and get
    state.set("key", "value")
    assert state.get("key") == "value"
    
    # Get with default
    assert state.get("missing", "default") == "default"
    
    # Export
    exported = state.export()
    assert "session_id" in exported
    assert "state" in exported
    assert exported["state"]["key"] == "value"
    
    # Clear
    state.clear()
    assert state.get("key") is None


def test_health_check_runs():
    """Test that health check executes without error."""
    from identity_interpreter.orchestrator.system_health import health_check
    
    # Should not raise exception
    health_check()


def test_log_directory_creation():
    """Test that log directory is created if missing."""
    import shutil
    
    log_dir = Path("logs/orchestrator_test")
    
    # Remove if exists
    if log_dir.exists():
        shutil.rmtree(log_dir)
    
    # Create orchestrator with custom log dir
    Orchestrator(log_dir=str(log_dir))
    
    # Directory should be created
    assert log_dir.exists()
    assert log_dir.is_dir()
    
    # Cleanup
    if log_dir.exists():
        shutil.rmtree(log_dir)


def test_pipeline_step_order():
    """Test that pipeline steps execute in order."""
    from identity_interpreter.orchestrator.pipeline import Pipeline
    
    pipeline = Pipeline()
    order = []
    
    def step1(data):
        order.append(1)
        return data
    
    def step2(data):
        order.append(2)
        return data
    
    def step3(data):
        order.append(3)
        return data
    
    pipeline.add_step(step1)
    pipeline.add_step(step2)
    pipeline.add_step(step3)
    
    pipeline.execute({})
    
    assert order == [1, 2, 3]
