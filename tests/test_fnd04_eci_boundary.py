"""FND-04: the External Capability Interface contract, its refusals, and its limits.

`tests/test_fnd04_eci_vertical_slice.py` proves the loop works. This file
proves the boundary is a *boundary*: that it refuses what it must, that it
holds no authority of its own, and that it did not quietly grow a second
identity system, a second Parking Brake, a second audit store or a second
executive.

Several tests here assert over this package's **source** rather than its
behaviour. That is deliberate and follows
`tests/test_companion_no_actuation.py` and
`tests/test_windows_action_prohibitions.py`: a property stated in a docstring
is a promise, and a property asserted over the source is a fact.
"""

from __future__ import annotations

import asyncio
import pathlib
import sqlite3
from datetime import timedelta

import pytest

from bartholomew.eci import capabilities as caps
from bartholomew.eci import endpoint_auth, store
from bartholomew.eci.boundary import (
    CapabilityRefusedError,
    DirectiveIssuer,
    clear_responder,
    responder_installed,
)
from bartholomew.eci.contract import (
    MAX_DIRECTIVE_TTL_SECONDS,
    MAX_PAYLOAD_CHARS,
    CapabilityRef,
    ContractError,
    DirectiveStatus,
    EndpointIdentity,
    ExchangeKind,
    ExchangeOutcome,
    GovernedDirective,
    InboundExchange,
    new_correlation_id,
    new_directive_id,
    utc_now,
)

ECI_ROOT = pathlib.Path(__file__).resolve().parents[1] / "bartholomew" / "eci"

SPEAK = CapabilityRef(kind="multimodal.spoken_output", version=1)


@pytest.fixture
def endpoint() -> EndpointIdentity:
    return EndpointIdentity(
        endpoint_id="dev-abc123",
        user_id="user-xyz789",
        verified_by="eci-device-credential",
        endpoint_kind="windows",
    )


@pytest.fixture
def db(tmp_path) -> str:
    path = str(tmp_path / "eci.db")
    store.ensure_schema(path)
    return path


class _Device:
    """A stand-in for `platform.devices.VerifiedDevice`, duck-typed as the code is."""

    def __init__(self, *, declared=(SPEAK,), authorised=(SPEAK,)):
        self._declared = declared
        self._authorised = authorised
        self.manifest = self

    def declares(self, kind, version):
        return any(c.kind == kind and c.version == version for c in self._declared)

    def authorizes(self, kind, version):
        return any(c.kind == kind and c.version == version for c in self._authorised)


# ---------------------------------------------------------------------------
# 1. The contract refuses rather than repairs.
# ---------------------------------------------------------------------------
class TestTheContract:
    def test_a_result_must_name_the_directive_it_answers(self, endpoint):
        with pytest.raises(ContractError, match="correlation_id"):
            InboundExchange(
                endpoint=endpoint,
                exchange_id="x1",
                kind=ExchangeKind.RESULT,
                status=DirectiveStatus.SUCCEEDED,
            )

    def test_a_result_must_carry_a_known_status(self, endpoint):
        with pytest.raises(ContractError, match="never folded"):
            InboundExchange(
                endpoint=endpoint,
                exchange_id="x2",
                kind=ExchangeKind.RESULT,
                correlation_id=new_correlation_id(),
            )

    def test_an_observation_may_not_claim_to_be_a_result(self, endpoint):
        with pytest.raises(ContractError, match="may not claim to be the result"):
            InboundExchange(
                endpoint=endpoint,
                exchange_id="x3",
                kind=ExchangeKind.OBSERVATION,
                correlation_id=new_correlation_id(),
            )

    def test_an_unknown_contract_version_is_refused_not_downgraded(self, endpoint):
        with pytest.raises(ContractError, match="refused rather"):
            InboundExchange(
                endpoint=endpoint,
                exchange_id="x4",
                kind=ExchangeKind.OBSERVATION,
                contract_version=99,
            )

    def test_an_oversized_payload_is_refused(self, endpoint):
        with pytest.raises(ContractError, match="carries at most"):
            InboundExchange(
                endpoint=endpoint,
                exchange_id="x5",
                kind=ExchangeKind.OBSERVATION,
                payload={"blob": "x" * (MAX_PAYLOAD_CHARS + 1)},
            )

    def test_there_is_no_anonymous_endpoint_identity(self):
        """ "We could not tell who this is" is unrepresentable, not a default."""
        with pytest.raises(ContractError):
            EndpointIdentity(endpoint_id="", user_id="u", verified_by="v")
        with pytest.raises(ContractError):
            EndpointIdentity(endpoint_id="e", user_id="u", verified_by="   ")

    def test_an_endpoint_identity_carries_no_field_a_sender_could_set(self, endpoint):
        """There is no runtime/tenant field an endpoint could populate."""
        fields = set(EndpointIdentity.__dataclass_fields__)
        assert fields == {"endpoint_id", "user_id", "verified_by", "endpoint_kind"}
        assert endpoint.source_id == "eci:dev-abc123"

    def test_a_directive_cannot_buy_itself_an_unbounded_window(self, endpoint):
        now = utc_now()
        with pytest.raises(ContractError, match="at most"):
            GovernedDirective(
                directive_id=new_directive_id(),
                correlation_id=new_correlation_id(),
                endpoint_id=endpoint.endpoint_id,
                capability=SPEAK,
                issued_at=now,
                expires_at=now + timedelta(seconds=MAX_DIRECTIVE_TTL_SECONDS + 1),
                issued_by="test",
            )

    def test_a_directive_must_name_the_authority_that_decided_it(self, endpoint):
        with pytest.raises(ContractError, match="issued_by"):
            GovernedDirective(
                directive_id=new_directive_id(),
                correlation_id=new_correlation_id(),
                endpoint_id=endpoint.endpoint_id,
                capability=SPEAK,
                issued_by="  ",
            )

    def test_the_wire_shape_hands_an_endpoint_nothing_but_the_work(self, endpoint):
        directive = GovernedDirective(
            directive_id=new_directive_id(),
            correlation_id=new_correlation_id(),
            endpoint_id=endpoint.endpoint_id,
            capability=SPEAK,
            parameters={"text": "hello"},
            issued_by="seam",
        )
        wire = set(directive.as_dict())
        assert wire == {
            "directive_id",
            "correlation_id",
            "capability",
            "parameters",
            "issued_at",
            "expires_at",
            "contract_version",
        }
        # Nothing that could read as authority, identity or governance state.
        assert not wire & {"user_id", "endpoint_id", "principal", "brake", "approved"}


# ---------------------------------------------------------------------------
# 2. Capability is not authority, and availability is not declaration.
# ---------------------------------------------------------------------------
class TestCapabilityStanding:
    def test_an_unknown_kind_is_unsupported_not_approximated(self):
        decision = caps.resolve_standing(
            _Device(),
            CapabilityRef(kind="teleport.person", version=1),
            None,
        )
        assert decision.standing is caps.CapabilityStanding.UNSUPPORTED
        assert not decision.directable

    def test_a_known_kind_at_an_unknown_version_is_unsupported(self):
        decision = caps.resolve_standing(
            _Device(),
            CapabilityRef(kind="multimodal.spoken_output", version=7),
            None,
        )
        assert decision.standing is caps.CapabilityStanding.UNSUPPORTED

    def test_an_undeclared_capability_is_never_inferred_from_the_endpoint_kind(self):
        decision = caps.resolve_standing(
            _Device(declared=(), authorised=()),
            SPEAK,
            _report(True),
        )
        assert decision.standing is caps.CapabilityStanding.UNDECLARED
        assert "never infers" in decision.reason

    def test_declaring_is_not_being_granted(self):
        """Declared, understood, but outside the operator's approved set."""
        decision = caps.resolve_standing(
            _Device(declared=(SPEAK,), authorised=()),
            SPEAK,
            _report(True),
        )
        assert decision.standing is caps.CapabilityStanding.UNAUTHORISED
        assert "not being granted" in decision.reason

    def test_silence_is_not_readiness(self):
        decision = caps.resolve_standing(_Device(), SPEAK, None)
        assert decision.standing is caps.CapabilityStanding.UNREPORTED
        assert not decision.directable

    def test_an_explicit_no_is_reported_as_unavailable_with_its_reason(self):
        decision = caps.resolve_standing(
            _Device(),
            SPEAK,
            _report(False, detail="no audio device"),
        )
        assert decision.standing is caps.CapabilityStanding.UNAVAILABLE
        assert "no audio device" in decision.reason

    def test_a_stale_report_stops_being_believed(self):
        """An endpoint that has gone away cannot withdraw its own report."""
        old = _report(True, age_seconds=caps.AVAILABILITY_TTL_SECONDS + 1)
        decision = caps.resolve_standing(_Device(), SPEAK, old)
        assert decision.standing is caps.CapabilityStanding.UNREPORTED
        assert "no longer believed" in decision.reason

    def test_an_unreadable_registry_is_not_a_permission(self):
        class Exploding:
            manifest = None

            def authorizes(self, kind, version):
                raise RuntimeError("registry is down")

        decision = caps.resolve_standing(Exploding(), SPEAK, _report(True))
        assert not decision.directable

    def test_only_available_is_directable(self):
        assert caps.DIRECTABLE == frozenset({caps.CapabilityStanding.AVAILABLE})


def _report(available: bool, *, detail: str = "", age_seconds: int = 0):
    return caps.AvailabilityReport(
        capability=SPEAK,
        available=available,
        reported_at=utc_now() - timedelta(seconds=age_seconds),
        detail=detail,
    )


# ---------------------------------------------------------------------------
# 3. Correlation: exactly once, or refused.
# ---------------------------------------------------------------------------
class TestCorrelation:
    def _directive(self, db, endpoint, *, ttl=120):
        directive = GovernedDirective(
            directive_id=new_directive_id(),
            correlation_id=new_correlation_id(),
            endpoint_id=endpoint.endpoint_id,
            capability=SPEAK,
            parameters={"text": "hello"},
            expires_at=utc_now() + timedelta(seconds=ttl),
            issued_by="seam",
        )
        store.record_directive(db, directive, user_id=endpoint.user_id)
        return directive

    def test_a_result_settles_exactly_once(self, db, endpoint):
        directive = self._directive(db, endpoint)
        first = store.settle_directive(
            db,
            correlation_id=directive.correlation_id,
            endpoint_id=endpoint.endpoint_id,
            status=DirectiveStatus.SUCCEEDED,
            reason=None,
            result_exchange_id="r1",
        )
        second = store.settle_directive(
            db,
            correlation_id=directive.correlation_id,
            endpoint_id=endpoint.endpoint_id,
            status=DirectiveStatus.FAILED,
            reason="a replay",
            result_exchange_id="r2",
        )
        assert first.outcome is store.SettleOutcome.SETTLED
        assert second.outcome is store.SettleOutcome.ALREADY_SETTLED
        assert store.get_directive(db, directive.correlation_id).result_status == "succeeded"

    def test_an_unknown_correlation_id_is_refused_not_attributed(self, db, endpoint):
        self._directive(db, endpoint)
        settled = store.settle_directive(
            db,
            correlation_id="eci-doesnotexist",
            endpoint_id=endpoint.endpoint_id,
            status=DirectiveStatus.SUCCEEDED,
            reason=None,
            result_exchange_id="r",
        )
        assert settled.outcome is store.SettleOutcome.UNCORRELATED
        assert settled.directive is None

    def test_another_endpoints_directive_is_refused_outright(self, db, endpoint):
        directive = self._directive(db, endpoint)
        settled = store.settle_directive(
            db,
            correlation_id=directive.correlation_id,
            endpoint_id="dev-someone-else",
            status=DirectiveStatus.SUCCEEDED,
            reason=None,
            result_exchange_id="r",
        )
        assert settled.outcome is store.SettleOutcome.WRONG_ENDPOINT
        # Nothing leaked about the directive it was not entitled to.
        assert settled.directive is None
        assert store.get_directive(db, directive.correlation_id).state == store.STATE_ISSUED

    def test_a_late_result_is_recorded_as_timed_out_not_as_what_was_reported(
        self,
        db,
        endpoint,
    ):
        directive = self._directive(db, endpoint, ttl=1)
        settled = store.settle_directive(
            db,
            correlation_id=directive.correlation_id,
            endpoint_id=endpoint.endpoint_id,
            status=DirectiveStatus.SUCCEEDED,
            reason=None,
            result_exchange_id="r",
            now=utc_now() + timedelta(seconds=5),
        )
        assert settled.outcome is store.SettleOutcome.EXPIRED
        assert store.get_directive(db, directive.correlation_id).result_status == "timed_out"

    def test_a_directive_nobody_answers_does_not_stay_in_flight_forever(
        self,
        db,
        endpoint,
    ):
        """(H) The honest handling of an endpoint that disappeared."""
        directive = self._directive(db, endpoint, ttl=1)
        swept = store.expire_overdue_directives(db, now=utc_now() + timedelta(seconds=10))
        assert swept == 1
        row = store.get_directive(db, directive.correlation_id)
        assert row.state == store.STATE_SETTLED
        assert row.result_status == "timed_out"

    def test_sweeping_creates_no_retry(self, db, endpoint):
        """`recovery.py` owns what follows a failure. A sweep must not invent one."""
        self._directive(db, endpoint, ttl=1)
        before = len(store.list_directives(db, endpoint.endpoint_id))
        store.expire_overdue_directives(db, now=utc_now() + timedelta(seconds=10))
        after = store.list_directives(db, endpoint.endpoint_id)
        assert len(after) == before, "a sweep manufactured a new directive"

    def test_a_directive_is_recorded_before_it_could_be_handed_over(self, db, endpoint):
        directive = self._directive(db, endpoint)
        assert store.get_directive(db, directive.correlation_id) is not None

    def test_two_directives_cannot_share_a_correlation_id(self, db, endpoint):
        directive = self._directive(db, endpoint)
        clash = GovernedDirective(
            directive_id=new_directive_id(),
            correlation_id=directive.correlation_id,
            endpoint_id=endpoint.endpoint_id,
            capability=SPEAK,
            issued_by="seam",
        )
        with pytest.raises(store.EciPersistenceError, match="not issued"):
            store.record_directive(db, clash, user_id=endpoint.user_id)

    def test_the_schema_is_additive_and_idempotent(self, db):
        store.ensure_schema(db)
        store.ensure_schema(db)
        with sqlite3.connect(db) as conn:
            names = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
        assert {"eci_directives", "eci_availability"} <= names


# ---------------------------------------------------------------------------
# 4. The issuer is the guard rail, not a convenience.
# ---------------------------------------------------------------------------
class TestDirectiveIssuer:
    def test_it_refuses_a_capability_the_endpoint_may_not_be_given(self, db, endpoint):
        issuer = DirectiveIssuer(
            endpoint=endpoint,
            device=_Device(declared=(), authorised=()),
            db_path=db,
            now=utc_now(),
        )
        with pytest.raises(CapabilityRefusedError):
            issuer.issue(SPEAK, parameters={"text": "hi"}, issued_by="seam")
        assert store.list_directives(db, endpoint.endpoint_id) == []

    def test_it_refuses_a_capability_with_no_availability_report(self, db, endpoint):
        issuer = DirectiveIssuer(
            endpoint=endpoint,
            device=_Device(),
            db_path=db,
            now=utc_now(),
        )
        with pytest.raises(CapabilityRefusedError):
            issuer.issue(SPEAK, parameters={"text": "hi"}, issued_by="seam")

    def test_one_exchange_carries_at_most_one_directive(self, db, endpoint):
        store.record_availability(db, endpoint.endpoint_id, _report(True))
        issuer = DirectiveIssuer(
            endpoint=endpoint,
            device=_Device(),
            db_path=db,
            now=utc_now(),
        )
        issuer.issue(SPEAK, parameters={"text": "one"}, issued_by="seam")
        with pytest.raises(CapabilityRefusedError, match="already been issued"):
            issuer.issue(SPEAK, parameters={"text": "two"}, issued_by="seam")


# ---------------------------------------------------------------------------
# 5. Bartholomew decides; the boundary carries.
# ---------------------------------------------------------------------------
class TestTheResponderSeam:
    def test_the_boundary_installs_no_responder_of_its_own(self):
        clear_responder()
        assert responder_installed() is False

    def test_the_voice_brake_stops_the_responder_issuing_a_directive(self, tmp_path, db, endpoint):
        """The directive-level gate, isolated from the capture-level one.

        Proved here rather than over HTTP because the capture path refuses on
        *any* engaged brake before a responder is ever reached -- so the only
        way to see this gate on its own is to call the responder directly.
        """
        from bartholomew.integration.eci_responder import SpokenAcknowledgementResponder
        from bartholomew.orchestrator.safety.governance_store import GovernanceStore

        kernel_db = str(tmp_path / "kernel.db")
        GovernanceStore(kernel_db).engage("voice", reason="test", actor="test")

        store.record_availability(db, endpoint.endpoint_id, _report(True))
        issuer = DirectiveIssuer(
            endpoint=endpoint,
            device=_Device(),
            db_path=db,
            now=utc_now(),
        )
        exchange = InboundExchange(
            endpoint=endpoint,
            exchange_id="e",
            kind=ExchangeKind.REQUEST,
            payload={"text": "say something"},
        )
        responder = SpokenAcknowledgementResponder(
            db_path=kernel_db,
            runtime_cfg={"voice": {"spoken_output": True}},
        )

        directive = asyncio.run(responder.respond(exchange, issuer))

        assert directive is None
        assert store.list_directives(db, endpoint.endpoint_id) == []

    def test_an_observation_is_never_answered_with_speech(self, db, endpoint):
        """Something happening near an endpoint is not a cue to talk."""
        from bartholomew.integration.eci_responder import SpokenAcknowledgementResponder

        exchange = InboundExchange(
            endpoint=endpoint,
            exchange_id="obs",
            kind=ExchangeKind.OBSERVATION,
            payload={"text": "the kettle boiled"},
        )
        responder = SpokenAcknowledgementResponder(
            db_path=db,
            runtime_cfg={"voice": {"spoken_output": True}},
        )
        assert asyncio.run(responder.respond(exchange, object())) is None


# ---------------------------------------------------------------------------
# 6. Channel separation: three doors, three keys.
# ---------------------------------------------------------------------------
class TestChannelSeparation:
    def test_opening_the_eci_does_not_open_inbound_capture(self):
        from bartholomew_api_bridge_v0_1.services.api import inbound_auth

        inbound_auth.clear_resolver()
        endpoint_auth.install_resolver(endpoint_auth.DeviceEndpointResolver())
        try:
            assert inbound_auth.get_resolver() is None
        finally:
            endpoint_auth.clear_resolver()

    def test_opening_the_eci_does_not_open_the_action_channel(self):
        from bartholomew_api_bridge_v0_1.services.api import device_action_auth

        device_action_auth.clear_resolver()
        endpoint_auth.install_resolver(endpoint_auth.DeviceEndpointResolver())
        try:
            assert device_action_auth.get_resolver() is None
        finally:
            endpoint_auth.clear_resolver()

    def test_the_eci_channel_is_closed_by_default_and_needs_its_own_gate(self, monkeypatch):
        endpoint_auth.clear_resolver()
        monkeypatch.delenv(endpoint_auth.ENDPOINT_AUTH_ENV, raising=False)
        assert endpoint_auth.maybe_install_from_env() is False
        assert endpoint_auth.get_resolver() is None

        monkeypatch.setenv(endpoint_auth.ENDPOINT_AUTH_ENV, "1")
        try:
            assert endpoint_auth.maybe_install_from_env() is True
            assert endpoint_auth.get_resolver() is not None
        finally:
            endpoint_auth.clear_resolver()

    def test_every_eci_route_is_classified_in_the_default_deny_table(self):
        from bartholomew.platform.route_policy import ROUTE_CAPABILITIES
        from bartholomew_api_bridge_v0_1.services.api.routes import eci

        for route in eci.router.routes:
            for method in route.methods:
                if method in ("HEAD", "OPTIONS"):
                    continue
                assert (
                    method,
                    route.path,
                ) in ROUTE_CAPABILITIES, (
                    f"{method} {route.path} is unclassified and would be refused"
                )


# ---------------------------------------------------------------------------
# 7. What this package must never grow. Asserted over its source.
# ---------------------------------------------------------------------------
class TestStructuralProhibitions:
    def _sources(self) -> dict[str, str]:
        return {p.name: p.read_text(encoding="utf-8") for p in ECI_ROOT.glob("*.py")}

    def test_it_holds_no_second_parking_brake(self):
        """One brake, read through the one governed capture path."""
        for name, src in self._sources().items():
            assert "GovernanceStore(" not in src, f"{name} constructs a second brake reader"
            assert ".engage(" not in src, f"{name} can change brake state"
            assert ".disengage(" not in src, f"{name} can change brake state"

    def test_it_mints_no_identity_and_reads_none_from_a_caller(self):
        for name, src in self._sources().items():
            assert "create_account" not in src, f"{name} mints identity"
            assert "create_session" not in src, f"{name} mints sessions"
            for claim in (
                'request.headers.get("x-user',
                "payload['user_id']",
                'payload["user_id"]',
            ):
                assert claim not in src, f"{name} reads identity from a caller"

    def test_it_creates_no_second_audit_store(self):
        """Provenance is the existing Reflection trail and the capture row.

        The boundary owns exactly two tables -- the correlation ledger and the
        availability record -- and neither is an audit log. Everything an
        auditor reads was written by an authority that already existed.
        """
        for name, src in self._sources().items():
            if name == "store.py":
                continue
            assert (
                "CREATE TABLE" not in src
            ), f"{name} creates durable state outside the boundary's own two tables"

        # Asserted against the schema constant, not the file text: the module
        # docstring names the DDL it uses, and counting prose is not counting
        # tables.
        assert store.ECI_SCHEMA.count("CREATE TABLE IF NOT EXISTS") == 2
        assert "eci_directives" in store.ECI_SCHEMA
        assert "eci_availability" in store.ECI_SCHEMA
        # Every write path is additive: an existing database gains two tables
        # and loses nothing, so there is no migration and no data-loss path.
        for destructive in ("DROP TABLE", "ALTER TABLE", "DELETE FROM"):
            assert destructive not in store.ECI_SCHEMA.upper()

    def test_it_reaches_no_operating_system(self):
        """A boundary that transports must not also act."""
        for name, src in self._sources().items():
            for forbidden in ("import subprocess", "os.system", "import ctypes", "Popen"):
                assert forbidden not in src, f"{name} can act on a machine"

    def test_it_does_not_branch_on_payload_content(self):
        """Inbound content is data. There is no path from it to a decision."""
        for name, src in self._sources().items():
            if name == "reference_endpoint.py":
                continue  # the test endpoint reads its own directive's parameters
            for smell in ('payload.get("command', 'payload["command', "payload.get('intent"):
                assert smell not in src, f"{name} reads payload content as an instruction"

    def test_the_capability_vocabulary_is_the_platforms_not_a_second_one(self):
        """FND-04 widened no vocabulary; it composes the frozen one."""
        src = (ECI_ROOT / "capabilities.py").read_text(encoding="utf-8")
        assert "from bartholomew.platform.device_capabilities import supports" in src
        assert "CAPABILITY_VERSIONS = {" not in src

    def test_nothing_recorded_means_nothing_recorded(self):
        from bartholomew.eci.contract import NOTHING_RECORDED

        assert ExchangeOutcome.REFUSED_BRAKE in NOTHING_RECORDED
        assert ExchangeOutcome.ACCEPTED not in NOTHING_RECORDED
        assert ExchangeOutcome.REFUSED_GOVERNANCE not in NOTHING_RECORDED


# ---------------------------------------------------------------------------
# 8. The startup wiring, which is what a deployment actually gets.
# ---------------------------------------------------------------------------
class TestStartupWiring:
    """`install_seams()` is the single place the boundary is put in place.

    Tested because every other test in this file installs the responder
    directly, and a seam that only works when a test wires it is not wired.
    """

    def _install(self, tmp_path, monkeypatch, **cfg):
        monkeypatch.setenv("BARTH_PLATFORM_DB_PATH", str(tmp_path / "platform.db"))
        monkeypatch.setenv("BARTH_DATA_ROOT", str(tmp_path / "data"))
        monkeypatch.setenv("BARTH_DB_PATH", str(tmp_path / "kernel.db"))
        from bartholomew.integration.install import install_seams

        clear_responder()
        endpoint_auth.clear_resolver()
        return install_seams(
            db_path=str(tmp_path / "kernel.db"),
            tenant_id=None,
            **cfg,
        ).to_dict()

    def test_it_installs_the_responder_and_reports_it(self, tmp_path, monkeypatch):
        report = self._install(
            tmp_path,
            monkeypatch,
            runtime_cfg={"voice": {"spoken_output": True}},
        )
        try:
            assert responder_installed() is True
            assert "run_spoken_output_through_runtime_contract" in report["eci_responder"]
            assert "voice.spoken_output=on" in report["eci_responder"]
            assert report["errors"] == []
        finally:
            clear_responder()

    def test_it_reports_the_enablement_it_actually_read(self, tmp_path, monkeypatch):
        """Not a claim: the report says which switch it found, and where."""
        report = self._install(tmp_path, monkeypatch, runtime_cfg=None)
        try:
            assert "voice.spoken_output=off" in report["eci_responder"]
        finally:
            clear_responder()

    def test_integrating_the_system_does_not_open_the_boundary(self, tmp_path, monkeypatch):
        """Installing the seams makes the system coherent, not permissive."""
        monkeypatch.delenv(endpoint_auth.ENDPOINT_AUTH_ENV, raising=False)
        report = self._install(tmp_path, monkeypatch)
        try:
            assert endpoint_auth.get_resolver() is None
            assert report["eci_endpoint_channel"].startswith("closed")
            assert endpoint_auth.ENDPOINT_AUTH_ENV in report["eci_endpoint_channel"]
        finally:
            clear_responder()

    def test_the_gate_opens_the_channel_when_a_deployment_sets_it(self, tmp_path, monkeypatch):
        monkeypatch.setenv(endpoint_auth.ENDPOINT_AUTH_ENV, "1")
        report = self._install(tmp_path, monkeypatch)
        try:
            assert endpoint_auth.get_resolver() is not None
            assert report["eci_endpoint_channel"].startswith("open")
        finally:
            endpoint_auth.clear_resolver()
            clear_responder()
