"""
LLM Adapter for Ollama
Provides interface for local model inference via Ollama

BGPR-01 (Golden Path Runtime Repair). Traced against a fake Ollama with a
fresh database, this adapter turned a healthy but slow, busy or unusually
addressed Ollama into untruthful outcomes. Each was reproduced before it was
repaired:

* A model-listing call that ran past its bound was reported as
  ``model_not_available`` -- "Model 'mistral:7b-instruct' not found. Run:
  ollama pull mistral:7b-instruct" -- while the model was installed and
  Ollama would have generated. ``generate()`` no longer gates a generation on
  a separate listing call that can fail on its own; the generation call's
  own outcome is the truth. A missing model is still ``model_not_available``,
  because Ollama's ``/api/generate`` answers 404 for one.
* ``OLLAMA_HOST`` in the forms Ollama itself documents (``127.0.0.1:11434``,
  ``0.0.0.0``, ``:11434``) produced a URL with no scheme, which ``requests``
  refused -- and that refusal, too, read as "model not found". The host is
  now resolved the way Ollama's own client resolves it.
* A fixed 60 s generation bound covered a cold model load plus generation.
  On a CPU-only machine the first call after ``ollama serve`` can
  legitimately take longer. The bound is configurable and defaults to a
  value that tolerates a cold load; a hung provider still becomes a truthful
  ``timeout`` rather than an indefinite wait.

``probe()`` is the readiness counterpart: it answers the same question the
health surface asks -- can this process reach Ollama, and is the selected
model installed -- with the same listing call, the same bound and the same
reason vocabulary, so readiness and generation agree by construction.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import unquote

import requests

# Try to import official ollama client (httpx is its own dependency, and is
# only needed here to give that client separate connect and read bounds)
try:
    import httpx
    from ollama import Client as OllamaClient

    HAS_OLLAMA_CLIENT = True
except ImportError:
    HAS_OLLAMA_CLIENT = False

#: Where Ollama listens unless told otherwise, and where its own client
#: connects unless told otherwise. ``127.0.0.1`` rather than ``localhost``:
#: the server binds the IPv4 loopback address, and a hostname that resolves
#: to ``::1`` first adds a connection attempt that a real machine's firewall
#: or VPN can turn from an instant refusal into a wait.
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"

#: Read bound for one generation. Covers a cold model load plus generation
#: on a CPU-only machine; a provider that has genuinely hung still becomes a
#: truthful ``timeout`` when it expires.
GENERATE_TIMEOUT_ENV = "BARTH_OLLAMA_TIMEOUT_SECONDS"
DEFAULT_GENERATE_TIMEOUT_SECONDS = 120.0

#: Read bound for listing installed models (readiness, ``is_available()``).
LIST_TIMEOUT_ENV = "BARTH_OLLAMA_LIST_TIMEOUT_SECONDS"
DEFAULT_LIST_TIMEOUT_SECONDS = 5.0

#: Bound on establishing the TCP connection, for every call.
CONNECT_TIMEOUT_SECONDS = 5.0

#: httpx's timeout family, by name so httpx need not be imported here. A
#: connect-phase timeout is deliberately absent: it is classified as a
#: connection failure below, because the bound that expired is the connect
#: bound, not the generation bound.
_TIMEOUT_EXCEPTION_NAMES = frozenset(
    {"ReadTimeout", "WriteTimeout", "PoolTimeout", "TimeoutException"},
)
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


def _seconds_from_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _split_host_port(rest: str) -> tuple[str, str]:
    if rest.startswith("["):
        end = rest.find("]")
        if end == -1:
            return rest, ""
        host = rest[: end + 1]
        port = rest[end + 2 :] if rest[end + 1 : end + 2] == ":" else ""
        return host, port
    if rest.count(":") > 1:
        # A bare IPv6 literal with no port.
        return f"[{rest}]", ""
    host, _, port = rest.partition(":")
    return host, port


def resolve_ollama_endpoint(raw: str | None = None) -> tuple[str, tuple[str, str] | None]:
    """
    The base URL this adapter will call, from ``OLLAMA_HOST`` (or ``raw``),
    plus any HTTP basic-auth credentials the value carried.

    Accepts every form Ollama documents for the variable, resolved as
    Ollama's own client resolves it: ``host`` and ``host:port`` (port
    11434 when absent), ``:port``, ``scheme://host`` (the scheme's own
    default port, 80 or 443, when absent -- what ``requests`` would have
    used for the raw value too), ``scheme://host:port``, an optional path
    prefix (a reverse proxy in front of Ollama), with or without a trailing
    slash.

    Two rules are this adapter's own, not the client's: a bind-all address
    (``0.0.0.0``, ``[::]``) is the server's listen setting, which a client
    cannot connect to on Windows, so it is read as the loopback address it
    implies for a local client; and a ``user:password@`` component is split
    off and returned separately, so the URL this adapter reports on
    ``/api/health`` and in its error messages never contains a secret.
    """
    value = (os.getenv("OLLAMA_HOST", "") if raw is None else raw).strip()
    if not value:
        return DEFAULT_OLLAMA_HOST, None

    scheme: str | None = None
    rest = value
    if "://" in value:
        scheme, rest = value.split("://", 1)
        scheme = scheme.lower() or None
    authority, _, path = rest.partition("/")

    auth: tuple[str, str] | None = None
    userinfo, at, hostport = authority.rpartition("@")
    if at:
        user, _, password = userinfo.partition(":")
        auth = (unquote(user), unquote(password))

    host, port = _split_host_port(hostport)
    if host in ("", "0.0.0.0", "[::]"):
        host = "127.0.0.1"
    if not port:
        if scheme is None:
            port = "11434"
        else:
            port = "443" if scheme == "https" else "80"
    scheme = scheme or "http"
    prefix = path.strip("/")
    return f"{scheme}://{host}:{port}" + (f"/{prefix}" if prefix else ""), auth


def resolve_ollama_base_url(raw: str | None = None) -> str:
    """The base URL alone -- see resolve_ollama_endpoint()."""
    return resolve_ollama_endpoint(raw)[0]


def _is_loopback(base_url: str) -> bool:
    rest = base_url.split("://", 1)[-1].split("/", 1)[0]
    host, _ = _split_host_port(rest)
    return host.lower() in _LOOPBACK_HOSTS


class _ListingFailedError(Exception):
    """The installed-model listing could not be obtained; carries why."""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class LLMAdapter:
    """Ollama adapter for local LLM inference"""

    def __init__(self, identity_config: Any):
        """
        Initialize LLM adapter

        Args:
            identity_config: Identity configuration object
        """
        self.identity = identity_config
        self.current_model = None
        self.ollama_base_url, auth = resolve_ollama_endpoint()
        self.generate_timeout_seconds = _seconds_from_env(
            GENERATE_TIMEOUT_ENV,
            DEFAULT_GENERATE_TIMEOUT_SECONDS,
        )
        self.list_timeout_seconds = _seconds_from_env(
            LIST_TIMEOUT_ENV,
            DEFAULT_LIST_TIMEOUT_SECONDS,
        )

        # One session for every call this adapter makes, listing and
        # generation alike, so both bounds and both rules below apply to both.
        #
        # Hardening beyond the reproduced runs, recorded as such: Ollama is a
        # local service, and a proxy inherited from the environment (a
        # corporate HTTP_PROXY on the machine, say) is never the right path to
        # a loopback address. Routing through one is exactly the shape of
        # difference -- "works from my shell, fails from Bartholomew" -- that
        # this repair exists to remove, so loopback targets never consult the
        # proxy environment. Credentials carried in OLLAMA_HOST are applied
        # here and nowhere else (never in a URL that is logged or reported).
        loopback = _is_loopback(self.ollama_base_url)
        self._http = requests.Session()
        if loopback:
            self._http.trust_env = False
        if auth is not None:
            self._http.auth = auth

        # Initialize ollama client if available. Generation only: the model
        # listing always goes through the session above, so the listing bound
        # holds on both installs (the client takes one timeout for every call
        # it makes, and would otherwise hold the readiness probe to the
        # generation bound).
        self.client = None
        if HAS_OLLAMA_CLIENT:
            try:
                client_kwargs: dict[str, Any] = {
                    "host": self.ollama_base_url,
                    "timeout": httpx.Timeout(
                        self.generate_timeout_seconds,
                        connect=CONNECT_TIMEOUT_SECONDS,
                    ),
                    "trust_env": not loopback,
                }
                if auth is not None:
                    client_kwargs["auth"] = auth
                self.client = OllamaClient(**client_kwargs)
            except Exception:
                # Fall back to requests-based approach
                pass

        # Model name mapping from Identity.yaml to Ollama
        self.model_mapping = {
            "Mistral-7B-Instruct-GGUF-Q4_K_M": "mistral:7b-instruct",
            "TinyLlama 1.1B": "tinyllama",
            "Phi-4 3B": "phi3:mini",
            # Additional available models
            "Qwen2.5-Coder-7B": "qwen2.5-coder:7b",
            "Gemma3": "gemma3:latest",
        }

    def _map_model_name(self, model_name: str) -> str:
        """Map Identity.yaml model names to Ollama model names"""
        return self.model_mapping.get(model_name, model_name)

    def _ollama_enabled(self) -> bool:
        return bool(self.identity.meta.deployment_profile.runtimes.ollama_enabled)

    # -- classification -------------------------------------------------

    @staticmethod
    def _classify_exception(exc: BaseException) -> str:
        """One reason word for a failed HTTP call, whichever client made it.

        The vocabulary is the one ``ModelBackendError.reason`` already carries
        (``connection_failed`` / ``timeout`` / ``model_not_available``), so a
        caller rendering a degraded state needs nothing new.
        """
        # requests (the default path). ConnectTimeout is both a Timeout and a
        # ConnectionError; it is a failure to *connect* within the connect
        # bound, so it is a connection failure -- the generation bound and
        # its knob are not what expired.
        if isinstance(exc, requests.exceptions.ConnectTimeout):
            return "connection_failed"
        if isinstance(exc, requests.exceptions.Timeout):
            return "timeout"
        if isinstance(exc, requests.exceptions.ConnectionError):
            return "connection_failed"
        if isinstance(exc, requests.exceptions.HTTPError):
            code = getattr(getattr(exc, "response", None), "status_code", None)
            return "model_not_available" if code == 404 else f"http_{code}"
        # ollama client (httpx underneath): ResponseError carries status_code,
        # a refused connection is raised as the built-in ConnectionError, and
        # httpx's timeout family is recognised by name so httpx need not be
        # imported here.
        status = getattr(exc, "status_code", None)
        if isinstance(status, int):
            return "model_not_available" if status == 404 else f"http_{status}"
        if type(exc).__name__ == "ConnectTimeout":
            return "connection_failed"
        if type(exc).__name__ in _TIMEOUT_EXCEPTION_NAMES or isinstance(exc, TimeoutError):
            return "timeout"
        if isinstance(exc, ConnectionError):
            return "connection_failed"
        return "adapter_error"

    @staticmethod
    def _response_body(exc: BaseException) -> str:
        """Whatever the provider said in the failed response's body, so an
        Ollama error such as "model requires more system memory" reaches the
        person instead of only the status line."""
        response = getattr(exc, "response", None)
        text = getattr(response, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
        error = getattr(exc, "error", None)  # ollama.ResponseError
        if isinstance(error, str) and error.strip():
            return error.strip()
        return ""

    def _describe_failure(self, reason: str, ollama_model: str, exc: BaseException) -> str:
        host = self.ollama_base_url
        if reason == "model_not_available":
            return f"[ERROR] Model '{ollama_model}' not found. Run: ollama pull {ollama_model}"
        if reason == "connection_failed":
            return (
                f"[ERROR] Could not connect to Ollama at {host} "
                f"(no connection within {CONNECT_TIMEOUT_SECONDS:g}s, or refused)"
            )
        if reason == "timeout":
            return (
                f"[ERROR] Request timed out for model {ollama_model} at {host} after "
                f"{self.generate_timeout_seconds:g}s (set {GENERATE_TIMEOUT_ENV} higher if "
                "this machine needs longer for a cold model load)"
            )
        if reason.startswith("http_"):
            body = self._response_body(exc)
            return (
                f"[ERROR] Ollama answered HTTP {reason[5:]} for model {ollama_model} at {host}: "
                f"{body or exc}"
            )
        return f"[ERROR] {exc}"

    # -- listing --------------------------------------------------------

    @staticmethod
    def _name_matches(names: list[str], ollama_model: str) -> bool:
        return any(name == ollama_model or name.startswith(f"{ollama_model}:") for name in names)

    def _list_models(self) -> list[str]:
        """Names of the models Ollama has installed. Raises _ListingFailedError.

        Always the session, never the optional client, so the listing bound
        (and therefore the readiness probe's bound) is the same on every
        install -- see __init__.
        """
        try:
            response = self._http.get(
                f"{self.ollama_base_url}/api/tags",
                timeout=(CONNECT_TIMEOUT_SECONDS, self.list_timeout_seconds),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise _ListingFailedError(self._classify_exception(exc), str(exc)) from exc
        names = []
        for entry in payload.get("models", []):
            name = entry.get("name") or entry.get("model")
            if name:
                names.append(str(name))
        return names

    def _model_exists(self, ollama_model: str) -> bool:
        """
        Check if model exists in local Ollama

        Args:
            ollama_model: Ollama model name

        Returns:
            True if model exists locally. False when it does not, and also
            when the listing could not be obtained at all -- callers that need
            the difference use probe().
        """
        try:
            names = self._list_models()
        except _ListingFailedError:
            return False
        return self._name_matches(names, ollama_model)

    def probe(self, model: str) -> dict[str, Any]:
        """
        Whether a generation for ``model`` can be expected to reach a real,
        installed model right now. The readiness counterpart of generate().

        Returns a dict with ``reachable`` (True / False / None when it could
        not be determined), ``reason`` (None, or one of ``ollama_disabled``,
        ``connection_failed``, ``timeout``, ``model_not_available``,
        ``adapter_error`` / ``http_<code>``), ``detail``, ``host``, ``model``
        and ``ollama_model``. Never raises.
        """
        ollama_model = self._map_model_name(model)
        report: dict[str, Any] = {
            "host": self.ollama_base_url,
            "model": model,
            "ollama_model": ollama_model,
            "reachable": None,
            "reason": None,
            "detail": None,
        }
        try:
            enabled = self._ollama_enabled()
        except Exception as exc:
            report.update(reason="adapter_error", detail=f"could not read runtimes: {exc}")
            return report
        if not enabled:
            report.update(reachable=False, reason="ollama_disabled")
            return report

        try:
            names = self._list_models()
        except _ListingFailedError as failed:
            report["detail"] = failed.detail
            if failed.reason == "timeout":
                # Ollama did not answer within the bound. That is the absence
                # of a reading, not a reading of failure.
                report["reason"] = "timeout"
            else:
                report.update(reachable=False, reason=failed.reason)
            return report

        if self._name_matches(names, ollama_model):
            report["reachable"] = True
        else:
            report.update(
                reachable=False,
                reason="model_not_available",
                detail=f"installed: {', '.join(names) or '(none)'}",
            )
        return report

    # -- generation -----------------------------------------------------

    @staticmethod
    def _failure(
        model: str,
        parameters: dict[str, Any],
        reason: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "response": message,
            "tokens_used": 0,
            "model": model,
            "parameters": parameters,
            "success": False,
            "error": reason,
        }

    def generate(
        self,
        prompt: str,
        model: str,
        parameters: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Generate response from LLM via Ollama

        Args:
            prompt: Input prompt
            model: Model name (from Identity.yaml)
            parameters: Model parameters (temperature, etc.)
            context: Optional context

        Returns:
            Dict with 'response', 'tokens_used', 'model', 'success'; on
            failure also 'error', one of ollama_disabled / empty_prompt /
            connection_failed / timeout / model_not_available / adapter_error
            / http_<code>. Never a fabricated response.
        """
        # Check if Ollama is enabled
        if not self._ollama_enabled():
            return self._failure(
                model,
                parameters,
                "ollama_disabled",
                "[ERROR] Ollama is disabled in Identity.yaml",
            )

        # Validate prompt
        if not prompt or not prompt.strip():
            return self._failure(model, parameters, "empty_prompt", "[ERROR] Empty prompt provided")

        ollama_model = self._map_model_name(model)
        self.current_model = ollama_model

        # Prepare generation parameters
        options = {
            "temperature": parameters.get("temperature", 0.2),
            "top_p": parameters.get("top_p", 0.9),
            "num_predict": parameters.get("max_tokens", 1536),
        }

        # One call, whose own outcome is the answer. There is deliberately no
        # separate "does the model exist" round trip before it: that call
        # could fail on its own (slow listing, starved provider) and was then
        # reported as a missing model, sending a person to `ollama pull` a
        # model they had already pulled. Ollama answers 404 for a model it
        # does not have, and that is classified below.
        try:
            # Try using ollama client first
            if self.client is not None:
                response = self.client.chat(
                    model=ollama_model,
                    messages=[{"role": "user", "content": prompt}],
                    options=options,
                )
                text = response.get("message", {}).get("content", "")
                tokens = response.get("eval_count", 0) or 0
                return {
                    "response": text,
                    "tokens_used": tokens,
                    "model": ollama_model,
                    "parameters": parameters,
                    "success": True,
                }

            # Fallback to REST API
            payload = {
                "model": ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": options,
            }
            response = self._http.post(
                f"{self.ollama_base_url}/api/generate",
                json=payload,
                timeout=(CONNECT_TIMEOUT_SECONDS, self.generate_timeout_seconds),
            )
            response.raise_for_status()
            result = response.json()
            return {
                "response": result.get("response", ""),
                "tokens_used": result.get("eval_count", 0),
                "model": ollama_model,
                "parameters": parameters,
                "success": True,
            }
        except Exception as exc:
            reason = self._classify_exception(exc)
            return self._failure(
                ollama_model,
                parameters,
                reason,
                self._describe_failure(reason, ollama_model, exc),
            )

    def is_available(self, model: str) -> bool:
        """
        Check if model is available

        Args:
            model: Model name (from Identity.yaml)

        Returns:
            True if available
        """
        ollama_model = self._map_model_name(model)
        return self._model_exists(ollama_model)

    def get_context_window(self, model: str) -> int:
        """
        Get model's context window size

        Args:
            model: Model name

        Returns:
            Context window size in tokens
        """
        # Stub - returns default
        return 8192
