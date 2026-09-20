"""
Retrieval Engine for Bartholomew
Implements privacy-aware vector search with rule-based filtering

Provides unified get_retriever(mode="hybrid"|"vector"|"fts") factory for
constructing retriever instances with consistent .retrieve() API.
"""

from __future__ import annotations

import html
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Any

from bartholomew.kernel.consent_gate import VERDICT_CURRENTLY_VALID
from bartholomew.kernel.embedding_engine import (
    EmbedderUnavailableError,
    EmbeddingEngine,
    EmbeddingMode,
    get_embedding_engine,
    get_embedding_status,
)
from bartholomew.kernel.fts_client import FTS5ProbeResult, FTS5Status, probe_fts5
from bartholomew.kernel.memory_rules import MemoryRulesEngine
from bartholomew.kernel.vector_store import VectorStore

logger = logging.getLogger(__name__)


#: FTS5 availability, cached **per database** and only when conclusive.
#:
#: R-RETRIEVAL-1: this was a single process-global boolean, keyed by nothing
#: and never re-probed, so the first probe in a process answered for every
#: later caller whatever database they retrieved from, and a transient failure
#: latched for the life of the process. Two rules replace that:
#:
#:   1. the key is the resolved database, so one database's answer can never
#:      decide another's;
#:   2. only a conclusive outcome (AVAILABLE or ABSENT) is stored. A
#:      `PROBE_ERROR` is never written here, so the next caller re-probes and
#:      a temporary failure cannot poison later retrieval.
_fts5_probe_cache: dict[str, FTS5ProbeResult] = {}


def _fts5_cache_key(db_path: str) -> str:
    """The identity of the database whose capability is being measured.

    Real paths are normalised so two spellings of the same file share one
    answer; SQLite's special forms (`:memory:`, `file:` URIs) are left alone
    because normalising them as filesystem paths would be a lie about what
    they name.
    """
    if not db_path or db_path == ":memory:" or db_path.startswith("file:"):
        return db_path
    return os.path.abspath(db_path)


def _names_a_missing_file(db_path: str) -> bool:
    """Whether `db_path` names an ordinary file that does not exist yet.

    SQLite's special forms (`:memory:`, `file:` URIs) and the empty string all
    name something other than a path on disk, so they are never "missing".
    """
    if not db_path or db_path == ":memory:" or db_path.startswith("file:"):
        return False
    return not os.path.exists(db_path)


def check_fts5(db_path: str, *, force: bool = False) -> FTS5ProbeResult:
    """
    Whether FTS5 is usable for **this** database, with the reason behind it.

    Caches conclusive answers per database. An inconclusive probe -- the
    database could not be opened, or the probe itself failed -- is returned
    but never cached, so the next call tries again. That is the recovery
    contract: a transient failure costs one extra probe, not a process
    lifetime of degraded retrieval.

    Args:
        db_path: Path to SQLite database
        force: Re-probe and overwrite any cached conclusive answer. Used by
            callers that know the underlying build or file changed; the normal
            path never needs it.

    Returns:
        FTS5ProbeResult for this database
    """
    key = _fts5_cache_key(db_path)

    if not force:
        cached = _fts5_probe_cache.get(key)
        if cached is not None:
            return cached

    if _names_a_missing_file(db_path):
        # Do not bring a database into existence merely to ask a question
        # about it. `describe_retrieval()` is read by health and CLI surfaces
        # now, and a reporting call must not create `data/barth.db` as a side
        # effect. Unknown is the honest answer, and it is not cached, so the
        # next call answers properly once the database exists.
        result = FTS5ProbeResult(
            FTS5Status.PROBE_ERROR,
            f"no database at {db_path} yet, so FTS5 support for it is not established",
        )
        logger.debug("FTS5 availability for %s is UNKNOWN: %s", db_path, result.detail)
        return result

    try:
        conn = sqlite3.connect(db_path)
    except Exception as exc:
        # The database could not be opened at all. That says nothing about
        # whether this SQLite build has FTS5, so it is not recorded as an
        # answer and not cached.
        result = FTS5ProbeResult(
            FTS5Status.PROBE_ERROR,
            f"could not open {db_path}: {type(exc).__name__}: {exc}",
        )
        logger.warning(
            "FTS5 availability for %s is UNKNOWN: %s. Not caching this; the "
            "probe will be retried on the next retrieval.",
            db_path,
            result.detail,
        )
        return result

    try:
        result = probe_fts5(conn)
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - close failure is not an answer
            logger.debug("Closing the FTS5 probe connection for %s failed", db_path)

    if not result.conclusive:
        logger.warning(
            "FTS5 availability for %s is UNKNOWN: %s. Not caching this; the "
            "probe will be retried on the next retrieval.",
            db_path,
            result.detail,
        )
        return result

    _fts5_probe_cache[key] = result

    if result.status is FTS5Status.ABSENT:
        logger.warning(
            "FTS5 is absent for %s (%s); hybrid mode will operate vector-only "
            "and fts mode resolved from config/env will degrade to vector-only "
            "to keep the API stable.",
            db_path,
            result.detail,
        )
    else:
        logger.debug("FTS5 is available for %s", db_path)

    return result


def reset_fts5_cache(db_path: str | None = None) -> None:
    """Forget cached FTS5 answers -- all of them, or one database's.

    The supported way to clear this state. Tests previously reassigned a
    private module global, which is exactly the coupling R-RETRIEVAL-1 is
    about; production callers that rebuild a database from scratch can use it
    too.
    """
    if db_path is None:
        _fts5_probe_cache.clear()
    else:
        _fts5_probe_cache.pop(_fts5_cache_key(db_path), None)


def _check_fts5_once(db_path: str) -> bool:
    """Fail-safe boolean view of `check_fts5()`: "can I use FTS right now?".

    Retained for callers that only need the yes/no. Anything deciding whether
    to *permanently* drop a retrieval arm, or reporting availability to an
    operator, must read `check_fts5()` and its `status` -- a False here still
    cannot tell an absent FTS5 from a failed probe.
    """
    return check_fts5(db_path).available


def resolve_embedding_engine() -> tuple[EmbeddingEngine | None, Any]:
    """The engine to retrieve with, and the truthful status behind it.

    Returns `(None, status)` only when no engine can be built at all -- the
    configured embedder is unavailable and degrading to the deterministic
    embedder was not authorised. In that state there is nothing to search a
    vector population with, so vector retrieval genuinely cannot run.

    Note that `DISABLED` is *not* such a state. `BARTHO_EMBED_ENABLED` gates
    whether new embeddings are written; a caller that explicitly constructs a
    vector retriever still gets an engine, exactly as before. What changes is
    that the status travelling alongside it says so, so the caller can report
    what it is really doing instead of implying semantic retrieval.
    """
    status = get_embedding_status()
    if status.mode is EmbeddingMode.UNAVAILABLE:
        return None, status
    try:
        return get_embedding_engine(), status
    except EmbedderUnavailableError:  # pragma: no cover - status covers this
        return None, get_embedding_status()


def describe_retrieval(mode: str | None = None, db_path: str | None = None) -> dict[str, Any]:
    """A truthful description of what retrieval will actually do right now.

    One accessor for every reporting surface -- health, CLI, result contracts
    -- so none of them has to reconstruct the answer from configuration and
    none of them can drift from the others.

    Two separate questions, deliberately not collapsed into one field, for the
    same reason `/api/health` separates a model being *selected* from being
    *reachable*:

      `mode_effective`  which retrieval arms will actually run;
      `semantic`        whether vector similarity means anything when they do.

    A hybrid configuration serving the deterministic development embedder is
    still structurally hybrid -- and is not semantic retrieval. Reporting only
    the mode would call that hybrid and be believed, which is precisely the
    OP-W003 condition: retrieval mode and quality not known and not truthfully
    reported at the time.

    R-RETRIEVAL-1 adds the lexical half of the same question. The embedding
    degradation was reported here; the FTS one was not, so nothing an operator
    or a result contract read could say that lexical retrieval had been
    dropped. `fts` now carries that state, and a **conclusively absent** FTS5
    moves `mode_effective` exactly as the embedder does.

    An *unknown* FTS5 -- a probe that could not be completed -- deliberately
    does neither. It is reported as `status: probe_error` and changes nothing
    else: claiming degradation that has not been established is the same class
    of untruth as hiding degradation that has.

    Args:
        mode: Retrieval mode to describe. As in `get_retriever`, passing one
            explicitly is honoured rather than degraded.
        db_path: The database to report FTS5 availability for. Resolved from
            config/env exactly as `get_retriever` resolves it when None, so a
            no-argument caller describes the database retrieval would use.
    """
    mode_explicit = mode is not None
    configured_mode = _resolve_mode(mode)
    embedding_status = get_embedding_status()

    resolved_db_path = _resolve_db_path(db_path)
    fts_probe = check_fts5(resolved_db_path)
    fts_absent = fts_probe.status is FTS5Status.ABSENT

    effective_mode = configured_mode
    degraded = embedding_status.degraded
    reasons: list[str] = []
    if embedding_status.reason:
        reasons.append(embedding_status.reason)

    # The lexical arm first, mirroring the order `get_retriever` applies.
    if fts_absent:
        if configured_mode == "fts" and not mode_explicit:
            effective_mode = "vector"
            degraded = True
            reasons.append(
                "fts retrieval was requested from configuration but SQLite FTS5 "
                "is absent, so lexical retrieval is not running and retrieval "
                "degrades to vector-only.",
            )
        elif configured_mode == "fts":
            # An explicit fts request is honoured -- and cannot match anything.
            effective_mode = "none"
            degraded = True
            reasons.append(
                "fts retrieval was requested explicitly and is honoured, but "
                "SQLite FTS5 is absent, so the lexical arm cannot return "
                "anything.",
            )
        elif configured_mode == "hybrid":
            effective_mode = "vector"
            degraded = True
            reasons.append(
                "hybrid retrieval is configured but SQLite FTS5 is absent, so "
                "only the vector arm is contributing.",
            )

    if effective_mode in ("hybrid", "vector"):
        if embedding_status.mode is EmbeddingMode.UNAVAILABLE:
            # No engine at all: this mirrors what `get_retriever` will build.
            effective_mode = "fts" if effective_mode == "hybrid" else "none"
            degraded = True
            reasons.append(
                f"{configured_mode} retrieval was requested but no embedder could "
                "be loaded, so vector similarity is not contributing at all.",
            )
            # Only claimable when FTS5 was *proved* absent. A configured
            # `vector` mode reaches "none" on the embedder alone, whatever
            # FTS5 is doing, so without this conjunct the sentence asserts an
            # absence the same payload's `fts` block contradicts -- and, on a
            # probe that merely failed, asserts a conclusive absence from an
            # inconclusive observation. That is the untruth this package
            # exists to remove.
            if effective_mode == "none" and fts_absent:
                reasons.append(
                    "No retrieval arm is operational: there is no embedder, and FTS5 is absent.",
                )
        elif embedding_status.mode is EmbeddingMode.DISABLED:
            if fts_absent:
                # "Lexical only" is the usual truth here -- but not when the
                # lexical arm was just removed a few lines above. With no
                # vectors being written AND no FTS5, nothing can match at all,
                # and saying "lexical only" next to `fts.status: absent` would
                # be the same payload contradicting itself.
                effective_mode = "none"
                degraded = True
                reasons.append(
                    f"{configured_mode} retrieval is configured, but with embeddings "
                    "disabled no vectors are written and SQLite FTS5 is absent, so "
                    "no retrieval arm can match anything.",
                )
            else:
                # Nothing is writing vectors, so the vector arm exists but has
                # nothing meaningful in it. Not "degraded" -- the system is doing
                # what it was configured to do -- but not semantic either.
                reasons.append(
                    f"{configured_mode} retrieval is configured, but with embeddings "
                    "disabled no vectors are written, so matching is lexical only.",
                )
        elif not embedding_status.semantic:
            degraded = True
            reasons.append(
                f"{configured_mode} retrieval is running on the deterministic "
                "development embedder; its similarity scores carry no semantic "
                "meaning and are searched as a separate vector population.",
            )

    return {
        "mode_configured": configured_mode,
        "mode_effective": effective_mode,
        # True only when a vector arm is running AND it is genuinely semantic.
        "semantic": embedding_status.semantic and effective_mode in ("hybrid", "vector"),
        "degraded": degraded,
        "reason": " ".join(reasons) or None,
        "embedding": embedding_status.as_dict(),
        # The lexical capability behind `mode_effective`, and the database it
        # was measured on -- configuration and runtime capability, separately.
        "fts": {**fts_probe.as_dict(), "db_path": resolved_db_path},
    }


@dataclass
class RetrievalFilters:
    """Filters for retrieval queries"""

    kinds: list[str] | None = None
    source: str | None = None  # 'summary' or 'full'
    after: str | None = None  # ISO timestamp
    before: str | None = None  # ISO timestamp


@dataclass
class RetrievedItem:
    """Single retrieved memory item

    W03-D adds `verdict` and `provenance`, the retrieval-side half of the
    `memory-retrieval-governance` shared contract. Every item a retriever
    returns is `currently_valid` -- a revoked, superseded or expired memory is
    excluded, not returned with a warning flag -- so `verdict` is carried for
    the *consumer's* benefit (W03-B's executive and W03-E's operator surface
    can state, honestly, that what they applied was checked), not as something
    a caller is expected to re-filter on. `provenance` carries the stored
    origin, confidence and validity window for the same reason: a consumer can
    present recalled material with its source without reaching into
    `memories` itself.
    """

    memory_id: int
    score: float
    snippet: str
    recall_policy: str | None = None
    kind: str | None = None
    context_only: bool = False
    policy_flags: set = None  # Phase 2d+: Set of policy flags
    verdict: str = VERDICT_CURRENTLY_VALID
    provenance: dict[str, Any] | None = None

    def __post_init__(self):
        """Initialize policy_flags if not provided"""
        if self.policy_flags is None:
            self.policy_flags = set()
        if self.provenance is None:
            self.provenance = {}


class Retriever:
    """
    Privacy-aware retrieval engine

    Integrates vector search with memory rules to enforce:
    - never_store: not present (no embeddings)
    - ask_before_store: excluded unless consent granted
    - context_only: returned but marked for internal use
    """

    def __init__(
        self,
        rules_engine: MemoryRulesEngine,
        vector_store: VectorStore,
        embedding_engine: EmbeddingEngine | None = None,
        memory_store: Any | None = None,
    ):
        """
        Initialize retriever

        Args:
            rules_engine: Memory rules engine for policy evaluation
            vector_store: Vector store for similarity search
            embedding_engine: Embedding engine (defaults to singleton)
            memory_store: Memory store for loading full memory data
        """
        self.rules_engine = rules_engine
        self.vector_store = vector_store
        self.embedding_engine = embedding_engine or get_embedding_engine()
        self.memory_store = memory_store

    def query(
        self,
        text: str,
        top_k: int = 8,
        filters: RetrievalFilters | None = None,
    ) -> list[RetrievedItem]:
        """
        Query for similar memories

        Args:
            text: Query text
            top_k: Number of results to return
            filters: Optional filters

        Returns:
            List of retrieved items, sorted by relevance
        """
        if filters is None:
            filters = RetrievalFilters()

        # Embed query text
        try:
            qvec = self.embedding_engine.embed_texts([text])[0]
        except Exception as e:
            logger.error(f"Failed to embed query: {e}")
            return []

        # Vector search with strict model matching (Phase 2d+)
        try:
            provider, model, embedder_kind = self.embedding_engine.storage_identity
            candidates = self.vector_store.search(
                qvec,
                top_k=top_k * 2,  # Get more candidates for filtering
                provider=provider,
                model=model,
                dim=self.embedding_engine.config.dim,
                source=filters.source,
                # Phase 2d Fixpack v3: relax provider/model matching for
                # vector-only Retriever while keeping dim strict, to ensure
                # tests that use ad-hoc model names (e.g. "test-model") can
                # still retrieve their embeddings.
                allow_mismatch=True,
                # NOT relaxed by allow_mismatch: the query vector came from
                # this engine, so only vectors from the same kind of embedder
                # can be meaningfully scored against it.
                embedder_kind=embedder_kind,
            )
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return []

        if not candidates:
            return []

        # Load consented memory IDs once per query (Phase 2d's memory_consent
        # table -- see ConsentGate), so _should_include() can tell an
        # actually-consented ask_before_store memory apart from one that
        # never got consent, instead of excluding every requires_consent
        # memory unconditionally regardless of its real consent state.
        consented_ids = self._get_consented_ids()

        # W03-D: the retrieval-side validity verdict, computed once for the
        # whole candidate set through the owned consent-gate seam. Relevance
        # logic below is untouched -- an invalid memory is dropped before it
        # is scored, not scored and then demoted, because a claim that does
        # not hold is not weak evidence, it is not evidence.
        verdicts = self._validity_verdicts([memory_id for memory_id, _ in candidates])

        # Load memory data and apply rule-based filtering
        # Store tuples of (item, ts) for tie-breaking
        results_with_ts = []
        for memory_id, score in candidates:
            # Load memory details
            memory_data = self._load_memory(memory_id)
            if memory_data is None:
                continue

            # Apply filters
            if not self._passes_filters(memory_data, filters):
                continue

            # Evaluate rules for this memory
            evaluated = self.rules_engine.evaluate(memory_data)

            # Check if memory should be excluded from retrieval
            if not self._should_include(evaluated, memory_id, consented_ids):
                continue

            verdict = verdicts.get(memory_id)
            if verdict is not None and not verdict.currently_valid:
                logger.debug(
                    "Excluding memory %s from vector retrieval: %s (%s)",
                    memory_id,
                    verdict.verdict,
                    verdict.reason,
                )
                continue

            # Phase 2d+: Apply retrieval boost from rules
            boost = evaluated.get("retrieval", {}).get("boost", 1.0)
            final_score = score * float(boost)

            # Phase 2d+: Build policy flags set
            policy_flags = set()
            recall_policy = evaluated.get("recall_policy")
            if recall_policy == "context_only":
                policy_flags.add("context_only")

            # Build result item
            item = RetrievedItem(
                memory_id=memory_id,
                score=final_score,
                snippet=self._extract_snippet(memory_data),
                recall_policy=recall_policy,
                kind=memory_data.get("kind"),
                context_only=(recall_policy == "context_only"),
                policy_flags=policy_flags,
                verdict=verdict.verdict if verdict else VERDICT_CURRENTLY_VALID,
                provenance=dict(verdict.provenance or {}) if verdict else {},
            )

            # Store with timestamp for tie-breaking
            results_with_ts.append((item, memory_data.get("ts")))

        # Sort by score desc, recency desc, mem_id asc and take top_k
        def sort_key(item_ts_tuple):
            item, ts = item_ts_tuple
            # Parse recency timestamp to epoch for sorting
            recency_epoch = 0.0
            if ts:
                try:
                    from datetime import datetime

                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    recency_epoch = dt.timestamp()
                except Exception:
                    pass
            return (-item.score, -recency_epoch, item.memory_id)

        results_with_ts.sort(key=sort_key)
        return [item for item, _ in results_with_ts[:top_k]]

    def _load_memory(self, memory_id: int) -> dict[str, Any] | None:
        """
        Load memory data from store

        Args:
            memory_id: Memory ID to load

        Returns:
            Memory dict or None if not found
        """
        if self.memory_store is None:
            # No store available, return minimal data
            return {"id": memory_id, "kind": "unknown"}

        try:
            # Assume store has a sync method to get by ID
            # For async stores, we'd need to handle differently
            import sqlite3

            with sqlite3.connect(self.memory_store.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT id, kind, key, value, summary, ts FROM memories WHERE id=?",
                    (memory_id,),
                )
                row = cursor.fetchone()

                if row:
                    return {
                        "id": row["id"],
                        "kind": row["kind"],
                        "key": row["key"],
                        "value": row["value"],
                        "summary": row["summary"],
                        "ts": row["ts"],
                    }
        except Exception as e:
            logger.error(f"Failed to load memory {memory_id}: {e}")

        return None

    def _passes_filters(self, memory: dict[str, Any], filters: RetrievalFilters) -> bool:
        """Check if memory passes retrieval filters"""
        # Kind filter
        if filters.kinds is not None:
            if memory.get("kind") not in filters.kinds:
                return False

        # Timestamp filters
        ts = memory.get("ts")
        if ts:
            if filters.after and ts < filters.after:
                return False
            if filters.before and ts > filters.before:
                return False

        return True

    def _get_consented_ids(self) -> set[int]:
        """Load the set of memory IDs with a recorded memory_consent row."""
        from bartholomew.kernel.consent_gate import ConsentGate

        return ConsentGate(self.vector_store.db_path).get_consented_memory_ids()

    def _validity_verdicts(self, memory_ids: list[int]) -> dict[int, Any]:
        """W03-D validity verdicts for this candidate set, from the gate.

        Computed through `ConsentGate` rather than here so there is exactly
        one place that decides what "still true" means. This retriever owns
        relevance; it does not own governance.
        """
        from bartholomew.kernel.consent_gate import ConsentGate

        return ConsentGate(self.vector_store.db_path).validity_verdicts(memory_ids)

    def _should_include(
        self,
        evaluated: dict[str, Any],
        memory_id: int | None = None,
        consented_ids: set[int] | None = None,
    ) -> bool:
        """
        Check if memory should be included in retrieval results

        Exclusions:
        - never_store: should not have embeddings anyway
        - ask_before_store: exclude unless a memory_consent record exists

        Inclusions (but marked):
        - context_only: include but mark for internal use
        """
        # Check if storage was blocked (shouldn't have embeddings)
        if not evaluated.get("allow_store", True):
            return False

        # Check if consent is required but not granted -- consult the same
        # memory_consent table ConsentGate reads, so a memory that actually
        # got consent (e.g. via upsert_memory()'s embedding flow) is
        # retrievable, not excluded unconditionally.
        if evaluated.get("requires_consent", False):
            if consented_ids is None or memory_id not in consented_ids:
                return False

        return True

    def _extract_snippet(self, memory: dict[str, Any]) -> str:
        """
        Extract HTML-safe centered excerpt from memory for display

        Prefers summary over full content.
        """
        # Prefer summary
        summary = memory.get("summary")
        if summary:
            excerpt = self._center_excerpt(summary, 200)
            return html.escape(excerpt, quote=False)

        # Fall back to value
        value = memory.get("value", "")
        excerpt = self._center_excerpt(value, 200)
        return html.escape(excerpt, quote=False)

    def _center_excerpt(self, text: str, max_len: int) -> str:
        """
        Extract a centered excerpt from text with ellipses

        Args:
            text: Source text
            max_len: Maximum excerpt length

        Returns:
            Centered excerpt with ' … ' ellipses as needed
        """
        if len(text) <= max_len:
            return text

        # Calculate centered window
        start = max(0, (len(text) - max_len) // 2)
        end = start + max_len

        excerpt = text[start:end]

        # Add leading ellipsis if not at start
        if start > 0:
            excerpt = " … " + excerpt

        # Add trailing ellipsis if not at end
        if end < len(text):
            excerpt = excerpt + " … "

        return excerpt


# ============================================================================
# Retrieval Façade: Unified get_retriever() Factory
# ============================================================================


def _resolve_db_path(explicit: str | None = None) -> str:
    """
    Resolve database path with precedence:
    1. Explicit argument
    2. BARTHO_DB_PATH env var
    3. kernel.yaml memory.db_path
    4. Default: "data/barth.db"
    """
    if explicit:
        return explicit

    # Check environment variable
    env_path = os.getenv("BARTHO_DB_PATH")
    if env_path:
        return env_path

    # Check kernel.yaml
    try:
        import yaml

        config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "kernel.yaml")
        if os.path.exists(config_path):
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            db_path = config.get("memory", {}).get("db_path")
            if db_path:
                return db_path
    except Exception as e:
        logger.debug(f"Failed to load db_path from kernel.yaml: {e}")

    # Default
    return "data/barth.db"


def _resolve_mode(explicit: str | None = None) -> str:
    """
    Resolve retrieval mode with precedence:
    1. Explicit argument
    2. BARTHO_RETRIEVAL_MODE env var
    3. kernel.yaml retrieval.mode
    4. Default: "hybrid"

    Returns normalized mode string, raises ValueError if invalid.
    """
    mode = None

    if explicit:
        mode = explicit.strip().lower()
    else:
        # Check environment variable
        env_mode = os.getenv("BARTHO_RETRIEVAL_MODE")
        if env_mode:
            mode = env_mode.strip().lower()
        else:
            # Check kernel.yaml
            try:
                import yaml

                config_path = os.path.join(
                    os.path.dirname(__file__),
                    "..",
                    "..",
                    "config",
                    "kernel.yaml",
                )
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = yaml.safe_load(f) or {}
                    file_mode = config.get("retrieval", {}).get("mode")
                    if file_mode:
                        mode = file_mode.strip().lower()
            except Exception as e:
                logger.debug(f"Failed to load mode from kernel.yaml: {e}")

    # Default to hybrid if still None
    if mode is None:
        mode = "hybrid"

    # Validate
    valid_modes = {"hybrid", "vector", "fts"}
    if mode not in valid_modes:
        raise ValueError(f"Invalid retrieval mode: '{mode}'. Must be one of {valid_modes}")

    return mode


class VectorRetrieverAdapter:
    """
    Adapter to unify Retriever.query() as .retrieve()

    Wraps the vector-only Retriever class to provide a consistent
    .retrieve() interface matching HybridRetriever.
    """

    def __init__(self, retriever: Retriever):
        """
        Initialize adapter

        Args:
            retriever: Vector-only Retriever instance
        """
        self.retriever = retriever

    def retrieve(
        self,
        query: str,
        top_k: int = 20,
        filters: RetrievalFilters | None = None,
    ) -> list[RetrievedItem]:
        """
        Perform vector-only retrieval

        Delegates to underlying Retriever.query() method.

        Args:
            query: Query text
            top_k: Number of results to return
            filters: Optional retrieval filters

        Returns:
            List of RetrievedItem
        """
        return self.retriever.query(query, top_k=top_k, filters=filters)


class FTSOnlyRetriever:
    """
    FTS-only retriever using FTSClient

    Provides a retrieval interface using only full-text search,
    without vector embeddings. Suitable for:
    - Exact keyword matching
    - Phrase searches
    - Low-resource environments
    """

    def __init__(
        self,
        db_path: str,
        rules_engine: MemoryRulesEngine | None = None,
        memory_store: Any | None = None,
    ):
        """
        Initialize FTS-only retriever

        Args:
            db_path: Path to SQLite database
            rules_engine: Optional rules engine for policy filtering
            memory_store: Optional memory store (not currently used)
        """
        from bartholomew.kernel.fts_client import FTSClient

        self.db_path = db_path
        self.fts = FTSClient(db_path)
        self.rules_engine = rules_engine
        self.memory_store = memory_store

    def retrieve(
        self,
        query: str,
        top_k: int = 20,
        filters: RetrievalFilters | None = None,
    ) -> list[RetrievedItem]:
        """
        Perform FTS-only retrieval

        Args:
            query: FTS query string (supports FTS5 syntax)
            top_k: Number of results to return
            filters: Optional filters (kinds, after/before)

        Returns:
            List of RetrievedItem with FTS ranking
        """
        if filters is None:
            filters = RetrievalFilters()

        # Pull FTS candidates (pull extra for filtering)
        try:
            fts_results = self.fts.search(query, limit=top_k * 2, order_by_rank=True)
        except Exception as e:
            logger.error(f"FTS search failed: {e}")
            return []

        if not fts_results:
            return []

        # Build snippet map from FTS results
        fts_snippets = {row["id"]: row.get("snippet") for row in fts_results if row.get("snippet")}

        # Load memory metadata and apply filters
        memory_ids = [r["id"] for r in fts_results]
        metadata = self._load_metadata(memory_ids)
        filtered_ids = self._apply_filters(metadata, filters)

        # Apply rules engine if provided
        rules_data = {}
        if self.rules_engine:
            from bartholomew.kernel.consent_gate import ConsentGate

            consented_ids = ConsentGate(self.db_path).get_consented_memory_ids()
            rules_data = self._evaluate_rules(metadata, filtered_ids, consented_ids)
            filtered_ids = {
                mid for mid in filtered_ids if rules_data.get(mid, {}).get("include", True)
            }

        # W03-D: the validity verdict is applied whether or not a rules engine
        # was supplied. That asymmetry is deliberate. A caller constructing
        # `FTSOnlyRetriever(db_path=...)` with no rules engine -- exactly what
        # this class's own docstring usage example shows, and what the
        # consent-bypass red-team suite exercises -- must still not be able to
        # recall a revoked, superseded or expired memory. Governance that only
        # applies when the caller opted into a rules engine is not governance.
        validity = self._validity_verdicts(sorted(filtered_ids))
        filtered_ids = {
            mid
            for mid in filtered_ids
            if validity.get(mid) is None or validity[mid].currently_valid
        }

        if not filtered_ids:
            return []

        # Normalize FTS ranks to scores
        fts_scores = self._normalize_fts_ranks(fts_results, filtered_ids)

        # Build results with snippets
        results = []
        for memory_id, score in sorted(fts_scores.items(), key=lambda x: x[1], reverse=True)[
            :top_k
        ]:
            data = metadata[memory_id]
            precomputed = fts_snippets.get(memory_id)
            snippet = self._generate_snippet(memory_id, data, query, precomputed=precomputed)

            # Extract rule-based fields
            rule_info = rules_data.get(memory_id, {})
            recall_policy = rule_info.get("recall_policy")
            policy_flags = set()
            if recall_policy == "context_only":
                policy_flags.add("context_only")

            verdict = validity.get(memory_id)
            item = RetrievedItem(
                memory_id=memory_id,
                score=score,
                snippet=snippet,
                recall_policy=recall_policy,
                kind=data.get("kind"),
                context_only=(recall_policy == "context_only"),
                policy_flags=policy_flags,
                verdict=verdict.verdict if verdict else VERDICT_CURRENTLY_VALID,
                provenance=dict(verdict.provenance or {}) if verdict else {},
            )
            results.append(item)

        logger.debug(f"FTS retrieval returned {len(results)} results")
        return results

    def _validity_verdicts(self, memory_ids: list[int]) -> dict[int, Any]:
        """W03-D validity verdicts for these ids, from the consent gate."""
        from bartholomew.kernel.consent_gate import ConsentGate

        return ConsentGate(self.db_path).validity_verdicts(memory_ids)

    def _load_metadata(self, memory_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Load memory metadata for given IDs"""
        if not memory_ids:
            return {}

        placeholders = ",".join("?" * len(memory_ids))
        query = f"""
            SELECT id, kind, key, value, summary, ts
            FROM memories
            WHERE id IN ({placeholders})
        """

        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, memory_ids)
            rows = cursor.fetchall()

            metadata = {}
            for row in rows:
                metadata[row["id"]] = {
                    "id": row["id"],
                    "kind": row["kind"],
                    "key": row["key"],
                    "value": row["value"],
                    "summary": row["summary"],
                    "ts": row["ts"],
                }

            return metadata
        finally:
            if conn:
                conn.close()

    def _apply_filters(self, metadata: dict[int, dict[str, Any]], filters: RetrievalFilters) -> set:
        """Apply retrieval filters, return set of passing memory IDs"""
        filtered = set()

        for memory_id, data in metadata.items():
            # Kind filter
            if filters.kinds is not None:
                if data.get("kind") not in filters.kinds:
                    continue

            # Timestamp filters
            ts = data.get("ts")
            if ts:
                if filters.after and ts < filters.after:
                    continue
                if filters.before and ts > filters.before:
                    continue

            filtered.add(memory_id)

        return filtered

    def _evaluate_rules(
        self,
        metadata: dict[int, dict[str, Any]],
        memory_ids: set,
        consented_ids: set[int] | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Evaluate rules for memories"""
        rules_data = {}
        consented_ids = consented_ids or set()

        for memory_id in memory_ids:
            data = metadata[memory_id]
            evaluated = self.rules_engine.evaluate(data)

            # Check if should be included -- a requires_consent memory with
            # a recorded memory_consent row (see ConsentGate) is retrievable,
            # not excluded unconditionally.
            include = True
            if not evaluated.get("allow_store", True):
                include = False
            if evaluated.get("requires_consent", False) and memory_id not in consented_ids:
                include = False

            rules_data[memory_id] = {
                "include": include,
                "recall_policy": evaluated.get("recall_policy"),
            }

        return rules_data

    def _normalize_fts_ranks(
        self,
        fts_results: list[dict[str, Any]],
        filtered_ids: set,
    ) -> dict[int, float]:
        """
        Normalize FTS BM25 ranks to [0, 1] scores

        Lower rank (better match) -> higher score
        """
        ranks = {}
        for row in fts_results:
            memory_id = row["id"]
            if memory_id in filtered_ids:
                ranks[memory_id] = row["rank"]

        if not ranks:
            return {}

        # Invert ranks to scores
        rank_values = list(ranks.values())
        rmin = min(rank_values)
        rmax = max(rank_values)

        normalized = {}
        if rmax > rmin:
            for memory_id, rank in ranks.items():
                normalized[memory_id] = (rmax - rank) / (rmax - rmin)
        else:
            for memory_id in ranks:
                normalized[memory_id] = 1.0

        return normalized

    def _generate_snippet(
        self,
        memory_id: int,
        metadata: dict[str, Any],
        query: str,
        precomputed: str | None = None,
    ) -> str:
        """
        Generate HTML-safe snippet with FTS highlighting or centered
        excerpt fallback

        Args:
            memory_id: Memory ID
            metadata: Memory metadata dict
            query: Query string (unused in current implementation)
            precomputed: Precomputed FTS snippet from search results

        Returns:
            HTML-escaped snippet string
        """
        # Use precomputed FTS snippet if available
        if precomputed:
            return html.escape(precomputed, quote=False)

        # Fallback: centered excerpt of summary or value
        summary = metadata.get("summary")
        if summary:
            excerpt = self._center_excerpt(summary, 200)
            return html.escape(excerpt, quote=False)

        value = metadata.get("value", "")
        excerpt = self._center_excerpt(value, 200)
        return html.escape(excerpt, quote=False)

    def _center_excerpt(self, text: str, max_len: int) -> str:
        """
        Extract a centered excerpt from text with ellipses

        Args:
            text: Source text
            max_len: Maximum excerpt length

        Returns:
            Centered excerpt with ' … ' ellipses as needed
        """
        if len(text) <= max_len:
            return text

        # Calculate centered window
        start = max(0, (len(text) - max_len) // 2)
        end = start + max_len

        excerpt = text[start:end]

        # Add leading ellipsis if not at start
        if start > 0:
            excerpt = " … " + excerpt

        # Add trailing ellipsis if not at end
        if end < len(text):
            excerpt = excerpt + " … "

        return excerpt


def get_retriever(
    mode: str | None = None,
    db_path: str | None = None,
    rules_engine: MemoryRulesEngine | None = None,
    embedding_engine: EmbeddingEngine | None = None,
    memory_store: Any | None = None,
) -> Any:
    """
    Factory to create retriever instance based on mode

    Returns a retriever with a unified .retrieve() interface:
    - "hybrid": HybridRetriever (FTS + vector fusion)
    - "vector": Retriever wrapped in VectorRetrieverAdapter (vector-only)
    - "fts": FTSOnlyRetriever (FTS-only, no embeddings)

    Mode resolution precedence:
    1. Explicit mode argument
    2. BARTHO_RETRIEVAL_MODE env var
    3. kernel.yaml retrieval.mode
    4. Default: "hybrid"

    DB path resolution precedence:
    1. Explicit db_path argument
    2. BARTHO_DB_PATH env var
    3. kernel.yaml memory.db_path
    4. Default: "data/barth.db"

    Args:
        mode: Retrieval mode ("hybrid", "vector", or "fts")
        db_path: Database path (resolved from config/env if None)
        rules_engine: Memory rules engine (uses singleton if None)
        embedding_engine: Embedding engine (uses singleton if None, not used for FTS mode)
        memory_store: Memory store instance (optional)

    Returns:
        Retriever instance with .retrieve(query, top_k, filters) method

    Raises:
        ValueError: If mode is invalid

    Examples:
        # Default (from config/env)
        retriever = get_retriever()
        results = retriever.retrieve("privacy concerns", top_k=10)

        # Force vector-only
        retriever = get_retriever(mode="vector")
        results = retriever.retrieve("travel plans")

        # FTS-only with filters
        retriever = get_retriever(mode="fts")
        results = retriever.retrieve(
            "machine learning",
            filters=RetrievalFilters(kinds=["event"])
        )
    """
    # Track if mode was explicitly provided (for degradation logic)
    mode_explicit = mode is not None

    # Resolve configuration
    resolved_mode = _resolve_mode(mode)
    resolved_db_path = _resolve_db_path(db_path)

    # Resolve rules engine (use module singleton if not provided)
    if rules_engine is None:
        try:
            from bartholomew.kernel.memory_rules import _rules_engine

            rules_engine = _rules_engine
        except ImportError:
            logger.warning(
                "Failed to import _rules_engine, creating new MemoryRulesEngine instance",
            )
            rules_engine = MemoryRulesEngine()

    # Check FTS5 availability for *this* database (conclusive answers cached)
    fts_probe = check_fts5(resolved_db_path)
    fts_ok = fts_probe.available

    # Degrade mode if FTS5 is unavailable, but ONLY if mode was NOT explicit
    # When user explicitly requests a mode, honor it and let the retriever
    # handle fallbacks internally (FTSOnlyRetriever has graceful fallbacks).
    #
    # R-RETRIEVAL-1: degradation requires *proof* of absence, not merely a
    # probe that failed. Dropping the lexical arm on an unproven absence is
    # the expensive mistake -- in this repository's default environment the
    # degraded vector mode then raises EmbedderUnavailableError and the caller
    # gets no retriever at all -- so an inconclusive probe keeps the
    # configured mode and is re-probed next time instead.
    if resolved_mode == "fts" and not fts_ok and not mode_explicit:
        if fts_probe.status is FTS5Status.ABSENT:
            logger.warning(
                "FTS mode from config/env but FTS5 is absent for %s (%s); "
                "degrading to vector-only",
                resolved_db_path,
                fts_probe.detail,
            )
            resolved_mode = "vector"
        else:
            logger.warning(
                "FTS mode from config/env and the FTS5 probe for %s did not "
                "conclude (%s); keeping lexical retrieval rather than degrading "
                "on an unproven absence. The probe will be retried.",
                resolved_db_path,
                fts_probe.detail,
            )
    elif resolved_mode == "hybrid" and not fts_ok:
        logger.info(
            "Hybrid mode with FTS5 %s for %s; "
            "will operate with vector-only (empty FTS candidates)",
            "absent" if fts_probe.status is FTS5Status.ABSENT else "of unknown availability",
            resolved_db_path,
        )

    # Vector-bearing modes need an embedder that actually works. When one is
    # not available, hybrid degrades to FTS rather than failing the whole
    # retrieval path -- lexical retrieval is genuinely useful and genuinely
    # honest. The degradation is logged and is visible through
    # `describe_retrieval()`; it is never silent.
    if resolved_mode in ("hybrid", "vector"):
        engine, embedding_status = resolve_embedding_engine()
        if engine is None:
            if resolved_mode == "hybrid":
                logger.warning(
                    "Hybrid retrieval requested but semantic embeddings are not "
                    "operational (%s: %s); serving FTS-only. Vector similarity is "
                    "NOT contributing to these results.",
                    embedding_status.mode.value,
                    embedding_status.reason,
                )
                resolved_mode = "fts"
            else:
                # An explicit vector-only request cannot be honoured by
                # quietly returning lexical results under a vector label.
                raise EmbedderUnavailableError(
                    "Vector-only retrieval was requested but semantic embeddings "
                    f"are not operational ({embedding_status.mode.value}): "
                    f"{embedding_status.reason}",
                )
        elif embedding_engine is None:
            embedding_engine = engine

    logger.debug(f"Creating retriever: mode={resolved_mode}, db_path={resolved_db_path}")

    # Route to appropriate retriever
    if resolved_mode == "hybrid":
        # Import here to avoid circular dependencies
        from bartholomew.kernel.hybrid_retriever import HybridRetriever

        return HybridRetriever(
            db_path=resolved_db_path,
            rules_engine=rules_engine,
            embedding_engine=embedding_engine,
            memory_store=memory_store,
        )

    elif resolved_mode == "vector":
        # Create vector store
        vector_store = VectorStore(resolved_db_path)

        # Create vector-only retriever
        retriever = Retriever(
            rules_engine=rules_engine,
            vector_store=vector_store,
            embedding_engine=embedding_engine,
            memory_store=memory_store,
        )

        # Wrap in adapter for consistent .retrieve() interface
        return VectorRetrieverAdapter(retriever)

    elif resolved_mode == "fts":
        return FTSOnlyRetriever(
            db_path=resolved_db_path,
            rules_engine=rules_engine,
            memory_store=memory_store,
        )

    else:
        # Should never reach here due to validation in _resolve_mode
        raise ValueError(f"Invalid mode: {resolved_mode}")
