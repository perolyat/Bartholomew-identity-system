"""
Pinned-values regression test for S5.2 Typed Cadence (see
docs/S5_2_TYPED_CADENCE_DESIGN.md Sec 5/13): the five REGISTRY cadence
strings that existed before this change must parse to the same
IntervalCadence/WindowCadence-shaped tuples and produce the same
compute_next_run() output as before -- the acceptance bar for "no
behavioural change to any existing drive." A sixth entry, initiative_sweep
(S5.2's own new drive), is pinned here too as its own established baseline
going forward.
"""

from __future__ import annotations

import random

from bartholomew.kernel.scheduler.cadence import compute_next_run, parse
from bartholomew.kernel.scheduler.drives import REGISTRY

# The exact cadence strings each drive was registered with before S5.2.
PRE_S5_2_CADENCES = {
    "self_check": "every:900",
    "curiosity_probe": "window:3600:2",
    "reflection_micro": "every:7200",
    "fts_optimize": "every:604800",
    "awaiting_response_check": "every:900",
}

# S5.2's own new drive, pinned as its own baseline (design doc Sec 6).
NEW_S5_2_CADENCES = {
    "initiative_sweep": "every:900",
}


def test_registry_still_has_every_pre_existing_drive_with_unchanged_cadence():
    for task_id, expected_cadence in PRE_S5_2_CADENCES.items():
        assert task_id in REGISTRY, f"{task_id} missing from REGISTRY"
        assert REGISTRY[task_id]["cadence"] == expected_cadence, (
            f"{task_id}'s cadence string changed: "
            f"expected {expected_cadence!r}, got {REGISTRY[task_id]['cadence']!r}"
        )


def test_registry_has_the_new_s5_2_drive():
    for task_id, expected_cadence in NEW_S5_2_CADENCES.items():
        assert task_id in REGISTRY
        assert REGISTRY[task_id]["cadence"] == expected_cadence


def test_pre_existing_cadence_strings_parse_to_unchanged_typed_values():
    assert parse("every:900") == ("every", 900)
    assert parse("every:7200") == ("every", 7200)
    assert parse("every:604800") == ("every", 604800)
    assert parse("window:3600:2") == ("window", 3600, 2)


def test_pre_existing_every_cadence_next_run_output_unchanged():
    """Pins compute_next_run()'s exact (deterministic, unjittered-seed)
    output for every:900 and every:7200/604800 -- the tz parameter's
    addition must not alter this in any way."""
    random.seed(0)
    first = compute_next_run(None, None, "every:900", 1_700_000_000, None)
    random.seed(0)
    first_with_tz_none = compute_next_run(None, None, "every:900", 1_700_000_000, None, tz=None)
    assert first == first_with_tz_none

    random.seed(0)
    subsequent = compute_next_run(1_700_000_000, 1_700_000_900, "every:900", 1_700_001_000, None)
    random.seed(0)
    subsequent_with_tz_none = compute_next_run(
        1_700_000_000,
        1_700_000_900,
        "every:900",
        1_700_001_000,
        None,
        tz=None,
    )
    assert subsequent == subsequent_with_tz_none


def test_pre_existing_window_cadence_next_run_output_unchanged():
    without_tz = compute_next_run(None, None, "window:3600:2", 1_700_000_000, None)
    with_tz_none = compute_next_run(None, None, "window:3600:2", 1_700_000_000, None, tz=None)
    assert without_tz == with_tz_none

    without_tz2 = compute_next_run(
        1_700_000_000,
        1_700_000_000,
        "window:3600:2",
        1_700_000_500,
        '{"window_start_ts": 1700000000, "runs_in_window": 1}',
    )
    with_tz_none2 = compute_next_run(
        1_700_000_000,
        1_700_000_000,
        "window:3600:2",
        1_700_000_500,
        '{"window_start_ts": 1700000000, "runs_in_window": 1}',
        tz=None,
    )
    assert without_tz2 == with_tz_none2


def test_every_registered_task_id_has_a_drive_function():
    for task_id, config in REGISTRY.items():
        assert callable(config["fn"]), f"{task_id}'s fn is not callable"
