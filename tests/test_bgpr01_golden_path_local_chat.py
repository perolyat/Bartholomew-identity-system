"""
BGPR-01 -- Golden Path Runtime Repair: the regression guard.

A clean Windows acceptance run of Wave 3 submitted one message --
"Hello Bartholomew. Please reply with only: READY" -- and got
`POST /api/chat 503` while the UI's presence line read "unknown -- cannot
tell whether the model will answer". Ollama was installed, the model was
pulled, and a direct call to Ollama generated "READY". Traced here against an
in-process fake Ollama on a fresh database, the wiring itself was correct
(the same request produced READY through the real route). What broke the
path was how the adapter and the health surface treated an Ollama that was
slow, busy or addressed in a form the adapter did not expect:

* a model-listing call slower than its bound was reported as "model not
  found, run ollama pull" -- and chat was gated on that listing;
* the readiness probe held Ollama to a 2 s clock of its own while the
  adapter allowed 5 s, so a working backend read as "unknown";
* the chat generation, and the nightly reflection's generation, ran on the
  event loop, freezing /api/health and the UI for the whole call;
* a scheme-less OLLAMA_HOST (Ollama's own documented form) also read as
  "model not found".

Every test here runs without a live Ollama. The fake speaks Ollama's HTTP
API closely enough to exercise the adapter's real request path; it is used
only to test wiring, timing and failure semantics, never to stand in for a
genuine generation in any live verification.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import socket
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

# Isolated database for the live-route tests, set before the app module is
# imported -- the same pattern as tests/test_api_chat_runtime_contract.py.
_db_dir = pathlib.Path(tempfile.mkdtemp()) / "data"
_db_dir.mkdir(parents=True, exist_ok=True)
_DB_PATH = str(_db_dir / "bgpr01.db")
os.environ["BARTH_DB_PATH"] = _DB_PATH

from identity_interpreter.adapters.llm_stub import (  # noqa: E402
    GENERATE_TIMEOUT_ENV,
    LIST_TIMEOUT_ENV,
    LLMAdapter,
    resolve_ollama_base_url,
)
from identity_interpreter.loader import load_identity  # noqa: E402
from identity_interpreter.orchestrator.model_router import (  # noqa: E402
    ModelBackendError,
    ModelRouter,
)
from identity_interpreter.orchestrator.orchestrator import Orchestrator  # noqa: E402

GOLDEN_PATH_MESSAGE = "Hello Bartholomew. Please reply with only: READY"
IDENTITY_MODEL = "Mistral-7B-Instruct-GGUF-Q4_K_M"
OLLAMA_MODEL = "mistral:7b-instruct"
GENERATION_PATHS = ("/api/generate", "/api/chat")
_MOCK_MARKER = "mock response"


class FakeOllama:
    """An in-process stand-in for Ollama's HTTP API.

    Serves GET /api/tags, POST /api/show, POST /api/generate and POST
    /api/chat with the response shapes the real server uses, on an ephemeral
    loopback port. Per-endpoint delays and the installed-model list are
    settable per test, and every request is recorded so a test can assert
    what the adapter actually asked for.
    """

    def __init__(self):
        self.models: list[str] = [OLLAMA_MODEL]
        self.tags_delay = 0.0
        self.generate_delay = 0.0
        self.reply = "READY"
        self.requests: list[tuple[str, str, str | None]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> FakeOllama:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def host(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def generation_requests(self) -> list[tuple[str, str, str | None]]:
        return [r for r in self.requests if r[1] in GENERATION_PATHS]

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _send(self, code: int, obj) -> None:
                body = json.dumps(obj).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    # The client gave up (a timeout under test). Fine.
                    pass

            def do_GET(self):
                if self.path == "/api/tags":
                    time.sleep(fake.tags_delay)
                    fake.requests.append(("GET", "/api/tags", None))
                    self._send(200, {"models": [{"name": m, "model": m} for m in fake.models]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                req = json.loads(self.rfile.read(length) or b"{}")
                model = req.get("model")
                fake.requests.append(("POST", self.path, model))
                if self.path == "/api/show":
                    if model in fake.models:
                        self._send(200, {"modelfile": "", "details": {}, "model_info": {}})
                    else:
                        self._send(404, {"error": f"model '{model}' not found"})
                elif self.path in GENERATION_PATHS:
                    if model not in fake.models:
                        self._send(
                            404,
                            {"error": f"model '{model}' not found, try pulling it first"},
                        )
                        return
                    time.sleep(fake.generate_delay)
                    if self.path == "/api/generate":
                        self._send(
                            200,
                            {
                                "model": model,
                                "created_at": "2026-09-10T00:00:00Z",
                                "response": fake.reply,
                                "done": True,
                                "eval_count": 3,
                            },
                        )
                    else:
                        self._send(
                            200,
                            {
                                "model": model,
                                "created_at": "2026-09-10T00:00:00Z",
                                "message": {"role": "assistant", "content": fake.reply},
                                "done": True,
                                "eval_count": 3,
                            },
                        )
                else:
                    self._send(404, {"error": "not found"})

        return Handler


def _closed_port() -> int:
    """A loopback port nothing is listening on."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def identity():
    return load_identity("Identity.yaml")


@pytest.fixture
def fake_ollama(monkeypatch):
    fake = FakeOllama().start()
    monkeypatch.setenv("OLLAMA_HOST", fake.host)
    monkeypatch.delenv(GENERATE_TIMEOUT_ENV, raising=False)
    monkeypatch.delenv(LIST_TIMEOUT_ENV, raising=False)
    try:
        yield fake
    finally:
        fake.stop()


def _identity_router(identity) -> ModelRouter:
    """The router the live API builds: Identity-wired, no explicit config."""
    return ModelRouter(identity_config=identity)


# ---------------------------------------------------------------------------
# 1-3. Wiring: adapter, Identity selection, model-name mapping
# ---------------------------------------------------------------------------


class TestIdentityWiring:
    def test_identity_configured_router_gets_a_usable_real_local_adapter(
        self,
        identity,
        fake_ollama,
    ):
        router = _identity_router(identity)
        assert isinstance(router.llm_adapter, LLMAdapter)
        assert router.config["default_backend"] == "local"
        assert router.llm_adapter.ollama_base_url == fake_ollama.host

    def test_identity_model_selection_resolves_the_local_primary(self, identity, fake_ollama):
        router = _identity_router(identity)
        route = router.select_route({"prompt": GOLDEN_PATH_MESSAGE})
        assert route["backend"] == "local"
        assert route["model"] == IDENTITY_MODEL
        assert route["model"] == identity.meta.deployment_profile.models.local_primary

    def test_identity_model_maps_to_the_installed_ollama_name(self, identity):
        assert LLMAdapter(identity)._map_model_name(IDENTITY_MODEL) == OLLAMA_MODEL


# ---------------------------------------------------------------------------
# 4. A healthy Ollama with the model installed produces genuine generation
# ---------------------------------------------------------------------------


class TestGenuineGeneration:
    def test_healthy_ollama_generates_through_the_identity_router(self, identity, fake_ollama):
        router = _identity_router(identity)

        out = router.route({"prompt": GOLDEN_PATH_MESSAGE})

        assert out == "READY"
        generated = fake_ollama.generation_requests()
        assert generated, "no generation request reached Ollama"
        assert all(model == OLLAMA_MODEL for _, _, model in generated)

    def test_generation_is_not_gated_on_a_separate_listing_call(self, identity, fake_ollama):
        """The listing call could fail on its own and was then reported as a
        missing model. Generation now stands on its own outcome: a listing
        far slower than its bound must not delay or fail a generation."""
        fake_ollama.tags_delay = 30.0
        router = _identity_router(identity)

        started = time.monotonic()
        out = router.route({"prompt": GOLDEN_PATH_MESSAGE})

        assert out == "READY"
        assert time.monotonic() - started < 5.0
        assert not [r for r in fake_ollama.requests if r[1] == "/api/tags"]

    def test_the_orchestrator_pipeline_returns_the_model_text(self, identity, fake_ollama):
        orch = Orchestrator(model_identity_config=identity)
        raw = orch.handle_input(GOLDEN_PATH_MESSAGE, skip_governance_check=True)
        assert "READY" in raw
        assert _MOCK_MARKER not in raw.lower()


# ---------------------------------------------------------------------------
# 5-6. Failures stay truthful, name the real reason, and never fabricate
# ---------------------------------------------------------------------------


class TestTruthfulFailure:
    def test_ollama_down_is_connection_failed_not_a_missing_model(self, identity, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{_closed_port()}")
        router = _identity_router(identity)

        with pytest.raises(ModelBackendError) as exc:
            router.route({"prompt": GOLDEN_PATH_MESSAGE})

        assert exc.value.reason == "connection_failed"
        assert "ollama pull" not in str(exc.value)
        assert _MOCK_MARKER not in str(exc.value).lower()

    def test_missing_model_is_model_not_available_with_the_pull_hint(
        self,
        identity,
        fake_ollama,
    ):
        fake_ollama.models = ["tinyllama:latest"]
        router = _identity_router(identity)

        with pytest.raises(ModelBackendError) as exc:
            router.route({"prompt": GOLDEN_PATH_MESSAGE})

        assert exc.value.reason == "model_not_available"
        assert f"ollama pull {OLLAMA_MODEL}" in str(exc.value)
        assert _MOCK_MARKER not in str(exc.value).lower()

    def test_generation_past_the_bound_is_a_timeout_that_names_the_knob(
        self,
        identity,
        fake_ollama,
        monkeypatch,
    ):
        monkeypatch.setenv(GENERATE_TIMEOUT_ENV, "0.5")
        fake_ollama.generate_delay = 3.0
        router = _identity_router(identity)

        with pytest.raises(ModelBackendError) as exc:
            router.route({"prompt": GOLDEN_PATH_MESSAGE})

        assert exc.value.reason == "timeout"
        assert GENERATE_TIMEOUT_ENV in str(exc.value)
        assert _MOCK_MARKER not in str(exc.value).lower()

    def test_the_generation_bound_is_configurable_and_defaults_to_a_cold_load(
        self,
        identity,
        fake_ollama,
        monkeypatch,
    ):
        assert LLMAdapter(identity).generate_timeout_seconds >= 120.0
        monkeypatch.setenv(GENERATE_TIMEOUT_ENV, "300")
        assert LLMAdapter(identity).generate_timeout_seconds == 300.0
        monkeypatch.setenv(GENERATE_TIMEOUT_ENV, "not a number")
        assert LLMAdapter(identity).generate_timeout_seconds >= 120.0


# ---------------------------------------------------------------------------
# Host resolution: every form Ollama documents for OLLAMA_HOST reaches Ollama
# ---------------------------------------------------------------------------


class TestHostResolution:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", "http://127.0.0.1:11434"),
            ("127.0.0.1:11434", "http://127.0.0.1:11434"),
            ("localhost", "http://localhost:11434"),
            (":11434", "http://127.0.0.1:11434"),
            ("0.0.0.0", "http://127.0.0.1:11434"),
            ("0.0.0.0:11434", "http://127.0.0.1:11434"),
            ("http://localhost:11434", "http://localhost:11434"),
            ("http://localhost:11434/", "http://localhost:11434"),
            ("https://ollama.example.com", "https://ollama.example.com:443"),
            ("[::1]:11434", "http://[::1]:11434"),
        ],
    )
    def test_resolves_the_forms_ollama_documents(self, raw, expected):
        assert resolve_ollama_base_url(raw) == expected

    def test_a_scheme_less_host_reaches_ollama(self, identity, fake_ollama, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", f"127.0.0.1:{fake_ollama.port}")
        router = _identity_router(identity)
        assert router.route({"prompt": GOLDEN_PATH_MESSAGE}) == "READY"

    def test_a_bind_all_host_reaches_the_local_ollama(self, identity, fake_ollama, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", f"0.0.0.0:{fake_ollama.port}")
        router = _identity_router(identity)
        assert router.route({"prompt": GOLDEN_PATH_MESSAGE}) == "READY"


# ---------------------------------------------------------------------------
# 8. Readiness agrees with what generation will do
# ---------------------------------------------------------------------------


class TestReadinessAgreesWithGeneration:
    def test_probe_reports_ready_for_a_healthy_ollama(self, identity, fake_ollama):
        report = LLMAdapter(identity).probe(IDENTITY_MODEL)
        assert report["reachable"] is True
        assert report["reason"] is None
        assert report["ollama_model"] == OLLAMA_MODEL

    def test_probe_reports_a_missing_model_as_such(self, identity, fake_ollama):
        fake_ollama.models = []
        report = LLMAdapter(identity).probe(IDENTITY_MODEL)
        assert report["reachable"] is False
        assert report["reason"] == "model_not_available"

    def test_probe_reports_a_down_ollama_as_unreachable(self, identity, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{_closed_port()}")
        report = LLMAdapter(identity).probe(IDENTITY_MODEL)
        assert report["reachable"] is False
        assert report["reason"] == "connection_failed"

    def test_probe_reports_unknown_only_when_ollama_did_not_answer_in_time(
        self,
        identity,
        fake_ollama,
        monkeypatch,
    ):
        monkeypatch.setenv(LIST_TIMEOUT_ENV, "0.5")
        fake_ollama.tags_delay = 3.0
        report = LLMAdapter(identity).probe(IDENTITY_MODEL)
        assert report["reachable"] is None
        assert report["reason"] == "timeout"

    def test_health_probe_bound_is_never_stricter_than_the_adapters(self, identity, fake_ollama):
        from bartholomew_api_bridge_v0_1.services.api import app as app_module

        adapter = LLMAdapter(identity)
        assert app_module._model_probe_timeout_seconds(adapter) >= adapter.list_timeout_seconds


# ---------------------------------------------------------------------------
# 7. The live /api/chat route, end to end, against the fake backend
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from bartholomew_api_bridge_v0_1.services.api import app as app_module

    os.environ["BARTH_DB_PATH"] = _DB_PATH
    with TestClient(app_module.app) as c:
        yield c


def _point_live_app_at(identity, host: str) -> None:
    """Rebuild the live app's orchestrator against `host`, exactly as
    startup() builds it, and forget any cached readiness reading."""
    from bartholomew_api_bridge_v0_1.services.api import app as app_module

    os.environ["OLLAMA_HOST"] = host
    app_module.orch = Orchestrator(model_identity_config=identity)
    app_module._model_probe_cache = (0.0, None, None)


class TestLiveChatRoute:
    def test_chat_returns_the_genuine_local_model_reply(self, client, fake_ollama, identity):
        _point_live_app_at(identity, fake_ollama.host)

        response = client.post("/api/chat", json={"message": GOLDEN_PATH_MESSAGE})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reply"] == "READY"
        assert _MOCK_MARKER not in body["reply"].lower()
        assert fake_ollama.generation_requests(), "the reply did not come from the backend"

        health = client.get("/api/health").json()
        assert health["model_backend"] == "local"
        assert health["model_name"] == IDENTITY_MODEL
        assert health["model_status"] == "ready"
        assert health["model_reachable"] is True
        assert health["model_reachability_reason"] is None
        assert health["model_host"] == fake_ollama.host

    def test_chat_503_is_truthful_and_logged_when_ollama_is_down(
        self,
        client,
        identity,
        monkeypatch,
        caplog,
    ):
        _point_live_app_at(identity, f"http://127.0.0.1:{_closed_port()}")

        with caplog.at_level(logging.WARNING):
            response = client.post("/api/chat", json={"message": GOLDEN_PATH_MESSAGE})

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "connection_failed" in detail
        assert OLLAMA_MODEL in detail
        assert _MOCK_MARKER not in detail.lower()
        # The reason is in the runtime log too, not only in the body the
        # access line never shows.
        logged = [r.getMessage() for r in caplog.records if "/api/chat 503" in r.getMessage()]
        assert logged and "reason=connection_failed" in logged[-1]

        health = client.get("/api/health").json()
        assert health["model_status"] == "selected_but_unreachable"
        assert health["model_reachability_reason"] == "connection_failed"

    def test_chat_503_names_a_missing_model_truthfully(self, client, fake_ollama, identity):
        fake_ollama.models = []
        _point_live_app_at(identity, fake_ollama.host)

        response = client.post("/api/chat", json={"message": GOLDEN_PATH_MESSAGE})

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "model_not_available" in detail
        assert f"ollama pull {OLLAMA_MODEL}" in detail

        health = client.get("/api/health").json()
        assert health["model_status"] == "selected_but_unreachable"
        assert health["model_reachability_reason"] == "model_not_available"

    def test_readiness_is_unknown_only_for_an_unanswered_probe_and_chat_still_works(
        self,
        client,
        fake_ollama,
        identity,
        monkeypatch,
    ):
        """A listing slower than its bound is the absence of a reading, not a
        reading of failure -- and it must not stop the generation."""
        monkeypatch.setenv(LIST_TIMEOUT_ENV, "0.5")
        fake_ollama.tags_delay = 3.0
        _point_live_app_at(identity, fake_ollama.host)

        health = client.get("/api/health").json()
        assert health["model_status"] == "selected_reachability_unknown"
        assert health["model_reachability_reason"] == "timeout"

        response = client.post("/api/chat", json={"message": GOLDEN_PATH_MESSAGE})
        assert response.status_code == 200, response.text
        assert response.json()["reply"] == "READY"

    def test_health_answers_while_a_chat_generation_is_in_flight(
        self,
        client,
        fake_ollama,
        identity,
    ):
        """The generation used to run on the event loop, so every other
        request -- the UI's readiness poll included -- waited for it."""
        fake_ollama.generate_delay = 3.0
        _point_live_app_at(identity, fake_ollama.host)
        outcome: dict = {}

        def _chat():
            outcome["response"] = client.post("/api/chat", json={"message": GOLDEN_PATH_MESSAGE})

        worker = threading.Thread(target=_chat)
        worker.start()
        time.sleep(0.5)
        started = time.monotonic()
        health = client.get("/api/health")
        elapsed = time.monotonic() - started
        worker.join(timeout=30)

        assert health.status_code == 200
        assert elapsed < 2.0, f"/api/health took {elapsed:.2f}s during a generation"
        assert outcome["response"].status_code == 200, outcome["response"].text
        assert outcome["response"].json()["reply"] == "READY"

    def test_governance_still_gates_chat_ahead_of_the_real_backend(
        self,
        client,
        fake_ollama,
        identity,
    ):
        """Restoring the real model path must not have moved the Parking
        Brake: an engaged brake refuses the turn before Ollama is asked."""
        from bartholomew_api_bridge_v0_1.services.api import app as app_module

        _point_live_app_at(identity, fake_ollama.host)
        store = app_module._kernel.governance_store
        store.engage("skills")
        try:
            response = client.post("/api/chat", json={"message": GOLDEN_PATH_MESSAGE})
            assert response.status_code == 503
            assert "parking brake" in response.json()["detail"].lower()
            assert not fake_ollama.generation_requests(), "Ollama was asked despite the brake"
        finally:
            store.disengage()

        response = client.post("/api/chat", json={"message": GOLDEN_PATH_MESSAGE})
        assert response.status_code == 200, response.text
        assert response.json()["reply"] == "READY"


# ---------------------------------------------------------------------------
# The nightly reflection's model call must not freeze the runtime either
# ---------------------------------------------------------------------------


class TestReflectionCompositionIsOffLoop:
    @pytest.mark.asyncio
    async def test_daily_reflection_composition_does_not_block_the_event_loop(self, monkeypatch):
        from bartholomew.kernel.daemon import KernelDaemon
        from identity_interpreter.adapters import reflection_generator

        seen: dict = {}

        class _SlowGenerator:
            def __init__(self, identity_path="Identity.yaml"):
                pass

            def generate_daily_reflection(self, **kwargs):
                seen["thread"] = threading.current_thread()
                seen["kwargs"] = kwargs
                time.sleep(0.6)  # a model call, standing in for Ollama
                return {
                    "content": "# Daily Reflection\n\ncomposed",
                    "meta": {"generator": "test"},
                    "safety": {},
                    "success": True,
                }

        monkeypatch.setattr(reflection_generator, "ReflectionGenerator", _SlowGenerator)

        class _Store:
            async def get_system_metrics(self):
                return {"pending_nudges": 0}

        class _Narrator:
            def generate_daily_reflection_narrative(self, now):
                return "evidence"

        class _Mem:
            def __init__(self):
                self.saved = []

            async def insert_reflection(self, **kwargs):
                self.saved.append(kwargs)

        daemon = SimpleNamespace(
            scheduler_store=_Store(),
            narrator=_Narrator(),
            blocking_executor=None,
            tz=timezone.utc,
            mem=_Mem(),
        )

        ticks = 0

        async def _ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        ticker = asyncio.create_task(_ticker())
        try:
            await KernelDaemon._run_daily_reflection(daemon, datetime.now(tz=timezone.utc))
        finally:
            ticker.cancel()

        assert seen["thread"] is not threading.main_thread()
        assert "backend" not in seen["kwargs"], "reflection must not pin the backend"
        assert ticks >= 5, f"event loop starved during composition ({ticks} ticks)"
        assert daemon.mem.saved and daemon.mem.saved[0]["kind"] == "daily_journal"
