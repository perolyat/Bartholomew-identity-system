"""
S8: cross-user isolation, tested at the persistence layer.

The approved Alpha model isolates each personal Bartholomew by **process and
file boundary**, not by a `WHERE user_id = ?` predicate. These tests assert
that boundary where it actually lives -- on disk -- rather than only through
HTTP, because an HTTP-only test would pass just as happily against a shared
database that merely filters well today.
"""

from __future__ import annotations

import tempfile
import uuid

import pytest

from bartholomew.platform.principal import (  # noqa: E402
    AuthenticationError,
    Principal,
    PrincipalKind,
)
from bartholomew.platform.runtime_registry import (  # noqa: E402
    RuntimeResolutionError,
    assert_runtime_matches,
    runtime_handle_for,
    runtime_handle_for_user_id,
)


@pytest.fixture(scope="module", autouse=True)
def _isolated_environment():
    """
    Set this module's environment for its own duration and restore it after.

    Module-level `os.environ[...]` assignment would leak `BARTH_AUTH_MODE` and
    the database paths into every other test file in the same pytest session
    -- silently enforcing authentication on suites written before it existed,
    and pointing their kernels at this module's database. A module-scoped
    MonkeyPatch keeps the change contained to this file.
    """
    mp = pytest.MonkeyPatch()
    tmp = tempfile.mkdtemp(prefix="s8-iso-")
    for var, value in {
        "BARTH_PLATFORM_DB_PATH": "<tmp>/platform.db",
        "BARTH_DATA_ROOT": "<tmp>/data",
    }.items():
        mp.setenv(var, value.replace("<tmp>", tmp))
    yield
    mp.undo()


def _user(name="u"):
    return Principal(str(uuid.uuid4()), name, PrincipalKind.USER, "sess")


# ---------------------------------------------------------------------------
# T2 -- two users never share a persistence surface
# ---------------------------------------------------------------------------


def test_two_users_get_disjoint_persistence_surfaces():
    """
    T2. Every addressable surface differs: database, data directory, vector
    directory, log directory and keyring namespace.
    """
    a, b = runtime_handle_for(_user("alice")), runtime_handle_for(_user("bob"))
    assert a.db_path != b.db_path
    assert a.data_dir != b.data_dir
    assert a.vector_dir != b.vector_dir
    assert a.log_dir != b.log_dir
    assert a.keyring_service != b.keyring_service
    # Neither directory contains the other, so a traversal from inside one
    # cannot reach the other by relative path.
    assert not str(a.data_dir).startswith(str(b.data_dir))
    assert not str(b.data_dir).startswith(str(a.data_dir))


def test_data_written_as_one_user_is_invisible_to_the_other():
    """
    T2/T6. The real test: write a memory through the ordinary MemoryStore
    under user A's runtime, then read every memory under user B's. B must see
    nothing of A's -- asserted against the database, not an API response.
    """
    import asyncio

    from bartholomew.kernel.memory_store import MemoryStore

    a, b = runtime_handle_for(_user("alice")), runtime_handle_for(_user("bob"))
    ts = "2026-08-27T00:00:00Z"

    async def _run():
        store_a, store_b = MemoryStore(a.db_path), MemoryStore(b.db_path)
        await store_a.init()
        await store_b.init()
        await store_a.upsert_memory("fact", "salary", "alice earns a specific amount", ts)
        await store_b.upsert_memory("fact", "salary", "bob earns a different amount", ts)
        return (
            await store_b.list_memories(limit=100),
            await store_a.list_memories(limit=100),
        )

    b_rows, a_rows = asyncio.run(_run())
    assert "alice earns" not in str(b_rows)
    assert "bob earns" not in str(a_rows)


def test_the_globally_unique_memory_key_index_does_not_collide_across_users():
    """
    T2. `uq_memories_kind_key` is unique on `(kind, key)` with no owner
    column -- the seam `DECISIONS.md` flagged. Under per-user databases it is
    unique *per user*, so two users may both hold `fact/salary` without one
    overwriting the other. This test is what would fail loudly if anyone
    later merged these databases into one without adding an owner column.
    """
    import asyncio

    from bartholomew.kernel.memory_store import MemoryStore

    a, b = runtime_handle_for(_user("alice")), runtime_handle_for(_user("bob"))
    ts = "2026-08-27T00:00:00Z"

    async def _run():
        store_a, store_b = MemoryStore(a.db_path), MemoryStore(b.db_path)
        await store_a.init()
        await store_b.init()
        await store_a.upsert_memory("fact", "shared-key", "value-a", ts)
        await store_b.upsert_memory("fact", "shared-key", "value-b", ts)
        return (
            await store_a.get_memory("fact", "shared-key"),
            await store_b.get_memory("fact", "shared-key"),
        )

    got_a, got_b = asyncio.run(_run())
    assert got_a["value"] == "value-a"
    assert got_b["value"] == "value-b"


def test_governance_state_is_per_user():
    """
    T7. One participant's Personal brake must never halt or alter another's
    -- the canonical requirement that personal brakes are independent.
    """
    from bartholomew.orchestrator.safety.governance_store import GovernanceStore

    a, b = runtime_handle_for(_user("alice")), runtime_handle_for(_user("bob"))
    GovernanceStore(a.db_path).engage("global", reason="alice halts her own", actor="alice")
    assert GovernanceStore(a.db_path).refresh().engaged is True
    assert GovernanceStore(b.db_path).refresh().engaged is False


# ---------------------------------------------------------------------------
# T11/T12 -- resolution cannot be steered by anything a client controls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "../bob",
        "../../etc",
        "..",
        ".",
        "",
        "/absolute",
        "a/b",
        "a\\b",
        "%2e%2e",
        "\x00",
        "not-a-uuid",
        "00000000-0000-0000-0000-00000000000",  # one char short
    ],
)
def test_hostile_user_ids_cannot_escape_their_directory(hostile):
    """
    T11. Path traversal in a `user_id` would defeat every other isolation
    property at once, so the identifier shape is validated rather than
    trusted.
    """
    with pytest.raises(RuntimeResolutionError):
        runtime_handle_for_user_id(hostile)


def test_a_platform_admin_has_no_personal_runtime():
    """
    T10. Administration is not a personal Bartholomew. An admin principal is
    refused a runtime rather than silently handed one.
    """
    admin = Principal(str(uuid.uuid4()), "ops", PrincipalKind.PLATFORM_ADMIN, "s")
    with pytest.raises(RuntimeResolutionError):
        runtime_handle_for(admin)


def test_a_runtime_mismatch_fails_closed_rather_than_serving_the_wrong_user():
    """
    T2. If the running kernel is not the authenticated principal's, the
    request is refused. Serving whatever runtime happens to be loaded is the
    exact cross-user disclosure this check exists to prevent.
    """
    a, b = runtime_handle_for(_user("alice")), runtime_handle_for(_user("bob"))
    assert_runtime_matches(a, a.db_path)  # the matching case is fine
    with pytest.raises(AuthenticationError):
        assert_runtime_matches(a, b.db_path)
    with pytest.raises(AuthenticationError):
        assert_runtime_matches(a, None)


def test_runtime_resolution_takes_a_principal_not_a_string():
    """
    T11. A structural guard: the HTTP-facing resolver's parameter is a
    `Principal`, which can only be built from a verified session. If someone
    later widens this to accept a bare user id from request data, this test
    is the tripwire.
    """
    import inspect

    sig = inspect.signature(runtime_handle_for)
    annotation = sig.parameters["principal"].annotation
    assert annotation in (
        Principal,
        "Principal",
    ), "runtime_handle_for must take a verified Principal, never a raw identifier"


# ---------------------------------------------------------------------------
# T2 -- a process dedicated to one user refuses every other identity
# ---------------------------------------------------------------------------


def test_a_bound_process_refuses_a_different_users_identity(monkeypatch):
    """
    T2. This is what makes "one runtime per process" an enforced boundary
    rather than an intention. Alice's process must refuse Bob even though Bob
    authenticated perfectly well.
    """
    from bartholomew.platform.principal import AuthorizationError
    from bartholomew.platform.runtime_registry import (
        RUNTIME_USER_ID_ENV,
        assert_principal_owns_this_process,
    )

    alice, bob = _user("alice"), _user("bob")
    monkeypatch.setenv(RUNTIME_USER_ID_ENV, alice.user_id)

    assert_principal_owns_this_process(alice)  # the owner is fine
    with pytest.raises(AuthorizationError):
        assert_principal_owns_this_process(bob)


def test_a_bound_process_refuses_platform_administrators(monkeypatch):
    """
    T10. A process serving one person's Bartholomew is not an administrative
    surface, so an admin principal is refused there too.
    """
    from bartholomew.platform.principal import AuthorizationError
    from bartholomew.platform.runtime_registry import (
        RUNTIME_USER_ID_ENV,
        assert_principal_owns_this_process,
    )

    alice = _user("alice")
    ops = Principal(str(uuid.uuid4()), "ops", PrincipalKind.PLATFORM_ADMIN, "s")
    monkeypatch.setenv(RUNTIME_USER_ID_ENV, alice.user_id)
    with pytest.raises(AuthorizationError):
        assert_principal_owns_this_process(ops)


def test_an_unbound_process_does_not_silently_claim_an_identity(monkeypatch):
    """
    The unbound case is a no-op, not a fallback. It must not invent a binding
    or accept one from anywhere -- an unbound process is simply not making the
    ownership claim, and the multi-runtime front door is what sets it.
    """
    from bartholomew.platform.runtime_registry import (
        RUNTIME_USER_ID_ENV,
        assert_principal_owns_this_process,
        bound_runtime_user_id,
    )

    monkeypatch.delenv(RUNTIME_USER_ID_ENV, raising=False)
    assert bound_runtime_user_id() is None
    assert_principal_owns_this_process(_user("anyone"))
