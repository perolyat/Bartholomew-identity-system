"""Contract test for the machine-readable wave coordination manifest.

Every wave from Wave 3 on carries ``docs/waves/<WAVE>/<WAVE>_MANIFEST.yaml``, the
authoritative record of session identities (immutable ids, semantic names,
branch / PR / handoff naming), ownership boundaries, dependencies, required CI
tier and integration order. Builder and integration sessions orient themselves
from it without reconstructing the wave from chat history, so it has to stay
parseable and internally consistent. This is the PR Fast tier's guard that it
does.

The rules pinned here are the ones ``docs/waves/W03/README.md`` states:

* session ids are immutable and match ``<WAVE>-PREP`` / ``<WAVE>-[A-Z]``;
* a builder's branch is ``wave/<wave>-<letter>-<slug>``, its PR title starts with
  ``[<ID>] <semantic name>``, its handoff is ``BARTHOLOMEW_<WAVE>_<LETTER>_HANDOFF.md``;
* dependencies name known sessions and form no cycle;
* the integration session's order lists every builder exactly once;
* two builders never own the same path.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.smoke

REPO_ROOT = Path(__file__).resolve().parent.parent
WAVES_DIR = REPO_ROOT / "docs" / "waves"

ALLOWED_TIERS = {"pr_fast", "integration", "merge_candidate", "nightly"}
ALLOWED_KINDS = {"prep", "builder", "integration"}
ALLOWED_STATUS = {
    "not_started",
    "ready_to_start",
    "in_progress",
    "frozen",
    "integrated",
    "complete",
    "blocked",
}


def _manifests() -> list[Path]:
    if not WAVES_DIR.exists():
        return []
    return sorted(WAVES_DIR.glob("W*/W*_MANIFEST.yaml"))


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), f"{path} is not a mapping at top level"
    return data


MANIFESTS = _manifests()
_IDS = [p.parent.name for p in MANIFESTS]


def test_at_least_one_wave_manifest_exists():
    assert MANIFESTS, f"no wave manifest under {WAVES_DIR}"


@pytest.mark.parametrize("path", MANIFESTS, ids=_IDS)
def test_manifest_identity_and_naming(path: Path):
    data = _load(path)
    wave = data["wave"]
    wave_id = wave["id"]
    assert re.fullmatch(r"W\d{2}", wave_id), wave_id
    assert path.parent.name == wave_id, f"{path} lives under {path.parent.name}, not {wave_id}"
    assert path.name == f"{wave_id}_MANIFEST.yaml"
    for key in ("objective", "baseline", "ci_tiers", "status"):
        assert key in wave, f"wave.{key} missing"

    sessions = data["sessions"]
    assert isinstance(sessions, list) and sessions
    ids = [s["id"] for s in sessions]
    assert len(ids) == len(set(ids)), f"duplicate session ids: {ids}"

    id_re = re.compile(rf"^{wave_id}-(PREP|[A-Z])$")
    for s in sessions:
        sid = s["id"]
        assert id_re.fullmatch(sid), f"session id {sid!r} does not match {id_re.pattern}"
        assert s["kind"] in ALLOWED_KINDS, f"{sid}: kind {s['kind']!r}"
        assert s["status"] in ALLOWED_STATUS, f"{sid}: status {s['status']!r}"
        assert s["required_ci_tier"] in ALLOWED_TIERS, f"{sid}: tier {s['required_ci_tier']!r}"
        name = s["name"]
        assert isinstance(name, str) and name.strip(), f"{sid}: empty semantic name"
        assert s["pr_title"].startswith(f"[{sid}] "), f"{sid}: PR title must start with [{sid}]"
        assert (
            s["session_title"] == f"{sid} — {name}"
        ), f"{sid}: session_title must be '<id> — <name>'"
        contract = REPO_ROOT / s["contract"]
        assert contract.is_file(), f"{sid}: contract file {s['contract']} missing"
        assert sid in contract.read_text(
            encoding="utf-8",
        ), f"{sid}: contract does not name its session"
        if s["kind"] in {"builder", "integration"}:
            letter = sid.rsplit("-", 1)[1]
            branch_re = re.compile(
                rf"^wave/{wave_id.lower()}-{letter.lower()}-[a-z0-9]+(-[a-z0-9]+)*$",
            )
            assert branch_re.fullmatch(s["branch"]), f"{sid}: branch {s['branch']!r}"
            assert s["handoff"] == f"BARTHOLOMEW_{wave_id}_{letter}_HANDOFF.md", f"{sid}: handoff"


@pytest.mark.parametrize("path", MANIFESTS, ids=_IDS)
def test_manifest_dependencies_are_known_and_acyclic(path: Path):
    data = _load(path)
    sessions = {s["id"]: s for s in data["sessions"]}
    for sid, s in sessions.items():
        for dep in s.get("depends_on", []) or []:
            assert dep in sessions, f"{sid} depends on unknown session {dep!r}"
        for dep in s.get("requires_frozen", []) or []:
            assert dep in sessions, f"{sid} requires unknown frozen head {dep!r}"
            assert dep in (
                s.get("depends_on") or []
            ), f"{sid}: requires_frozen {dep} not in depends_on"

    state: dict[str, int] = {}

    def visit(node: str, trail: list[str]) -> None:
        if state.get(node) == 1:
            raise AssertionError(f"dependency cycle: {' -> '.join([*trail, node])}")
        if state.get(node) == 2:
            return
        state[node] = 1
        for dep in sessions[node].get("depends_on", []) or []:
            visit(dep, [*trail, node])
        state[node] = 2

    for sid in sessions:
        visit(sid, [])


@pytest.mark.parametrize("path", MANIFESTS, ids=_IDS)
def test_integration_order_covers_every_builder_once(path: Path):
    data = _load(path)
    sessions = data["sessions"]
    builders = {s["id"] for s in sessions if s["kind"] == "builder"}
    integrators = [s for s in sessions if s["kind"] == "integration"]
    assert len(integrators) == 1, "exactly one integration session per wave"
    order = integrators[0]["integration_order"]
    assert list(order) == list(dict.fromkeys(order)), f"integration_order repeats: {order}"
    assert set(order) == builders, f"integration_order {order} != builders {sorted(builders)}"
    for b in builders:
        assert b in (integrators[0].get("depends_on") or []), f"integration must depend on {b}"


@pytest.mark.parametrize("path", MANIFESTS, ids=_IDS)
def test_builder_ownership_does_not_overlap(path: Path):
    data = _load(path)
    owned: list[tuple[str, str]] = []
    for s in data["sessions"]:
        for p in s.get("owns", []) or []:
            owned.append((s["id"], p.rstrip("/")))
    for i, (sid_a, pa) in enumerate(owned):
        for sid_b, pb in owned[i + 1 :]:
            if sid_a == sid_b:
                continue
            same = pa == pb or pa.startswith(pb + "/") or pb.startswith(pa + "/")
            assert not same, f"{sid_a} and {sid_b} both own {pa!r} / {pb!r}"


@pytest.mark.parametrize("path", MANIFESTS, ids=_IDS)
def test_shared_contracts_are_named_and_owned_by_one_session(path: Path):
    data = _load(path)
    sessions = {s["id"] for s in data["sessions"]}
    for c in data.get("shared_contracts", []) or []:
        assert (
            c["owner"] in sessions
        ), f"shared contract {c['name']!r} owned by unknown {c['owner']!r}"
        assert c.get("consumers"), f"shared contract {c['name']!r} lists no consumers"
        for consumer in c["consumers"]:
            assert (
                consumer in sessions
            ), f"shared contract {c['name']!r}: unknown consumer {consumer!r}"
