"""
Governance persistence: the Parking Brake's schema and transition semantics.

Phase B, stage B3. See docs/PHASE_B_OVERVIEW.md's B3 scope and
docs/B3_IMPLEMENTATION.md. This package is NOT yet the runtime's shared
Parking Brake instance -- bartholomew.orchestrator.safety.parking_brake
remains the live implementation every real construction site uses until
B4 (shared Governance runtime integration) explicitly swaps it in. Building
and testing this in isolation first, without touching the live gate points,
is B3's own exit condition, not an oversight.
"""
