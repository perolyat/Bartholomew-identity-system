"""The canonical envelope and the event-type registry (Package A).

Two claims, both structural:

* an envelope can be built from an `inbound_events` row that predates it, and
  a version this build does not understand is refused rather than guessed at;
* an event type is either registered in first-party code with a typed payload
  and one handler, or it is unknown -- and there is no third way for one to
  appear.
"""

from __future__ import annotations

import pytest

from bartholomew.kernel import inbound_store
from bartholomew.kernel.event_processing import registry
from bartholomew.kernel.event_processing.adapters import (
    MAX_PAYLOAD_DEPTH,
    OBSERVATION_NOTE,
    OBSERVATION_STATUS,
    ObservationPayload,
    handle_observation,
)
from bartholomew.kernel.event_processing.envelope import (
    ENVELOPE_VERSION,
    CanonicalEvent,
    EnvelopeVersionError,
    is_supported_version,
    payload_matches_digest,
)
from bartholomew.kernel.event_processing.registry import (
    HandlerRegistrationError,
    HandlerResult,
    PayloadValidationError,
    RegisteredEventType,
)
from bartholomew.kernel.event_processing.store import (
    STATE_PROCESSED,
    STATE_QUARANTINED,
)


@pytest.fixture
def captured(tmp_path):
    """A real capture row, written by the real capture store."""
    db = str(tmp_path / "envelope.db")
    inbound_store.ensure_schema(db)
    stored = inbound_store.capture_event(
        db,
        source_id="src-roofer",
        event_id="evt-1",
        event_type=OBSERVATION_NOTE,
        occurred_at="2026-08-30T09:00:00Z",
        payload={"subject": "Roof repair", "body": "Roofer confirmed attendance Tuesday."},
        outcome=inbound_store.OUTCOME_CAPTURED,
        governance_reason=None,
        verified_by="test-resolver",
        runtime_id=None,
    )
    payload = inbound_store.get_event_payload(db, "src-roofer", "evt-1")
    return db, stored, payload


# ----------------------------------------------------------------- envelope


def test_an_envelope_is_built_from_an_unmodified_capture_row(captured):
    _db, stored, payload = captured
    event = CanonicalEvent.from_inbound_row(stored, payload)

    assert event.envelope_version == ENVELOPE_VERSION
    assert event.source_id == "src-roofer"
    assert event.event_id == "evt-1"
    assert event.event_type == OBSERVATION_NOTE
    assert event.occurred_at == "2026-08-30T09:00:00Z"
    assert event.received_at == stored.received_at
    assert event.payload_sha256 == stored.payload_sha256
    assert event.verified_by == "test-resolver"
    assert event.inbound_row_id == stored.row_id
    # Nothing about the capture record had to change for this to work: the
    # envelope is a reading of the columns capture already wrote.
    assert event.payload == payload


def test_an_envelope_satisfies_the_interpretation_seams_stored_contract(captured):
    """`interpret_captured_event` reads `stored` entirely through getattr.

    If the envelope stopped satisfying that shape, the backbone would have to
    build a second object describing the same row -- which is exactly the
    duplicated authority this design avoids.
    """
    _db, stored, payload = captured
    event = CanonicalEvent.from_inbound_row(stored, payload)
    for attribute in (
        "row_id",
        "source_id",
        "event_id",
        "event_type",
        "occurred_at",
        "received_at",
        "payload_sha256",
        "verified_by",
        "outcome",
        "duplicate",
    ):
        assert hasattr(event, attribute), attribute
    assert event.outcome == inbound_store.OUTCOME_CAPTURED
    assert event.duplicate is False


def test_an_envelope_can_be_built_from_a_plain_row_mapping(captured):
    _db, stored, payload = captured
    event = CanonicalEvent.from_inbound_row(stored.as_dict(), payload)
    assert event.source_id == "src-roofer"
    assert event.inbound_row_id == stored.row_id


def test_an_unreadable_capture_record_is_refused_rather_than_half_built():
    with pytest.raises(ValueError, match="missing the fields"):
        CanonicalEvent.from_inbound_row({"source_id": "src"}, {"body": "x"})


def test_an_unknown_envelope_version_is_refused_not_guessed_at(captured):
    _db, stored, payload = captured
    assert is_supported_version(1) is True
    assert is_supported_version(99) is False
    assert is_supported_version("not a number") is False
    with pytest.raises(EnvelopeVersionError):
        CanonicalEvent.from_inbound_row(stored, payload, envelope_version=99)


def test_the_digest_check_catches_a_payload_that_changed_after_capture(captured):
    _db, stored, payload = captured
    assert payload_matches_digest(CanonicalEvent.from_inbound_row(stored, payload)) is True
    tampered = CanonicalEvent.from_inbound_row(stored, {"body": "something else entirely"})
    assert payload_matches_digest(tampered) is False


def test_the_envelope_never_renders_the_payload(captured):
    _db, stored, payload = captured
    rendered = CanonicalEvent.from_inbound_row(stored, payload).to_dict()
    assert "payload" not in rendered
    assert rendered["payload_sha256"] == stored.payload_sha256


def test_the_idempotency_key_is_the_pair_capture_made_unique(captured):
    _db, stored, payload = captured
    event = CanonicalEvent.from_inbound_row(stored, payload)
    other = CanonicalEvent.from_inbound_row(
        {**stored.as_dict(), "event_id": "evt-2"},
        payload,
    )
    assert event.idempotency_key != other.idempotency_key
    assert event.source_id in event.idempotency_key
    assert event.event_id in event.idempotency_key


# ----------------------------------------------------------------- registry


def test_the_registered_types_are_the_declared_ones():
    assert registry.registered_types() == (OBSERVATION_NOTE, OBSERVATION_STATUS)
    for spec in registry.describe_registry():
        assert spec["description"]


def test_an_unregistered_type_looks_up_to_nothing():
    assert registry.lookup("mail.received") is None
    assert registry.lookup("") is None


def test_a_type_cannot_be_re_registered_to_a_second_handler():
    async def other(ctx, event, payload):  # pragma: no cover - never called
        raise AssertionError

    with pytest.raises(HandlerRegistrationError, match="already registered"):
        registry.register(
            RegisteredEventType(
                event_type=OBSERVATION_NOTE,
                parse=ObservationPayload.parse,
                handler=other,
                description="a competing claim on the same type",
            ),
        )
    # The original registration is untouched.
    assert registry.lookup(OBSERVATION_NOTE).handler is handle_observation


def test_registering_the_same_spec_again_is_a_no_op():
    spec = registry.lookup(OBSERVATION_NOTE)
    assert registry.register(spec) is spec


def test_a_registration_without_a_callable_handler_is_refused():
    with pytest.raises(HandlerRegistrationError, match="callable"):
        registry.register(
            RegisteredEventType(
                event_type="observation.bogus",
                parse=ObservationPayload.parse,
                handler="not a function",
                description="x",
            ),
        )
    assert registry.lookup("observation.bogus") is None


def test_a_registration_needs_a_non_empty_type():
    with pytest.raises(HandlerRegistrationError, match="non-empty"):
        registry.register(
            RegisteredEventType(
                event_type="  ",
                parse=ObservationPayload.parse,
                handler=handle_observation,
                description="x",
            ),
        )


def test_register_refuses_anything_that_is_not_a_registration():
    with pytest.raises(HandlerRegistrationError, match="RegisteredEventType"):
        registry.register({"event_type": "observation.note"})


# ------------------------------------------------------------ typed payload


def test_a_payload_that_is_not_an_object_is_refused():
    for bad in (["a", "list"], "a string", 7, None):
        with pytest.raises(PayloadValidationError, match="JSON object"):
            ObservationPayload.parse(bad)


def test_a_payload_deeper_than_interpretation_reads_is_refused():
    payload: dict = {"leaf": "bottom"}
    for _ in range(MAX_PAYLOAD_DEPTH + 2):
        payload = {"nested": payload}
    with pytest.raises(PayloadValidationError, match="nests deeper"):
        ObservationPayload.parse(payload)


def test_an_oversized_payload_is_refused():
    with pytest.raises(PayloadValidationError, match="over the"):
        ObservationPayload.parse({"body": "x" * (64 * 1024 + 10)})


def test_a_parsed_payload_carries_the_text_the_interpretation_seam_will_read():
    parsed = ObservationPayload.parse(
        {"subject": "Roof repair", "body": "Roofer confirmed attendance Tuesday."},
    )
    assert parsed.has_text
    assert "Roofer confirmed attendance Tuesday." in parsed.text
    # Domain-blind: no key is privileged, so an unfamiliar shape still parses.
    odd = ObservationPayload.parse({"zzz": {"qqq": ["a note"]}})
    assert "a note" in odd.text


def test_an_empty_payload_parses_and_is_left_for_interpretation_to_judge():
    """Emptiness is a verdict for the interpretation seam, not a parse error.

    `no_interpretable_content` is an explicit irrelevant disposition there;
    refusing it here would replace a reached answer with a declined one.
    """
    parsed = ObservationPayload.parse({})
    assert parsed.has_text is False


# ------------------------------------------------------------ handler result


def test_a_handler_result_must_name_a_settleable_disposition():
    assert HandlerResult(disposition=STATE_PROCESSED, reason="ok").disposition == STATE_PROCESSED
    with pytest.raises(ValueError, match="disposition must be one of"):
        HandlerResult(disposition=STATE_QUARANTINED, reason="ok")
    with pytest.raises(ValueError, match="disposition must be one of"):
        HandlerResult(disposition="claimed", reason="ok")


def test_a_handler_result_must_carry_a_machine_readable_reason():
    with pytest.raises(ValueError, match="machine-readable reason"):
        HandlerResult(disposition=STATE_PROCESSED, reason="   ")
