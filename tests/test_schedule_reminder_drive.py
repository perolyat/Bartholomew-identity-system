"""
Usable POC slice 2 -- proactive schedule reminders, end to end.

This is the slice's acceptance suite. Per
`docs/POC_SLICE_2_PROACTIVE_REMINDERS.md` Sec.5, the bar is:

    a date-bearing fact stated in conversation days earlier produces,
    unprompted, exactly one governed reminder before it falls due -- visible
    in the nudge queue *and* delivered through the real webhook outside the
    browser -- while quiet hours defer it, mute defers it, an engaged brake
    prevents it entirely, and a fresh start with the feature off (or nothing
    due) produces zero proactive behaviour of any kind.

`TestAcceptanceBar` demonstrates that with a real `MemoryStore`, a real
`SkillRegistry` running the real `NotifySkill`, a real SQLite queue and a
real loopback HTTP server -- the same "not a mock" posture slice 1's
acceptance test took, for the same reason: a mocked delivery would prove only
that the code called itself, and the visible real-world result *is* the
delivery.

The remaining classes protect the properties that make the demonstration mean
something, including the four points Taylor approved on 2026-08-25:

  A1  the governed delivery is the slice's action (no second approval step)
  A2  one reminder per (fact, due date); acking does not re-arm it
  A3  consent is a `config/kernel.yaml` flag, default OFF, and OFF means the
      drive is not registered at all
  A4  a delivery failure is recorded truthfully on the nudge, is never
      reported as success, is not retried, and never raises into the loop
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bartholomew.kernel import schedule_noticing as sn
from bartholomew.kernel.memory_store import MemoryStore
from bartholomew.kernel.scheduler import containment
from bartholomew.kernel.scheduler import drives as drives_module
from bartholomew.kernel.scheduler import persistence as sp
from bartholomew.kernel.scheduler.drives import (
    SCHEDULE_REMINDER_DRIVE,
    drive_schedule_reminder_check,
    resolve_registry,
)
from bartholomew.kernel.scheduler.loop import _run_drive, resolve_cadences
from bartholomew.kernel.scheduler.store import SchedulerStore
from bartholomew.kernel.skill_registry import SkillRegistry
from bartholomew.orchestrator.safety.governance_store import GovernanceStore
from bartholomew.skills.notify import WEBHOOK_URL_ENV
from identity_interpreter.identity_context import IdentityContext

# Identity.yaml's real allowlist, as amended by this slice. The drive task_id
# and "notify" both have to be present for a reminder to reach anyone.
REAL_ALLOW_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=[
        "web_fetch",
        "browser_action",
        "notify",
        "awaiting_response_check",
        SCHEDULE_REMINDER_DRIVE,
    ],
)
# The shape Identity.yaml had BEFORE this slice: the drive is not allowlisted.
PRE_SLICE_DENY_CONTEXT = IdentityContext(
    tool_use_default_allowed=False,
    tool_use_allowlist=["web_fetch", "browser_action", "notify"],
)


# ---------------------------------------------------------------------------
# A real loopback endpoint. Delivery is proven by an HTTP request arriving,
# never by a mock reporting that it was called.
# ---------------------------------------------------------------------------
class _Recorder(BaseHTTPRequestHandler):
    received: list[dict] = []
    status = 200

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's required name
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8")
        type(self).received.append({"path": self.path, "body": body})
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


@pytest.fixture
def webhook_server():
    _Recorder.received = []
    _Recorder.status = 200
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, _Recorder
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _url(server) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}/reminders"


class _Ctx:
    """A scheduler context with exactly the attributes the drive reads.

    Deliberately duck-typed rather than a KernelDaemon: the drive's own
    docstring commits to working with the minimal context scheduler tests use,
    and a full daemon would drag its background loops into every case here.
    """

    def __init__(self, mem, scheduler_store, skill_registry=None, cfg=None, identity=None):
        self.mem = mem
        self.scheduler_store = scheduler_store
        self.skill_registry = skill_registry
        self.cfg = cfg if cfg is not None else {"proactive": {"schedule_reminders": True}}
        self.tz = timezone.utc
        self.identity_context = identity
        self.blocking_executor = None


@pytest.fixture
async def mem(tmp_path):
    store = MemoryStore(str(tmp_path / "slice2.db"))
    await store.init()
    yield store
    await store.close()


@pytest.fixture
async def scheduler_store(mem):
    sp.ensure_schema(mem.db_path)
    store = SchedulerStore(mem.db_path)
    yield store
    await store.close()


async def _registry(mem, monkeypatch, webhook_url: str | None, identity=REAL_ALLOW_CONTEXT):
    if webhook_url is None:
        monkeypatch.delenv(WEBHOOK_URL_ENV, raising=False)
    else:
        monkeypatch.setenv(WEBHOOK_URL_ENV, webhook_url)
    registry = SkillRegistry(
        db_path=mem.db_path,
        memory_store=mem,
        identity_context=identity,
    )
    await registry.load_skill("notify")

    # Pin the suppression state, because it is otherwise read from the wall
    # clock. `NotifySkill`'s default quiet hours are 22:00-07:00 local
    # (S1.3), and during them a send is correctly *deferred* rather than
    # delivered -- so every test below that asserts a delivery actually
    # happened silently depended on what time of day it ran. This suite went
    # green at 18:29 UTC and red at 23:08 UTC on byte-identical code before
    # this was pinned; the production behaviour was right both times.
    #
    # An empty window (start == end) is never active, so this exercises the
    # real `_is_quiet_hours()` predicate rather than replacing it -- and the
    # assertion below makes that self-verifying instead of assumed. Tests
    # that are *about* deferral override these per-instance afterwards.
    notify = registry._loaded["notify"].instance
    notify._quiet_hours_start = "00:00"
    notify._quiet_hours_end = "00:00"
    notify._muted = False
    notify._muted_until = None
    assert not notify._is_quiet_hours(), "quiet hours were not successfully pinned off"
    assert not notify._is_muted(), "mute was not successfully pinned off"

    return registry


async def _store_schedule_fact(mem, key: str, text: str) -> None:
    """Put a fact into memory the way slice 1 does -- through the one governed
    write path, never by inserting a row behind it."""
    result = await mem.upsert_memory(
        "user_schedule",
        key,
        text,
        datetime.now(timezone.utc).isoformat(),
    )
    assert result.stored, f"governed write refused {key!r}: the test's premise is wrong"


def _backdate_fact(db_path: str, key: str, days: int) -> None:
    """Move a stored fact's capture timestamp into the past.

    Written directly rather than through `upsert_memory()` because there is no
    governed way to store a fact *as of* an earlier moment -- and the point of
    the test is what the drive does with a row whose anchor has aged, not how
    the row got there.
    """
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE memories SET ts = ? WHERE kind = 'user_schedule' AND key = ?",
            (stamp, key),
        )
        conn.commit()
    finally:
        conn.close()


def _due_in(days: int) -> str:
    """A fact whose text names an absolute date `days` from today, in the
    shape slice 1's extractor produces."""
    due = date.today() + timedelta(days=days)
    return f"Car rego: on {sn.format_due_date(due)}"


def _nudges(db_path: str, kind: str | None = None) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if kind is None:
            return conn.execute("SELECT * FROM nudges ORDER BY id").fetchall()
        return conn.execute(
            "SELECT * FROM nudges WHERE kind = ? ORDER BY id",
            (kind,),
        ).fetchall()
    finally:
        conn.close()


def _reminders(db_path: str) -> list[sqlite3.Row]:
    return _nudges(db_path, sn.REMINDER_KIND)


# ---------------------------------------------------------------------------
# 1. The acceptance bar itself.
# ---------------------------------------------------------------------------
class TestAcceptanceBar:
    async def test_a_fact_stated_earlier_produces_one_delivered_reminder(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """The whole slice: a date-bearing fact the user stated days ago, a
        drive firing on its own cadence, one nudge in the queue, and a real
        HTTP delivery leaving the machine -- with nobody asking for any of it."""
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(2))
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, scheduler_store, registry, identity=REAL_ALLOW_CONTEXT)

        await drive_schedule_reminder_check(ctx)

        rows = _reminders(mem.db_path)
        assert len(rows) == 1, "expected exactly one reminder in the queue"
        assert rows[0]["status"] == "pending"
        assert rows[0]["reason"] == sn.REMINDER_REASON
        assert "Car rego" in rows[0]["message"]

        assert len(recorder.received) == 1, "no outbound HTTP request reached the endpoint"
        assert recorder.received[0]["path"] == "/reminders"
        assert "Car rego" in recorder.received[0]["body"]

        # And the queue says, in its own row, that the delivery genuinely
        # happened -- not merely that a send was attempted.
        delivery = await scheduler_store.get_nudge_delivery(rows[0]["id"])
        assert delivery["delivery_status"] == sp.DELIVERY_DELIVERED
        assert delivery["delivery_detail"] is None

    async def test_nothing_due_produces_no_proactive_behaviour(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(60))
        registry = await _registry(mem, monkeypatch, _url(server))

        await drive_schedule_reminder_check(_Ctx(mem, scheduler_store, registry))

        assert _reminders(mem.db_path) == []
        assert recorder.received == []

    async def test_an_unparseable_fact_produces_no_reminder(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """The drive would rather say nothing than guess a date.

        `on Friday` was this test's example until relative forms were
        implemented (2026-08-27); it is now resolvable against the row's
        capture date and has its own test below. The examples here are the
        ones that remain genuinely unresolvable: a fact with no "when" at all,
        and `next Friday`, whose meaning varies between speakers and is
        refused rather than approximated.
        """
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "dentist", "Dentist appointment: sometime soon")
        await _store_schedule_fact(mem, "vet", "Vet visit: next Friday")
        registry = await _registry(mem, monkeypatch, _url(server))

        await drive_schedule_reminder_check(_Ctx(mem, scheduler_store, registry))

        assert _reminders(mem.db_path) == []
        assert recorder.received == []

    async def test_a_relatively_dated_fact_produces_a_reminder(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """The end-to-end half of the 2026-08-27 relative-form change.

        `_store_schedule_fact()` writes through the real governed path, so the
        row carries a real capture timestamp -- meaning this exercises the
        actual anchor the drive reads, not a hand-placed one. The fact is
        stored today and says "tomorrow", so it falls one day out and inside
        the default look-ahead window whenever the test runs.
        """
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "dentist", "Dentist appointment: tomorrow")
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, scheduler_store, registry, identity=REAL_ALLOW_CONTEXT)

        await drive_schedule_reminder_check(ctx)

        rows = _reminders(mem.db_path)
        assert len(rows) == 1, "expected exactly one reminder in the queue"
        expected = sn.format_due_date(date.today() + timedelta(days=1))
        assert expected in rows[0]["message"]
        assert len(recorder.received) == 1

    async def test_a_stale_relative_fact_is_not_dragged_forward(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """The safety property the capture anchor exists for.

        A fact stored long ago that says "tomorrow" names a date that has
        gone. Anchored to notice-time today it would fire every single day,
        forever, for a commitment the user never made. Anchored to capture it
        goes quiet, which is the whole reason the anchor is the capture date.
        """
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "dentist", "Dentist appointment: tomorrow")
        _backdate_fact(mem.db_path, "dentist", days=30)
        registry = await _registry(mem, monkeypatch, _url(server))

        await drive_schedule_reminder_check(_Ctx(mem, scheduler_store, registry))

        assert _reminders(mem.db_path) == []
        assert recorder.received == []


# ---------------------------------------------------------------------------
# 2. A3 -- consent: default OFF means not registered at all.
# ---------------------------------------------------------------------------
class TestDefaultOff:
    def test_the_shipped_config_has_the_feature_off(self):
        """The repository's own `config/kernel.yaml`, read as the daemon reads
        it -- not a fixture asserting about itself."""
        import yaml

        cfg = yaml.safe_load(open("config/kernel.yaml", encoding="utf-8"))
        assert cfg["proactive"]["schedule_reminders"] is False

    def test_the_drive_is_absent_from_the_registry_when_off(self):
        class _Off:
            cfg = {"proactive": {"schedule_reminders": False}}

        assert SCHEDULE_REMINDER_DRIVE not in resolve_registry(_Off())

    def test_a_context_with_no_proactive_block_at_all_is_off(self):
        """An existing deployment that has never heard of this flag."""

        class _Legacy:
            cfg = {"timezone": "Australia/Brisbane"}

        assert SCHEDULE_REMINDER_DRIVE not in resolve_registry(_Legacy())

        class _NoCfg:
            pass

        assert SCHEDULE_REMINDER_DRIVE not in resolve_registry(_NoCfg())

    def test_the_drive_is_present_only_when_explicitly_turned_on(self):
        class _On:
            cfg = {"proactive": {"schedule_reminders": True}}

        registry = resolve_registry(_On())
        assert SCHEDULE_REMINDER_DRIVE in registry
        assert registry[SCHEDULE_REMINDER_DRIVE]["fn"] is drive_schedule_reminder_check

    def test_turning_it_off_does_not_disturb_the_always_on_drives(self):
        class _Off:
            cfg = {"proactive": {"schedule_reminders": False}}

        assert set(resolve_registry(_Off())) == set(drives_module.REGISTRY)

    def test_no_cadence_is_scheduled_for_it_when_off(self):
        """Registration, not merely cadence: a drive that is off must never
        reach `scheduled_tasks` at all."""

        class _Off:
            cfg = {"proactive": {"schedule_reminders": False}}

        assert SCHEDULE_REMINDER_DRIVE not in resolve_cadences(_Off())

        class _On:
            cfg = {"proactive": {"schedule_reminders": True}}

        assert resolve_cadences(_On())[SCHEDULE_REMINDER_DRIVE] == "every:3600"

    def test_the_drives_cadence_block_is_not_a_registration_list(self):
        """A5's factual correction, pinned: `config/kernel.yaml`'s `drives:`
        block overrides cadences and registers nothing, so omitting a drive
        from it disables nothing."""

        class _OnButOmittedFromDrivesBlock:
            cfg = {
                "proactive": {"schedule_reminders": True},
                "drives": {"self_check": "every:900"},
            }

        assert SCHEDULE_REMINDER_DRIVE in resolve_registry(_OnButOmittedFromDrivesBlock())

    async def test_the_shipped_default_yields_no_reminders_even_with_facts_due(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """Belt and braces: even if something did call the drive directly with
        the feature off, the queue and the endpoint stay untouched because the
        drive is never reached through the loop. Here we prove the loop path."""
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(
            mem,
            scheduler_store,
            registry,
            cfg={"proactive": {"schedule_reminders": False}},
        )

        assert SCHEDULE_REMINDER_DRIVE not in resolve_registry(ctx)
        assert _reminders(mem.db_path) == []
        assert recorder.received == []


# ---------------------------------------------------------------------------
# 3. A2 -- one reminder per (fact, due date), and acking does not re-arm it.
# ---------------------------------------------------------------------------
class TestNotNagging:
    async def test_repeated_firings_leave_exactly_one_unresolved_reminder(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(2))
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, scheduler_store, registry)

        for _ in range(5):
            await drive_schedule_reminder_check(ctx)

        assert len(_reminders(mem.db_path)) == 1
        assert len(recorder.received) == 1, "the user was notified more than once"

    async def test_acking_a_reminder_does_not_re_arm_it(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """Sec.4's after-ack rule: the partial UNIQUE index frees the key when
        a nudge is resolved, so without the courtesy check the very next tick
        would re-raise a reminder the user had just dealt with."""
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(2))
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, scheduler_store, registry)

        await drive_schedule_reminder_check(ctx)
        rows = _reminders(mem.db_path)
        assert len(rows) == 1

        await mem.set_nudge_status(rows[0]["id"], "acked")
        await drive_schedule_reminder_check(ctx)

        assert len(_reminders(mem.db_path)) == 1, "an acked reminder was raised again"
        assert len(recorder.received) == 1

    async def test_the_acked_row_is_never_deleted_or_rewritten(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """Containment bounds the queue; it never sheds an obligation."""
        server, _recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(2))
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, scheduler_store, registry)

        await drive_schedule_reminder_check(ctx)
        original = dict(_reminders(mem.db_path)[0])
        await mem.set_nudge_status(original["id"], "acked")
        await drive_schedule_reminder_check(ctx)

        surviving = dict(_reminders(mem.db_path)[0])
        assert surviving["id"] == original["id"]
        assert surviving["message"] == original["message"]
        assert surviving["status"] == "acked"

    async def test_two_different_facts_due_the_same_day_are_two_reminders(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """Containment must not merge two genuine commitments."""
        server, recorder = webhook_server
        due = _due_in(2).replace("Car rego", "{}")
        await _store_schedule_fact(mem, "car_rego", due.format("Car rego"))
        await _store_schedule_fact(mem, "dentist", due.format("Dentist"))
        registry = await _registry(mem, monkeypatch, _url(server))

        await drive_schedule_reminder_check(_Ctx(mem, scheduler_store, registry))

        assert len(_reminders(mem.db_path)) == 2
        assert len(recorder.received) == 2

    async def test_restating_the_same_fact_does_not_create_a_second_reminder(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """The identity is (fact, due date), not the message text."""
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(2))
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, scheduler_store, registry)
        await drive_schedule_reminder_check(ctx)

        # Same key, same date, reworded -- `upsert_memory()` updates in place.
        await _store_schedule_fact(mem, "car_rego", _due_in(2).replace("Car rego", "Rego renewal"))
        await drive_schedule_reminder_check(ctx)

        assert len(_reminders(mem.db_path)) == 1
        assert len(recorder.received) == 1

    async def test_the_after_ack_check_and_the_stored_key_agree(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """The key the drive looks up must be the key the insert wrote.

        Both are computed by `containment`, not formatted by hand at either
        site -- so a later change to the key format (it has already grown an
        escalation suffix once) cannot leave the after-ack check silently
        failing to match, which would show up as re-reminding the user about
        things they had already dealt with.
        """
        server, _recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(2))
        registry = await _registry(mem, monkeypatch, _url(server))

        await drive_schedule_reminder_check(_Ctx(mem, scheduler_store, registry))

        row = _reminders(mem.db_path)[0]
        assert row["dedup_key"], "the reminder was persisted unkeyed"
        assert await scheduler_store.nudge_exists_for_dedup_key(row["dedup_key"]) is True

    async def test_the_reminder_reason_is_containment_eligible(self):
        assert containment.is_policy_eligible(sn.REMINDER_REASON)
        assert sn.REMINDER_REASON in containment.eligible_reasons()

    async def test_no_nudge_recursion_the_reminder_queue_does_not_feed_itself(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """B-F001's shape, checked for this drive: reminders must be bounded by
        the number of distinct due facts, not by how often the drive runs."""
        server, _recorder = webhook_server
        for n in range(3):
            await _store_schedule_fact(mem, f"item{n}", _due_in(1).replace("Car rego", f"Item {n}"))
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, scheduler_store, registry)

        for _ in range(10):
            await drive_schedule_reminder_check(ctx)

        assert len(_reminders(mem.db_path)) == 3


# ---------------------------------------------------------------------------
# 4. Governance: parking brake at both gates, Identity policy, quiet hours.
# ---------------------------------------------------------------------------
class TestGovernance:
    async def test_an_engaged_scheduler_brake_stops_the_drive_before_it_runs(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """Gate one: `run_drive_through_runtime_contract()`'s own brake check,
        reached through the loop's real `_run_drive()`.

        The brake *raises* here rather than returning a denial -- an
        operator-engaged emergency stop, not a routine policy decision -- which
        is the pre-existing contract `run_scheduler()` and the parking-brake
        integration tests both depend on. What matters for this slice is that
        the drive body never runs: no scan, no queue row, no outbound request.
        """
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, scheduler_store, registry, identity=REAL_ALLOW_CONTEXT)

        GovernanceStore(mem.db_path).engage("scheduler", reason="slice 2 test", actor="test")

        with pytest.raises(RuntimeError, match="ParkingBrake"):
            await _run_drive(ctx, SCHEDULE_REMINDER_DRIVE, drive_schedule_reminder_check)

        assert _reminders(mem.db_path) == []
        assert recorder.received == []

    async def test_an_engaged_skills_brake_stops_the_delivery(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """Gate two, independent of gate one: `execute_action()`'s `skills`
        scope. Nothing reaches the endpoint, and the queue says so."""
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, scheduler_store, registry)

        GovernanceStore(mem.db_path).engage("skills", reason="slice 2 test", actor="test")

        await drive_schedule_reminder_check(ctx)

        assert recorder.received == [], "a blocked skills scope still delivered"
        rows = _reminders(mem.db_path)
        assert len(rows) == 1, "the obligation must still be represented"
        delivery = await scheduler_store.get_nudge_delivery(rows[0]["id"])
        assert delivery["delivery_status"] == sp.DELIVERY_FAILED
        assert "brake" in (delivery["delivery_detail"] or "").lower()

    async def test_identity_policy_denies_the_drive_without_its_allowlist_entry(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """The drive is deliberately not self-maintenance-exempt: with the
        pre-slice Identity.yaml it is denied outright."""
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, _url(server))
        ctx = _Ctx(mem, scheduler_store, registry, identity=PRE_SLICE_DENY_CONTEXT)

        nudge, success = await _run_drive(
            ctx,
            SCHEDULE_REMINDER_DRIVE,
            drive_schedule_reminder_check,
        )

        assert success == 0
        assert _reminders(mem.db_path) == []
        assert recorder.received == []

    async def test_the_drive_is_not_exempt_from_the_identity_policy_check(self):
        from bartholomew.kernel.runtime_contract import _SELF_MAINTENANCE_DRIVES

        assert SCHEDULE_REMINDER_DRIVE not in _SELF_MAINTENANCE_DRIVES

    async def test_the_shipped_identity_allowlists_the_drive(self):
        import yaml

        identity = yaml.safe_load(open("Identity.yaml", encoding="utf-8"))
        allowlist = identity["tool_use"]["allowlist"]
        assert SCHEDULE_REMINDER_DRIVE in allowlist
        assert "notify" in allowlist

    async def test_quiet_hours_defer_the_delivery_and_say_so(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """S1.3: NotifySkill queues rather than dropping. The obligation is
        intact, nothing reached the endpoint, and the record says `deferred` --
        not `delivered`."""
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, _url(server))
        notify = registry._loaded["notify"].instance
        monkeypatch.setattr(notify, "_is_quiet_hours", lambda: True)

        await drive_schedule_reminder_check(_Ctx(mem, scheduler_store, registry))

        assert recorder.received == [], "quiet hours were bypassed"
        rows = _reminders(mem.db_path)
        assert len(rows) == 1
        delivery = await scheduler_store.get_nudge_delivery(rows[0]["id"])
        assert delivery["delivery_status"] == sp.DELIVERY_DEFERRED

    async def test_mute_defers_the_delivery_and_says_so(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, _url(server))
        notify = registry._loaded["notify"].instance
        monkeypatch.setattr(notify, "_is_muted", lambda: True)

        await drive_schedule_reminder_check(_Ctx(mem, scheduler_store, registry))

        assert recorder.received == []
        rows = _reminders(mem.db_path)
        delivery = await scheduler_store.get_nudge_delivery(rows[0]["id"])
        assert delivery["delivery_status"] == sp.DELIVERY_DEFERRED

    async def test_a_deferred_reminder_is_not_re_sent_on_the_next_tick(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """Deferral is not loss, and it is also not a licence to nag: the
        queued notification is NotifySkill's to deliver when quiet hours end."""
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, _url(server))
        notify = registry._loaded["notify"].instance
        monkeypatch.setattr(notify, "_is_quiet_hours", lambda: True)
        ctx = _Ctx(mem, scheduler_store, registry)

        await drive_schedule_reminder_check(ctx)
        await drive_schedule_reminder_check(ctx)

        assert len(_reminders(mem.db_path)) == 1
        assert recorder.received == []


# ---------------------------------------------------------------------------
# 5. A4 -- delivery-failure semantics (approval point 8.4, option (b)).
# ---------------------------------------------------------------------------
class TestDeliveryFailureIsTruthful:
    async def test_an_unreachable_webhook_is_recorded_as_failed_not_delivered(
        self,
        mem,
        scheduler_store,
        monkeypatch,
    ):
        """The failure mode 8.4 exists to prevent: the nudge row stands (no
        obligation lost) but nothing tells anyone the reminder never left."""
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        # A port nothing is listening on.
        registry = await _registry(mem, monkeypatch, "http://127.0.0.1:9/reminders")

        await drive_schedule_reminder_check(_Ctx(mem, scheduler_store, registry))

        rows = _reminders(mem.db_path)
        assert len(rows) == 1, "the obligation must survive a failed delivery"
        delivery = await scheduler_store.get_nudge_delivery(rows[0]["id"])
        assert delivery["delivery_status"] == sp.DELIVERY_FAILED
        assert delivery["delivery_status"] != sp.DELIVERY_DELIVERED
        assert delivery["delivery_detail"]

    async def test_a_failing_endpoint_is_recorded_as_failed(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        server, recorder = webhook_server
        _Recorder.status = 500
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, _url(server))

        await drive_schedule_reminder_check(_Ctx(mem, scheduler_store, registry))

        assert len(recorder.received) == 1, "the POST should have been attempted"
        rows = _reminders(mem.db_path)
        delivery = await scheduler_store.get_nudge_delivery(rows[0]["id"])
        assert delivery["delivery_status"] == sp.DELIVERY_FAILED

    async def test_no_webhook_configured_is_local_only_not_delivered(
        self,
        mem,
        scheduler_store,
        monkeypatch,
    ):
        """Distinct from `failed`: nothing was attempted because nothing is
        configured. Also distinct from `delivered`: nothing left the machine."""
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, None)

        await drive_schedule_reminder_check(_Ctx(mem, scheduler_store, registry))

        rows = _reminders(mem.db_path)
        delivery = await scheduler_store.get_nudge_delivery(rows[0]["id"])
        assert delivery["delivery_status"] == sp.DELIVERY_SENT_LOCAL_ONLY
        assert delivery["delivery_status"] in sp.DELIVERY_STATUSES_NOT_DELIVERED

    async def test_a_failed_delivery_never_raises_into_the_scheduler_loop(
        self,
        mem,
        scheduler_store,
        monkeypatch,
    ):
        """The loop must survive an unreachable endpoint: a tick that could not
        deliver is still a successful tick, because the tick's job is noticing
        and recording, and both happened."""
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, "http://127.0.0.1:9/reminders")
        ctx = _Ctx(mem, scheduler_store, registry, identity=REAL_ALLOW_CONTEXT)

        nudge, success = await _run_drive(
            ctx,
            SCHEDULE_REMINDER_DRIVE,
            drive_schedule_reminder_check,
        )

        assert success == 1, "a delivery failure destabilised the tick"
        assert nudge is None

    async def test_a_failed_delivery_is_not_retried(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """Approval point 8.4 as decided: no retry-until-delivered in this
        sprint. The recorded failure is the remedy; an unbounded retry against
        a permanently unreachable endpoint is explicitly out of scope."""
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, "http://127.0.0.1:9/reminders")
        ctx = _Ctx(mem, scheduler_store, registry)

        await drive_schedule_reminder_check(ctx)
        # Endpoint comes back -- but the reminder is not re-attempted.
        registry._loaded["notify"].instance._webhook_url = _url(server)
        await drive_schedule_reminder_check(ctx)

        assert recorder.received == []
        rows = _reminders(mem.db_path)
        assert len(rows) == 1
        delivery = await scheduler_store.get_nudge_delivery(rows[0]["id"])
        assert delivery["delivery_status"] == sp.DELIVERY_FAILED

    async def test_no_skill_registry_is_recorded_as_not_attempted(
        self,
        mem,
        scheduler_store,
    ):
        await _store_schedule_fact(mem, "car_rego", _due_in(1))

        await drive_schedule_reminder_check(_Ctx(mem, scheduler_store, skill_registry=None))

        rows = _reminders(mem.db_path)
        delivery = await scheduler_store.get_nudge_delivery(rows[0]["id"])
        assert delivery["delivery_status"] == sp.DELIVERY_NOT_ATTEMPTED

    async def test_ordinary_nudges_carry_no_delivery_claim_at_all(
        self,
        mem,
        scheduler_store,
    ):
        """NULL is not a status. A curiosity nudge represents no delivery, and
        must not read as a failed one."""
        outcome = await scheduler_store.insert_nudge_contained(
            "curiosity",
            "How are you feeling right now?",
            [],
            "curiosity_probe",
            1_760_000_000,
        )
        delivery = await scheduler_store.get_nudge_delivery(outcome["nudge_id"])
        assert delivery["delivery_status"] is None

    async def test_a_lost_nudge_write_is_loud_not_silent(
        self,
        mem,
        scheduler_store,
        monkeypatch,
    ):
        """WP-A1 requirement E's posture, applied here: a queue write that did
        not happen is never treated as a duplicate or as disposable."""
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, None)
        ctx = _Ctx(mem, scheduler_store, registry)

        async def _boom(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(scheduler_store, "insert_nudge_contained", _boom)

        with pytest.raises(RuntimeError, match="could not durably record"):
            await drive_schedule_reminder_check(ctx)

    async def test_a_lost_nudge_write_marks_the_tick_a_failure(
        self,
        mem,
        scheduler_store,
        monkeypatch,
    ):
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, None)
        ctx = _Ctx(mem, scheduler_store, registry, identity=REAL_ALLOW_CONTEXT)

        async def _boom(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(scheduler_store, "insert_nudge_contained", _boom)

        _nudge, success = await _run_drive(
            ctx,
            SCHEDULE_REMINDER_DRIVE,
            drive_schedule_reminder_check,
        )
        assert success == 0


# ---------------------------------------------------------------------------
# 6. Privacy: the drive reads through the existing consent authority.
# ---------------------------------------------------------------------------
class TestPrivacyBoundary:
    async def test_a_consent_gated_fact_is_never_surfaced(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """A row the existing `ConsentGate` excludes cannot become an outbound
        notification -- the drive gates every row it reads through the same
        filter the retrieval layer uses."""
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, _url(server))

        from bartholomew.kernel import consent_gate as consent_gate_module

        real_filter = consent_gate_module.ConsentGate.filter_memory_ids

        def _exclude_everything(self, memory_ids, consented_ids=None):
            verdicts = real_filter(self, memory_ids, consented_ids)
            for verdict in verdicts.values():
                verdict["include"] = False
            return verdicts

        monkeypatch.setattr(
            consent_gate_module.ConsentGate,
            "filter_memory_ids",
            _exclude_everything,
        )

        await drive_schedule_reminder_check(_Ctx(mem, scheduler_store, registry))

        assert _reminders(mem.db_path) == []
        assert recorder.received == []

    async def test_a_context_only_fact_is_never_surfaced(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        """`recall_policy: context_only` means usable in context, not
        broadcastable to a device."""
        server, recorder = webhook_server
        await _store_schedule_fact(mem, "car_rego", _due_in(1))
        registry = await _registry(mem, monkeypatch, _url(server))

        from bartholomew.kernel import consent_gate as consent_gate_module

        real_filter = consent_gate_module.ConsentGate.filter_memory_ids

        def _mark_context_only(self, memory_ids, consented_ids=None):
            verdicts = real_filter(self, memory_ids, consented_ids)
            for verdict in verdicts.values():
                verdict["context_only"] = True
            return verdicts

        monkeypatch.setattr(
            consent_gate_module.ConsentGate,
            "filter_memory_ids",
            _mark_context_only,
        )

        await drive_schedule_reminder_check(_Ctx(mem, scheduler_store, registry))

        assert _reminders(mem.db_path) == []
        assert recorder.received == []

    async def test_non_date_bearing_profile_facts_are_never_read(
        self,
        mem,
        scheduler_store,
        monkeypatch,
        webhook_server,
    ):
        server, recorder = webhook_server
        await mem.upsert_memory(
            "user_profile",
            "doctor",
            f"Doctor: Dr Smith, review on {sn.format_due_date(date.today())}",
            datetime.now(timezone.utc).isoformat(),
        )
        registry = await _registry(mem, monkeypatch, _url(server))

        await drive_schedule_reminder_check(_Ctx(mem, scheduler_store, registry))

        assert _reminders(mem.db_path) == []
        assert recorder.received == []


# ---------------------------------------------------------------------------
# 7. No regression in what was already there.
# ---------------------------------------------------------------------------
class TestNoRegression:
    async def test_existing_containment_reasons_are_unchanged(self):
        assert {"curiosity_probe", "self_check_drift"} <= containment.eligible_reasons()

    async def test_an_explicit_identity_cannot_make_an_ineligible_nudge_eligible(self):
        assert (
            containment.dedup_key_for(
                "anything",
                "a message",
                "some_unlisted_reason",
                None,
                "an-identity",
            )
            is None
        )

    async def test_existing_reasons_ignore_a_supplied_identity(self):
        """Only a policy that asks for an explicit identity consults it."""
        with_identity = containment.dedup_key_for(
            "curiosity",
            "How are you feeling right now?",
            "curiosity_probe",
            None,
            "some-other-identity",
        )
        without = containment.dedup_key_for(
            "curiosity",
            "How are you feeling right now?",
            "curiosity_probe",
        )
        assert with_identity == without

    async def test_a_reminder_without_an_explicit_identity_still_dedups(self):
        """The defensive fallback: deterministic on the message, so it still
        collapses ordinary repeats and never merges two distinct items."""
        key = containment.dedup_key_for(
            sn.REMINDER_KIND,
            "Reminder: Car rego — due 3 June 2026",
            sn.REMINDER_REASON,
        )
        assert key is not None
        assert key == containment.dedup_key_for(
            sn.REMINDER_KIND,
            "Reminder: Car rego — due 3 June 2026",
            sn.REMINDER_REASON,
        )

    async def test_the_always_on_drives_still_resolve_their_default_cadences(self):
        class _On:
            cfg = {"proactive": {"schedule_reminders": True}}

        cadences = resolve_cadences(_On())
        assert cadences["self_check"] == "every:900"
        assert cadences["awaiting_response_check"] == "every:900"
