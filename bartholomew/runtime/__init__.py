"""Service lifecycle for Bartholomew (Session D).

Owns *how Bartholomew runs as a service*: the non-interactive entry point,
process supervision assumptions, and the truthful health surface that says
whether the runtime and its scheduler are actually alive.

Deliberately does NOT own authentication, principals, identity-to-runtime
resolution or per-user data isolation -- those belong to the authenticated
control plane and are consumed here through narrow injected interfaces
(`bartholomew_api_bridge_v0_1.services.api.routes.inbound`), never
reimplemented.
"""
