"""Session F: the seams that make Packages A-E one system rather than five.

Every module here is an *adapter*. None of them owns a policy, a store, an
authority or a vocabulary -- each one connects a consumer that declared a seam
to the producer that already owns the answer, in the direction the packages'
own closeouts specify:

* `device_registry`   -- E's enrolled-device state answers B's
                         `DeviceCapabilityRegistry` and C's
                         `DeviceCapabilityResolver`. One device truth.
* `multimodal_events` -- C's serialized envelopes enter A's canonical ingress
                         (`inbound_events`), which is the one event bus.
* `learning_adapters` -- D's constructed projections are replaced by E's real
                         sharing state and by honest "not measured" answers
                         where this wave measures nothing.
* `install`           -- the single startup call that puts the three above in
                         place, and the single place to look to find out
                         whether they are.

Nothing here widens a capability, mints a scope, adds a governance authority
or creates a second path around `runtime_contract.py`. Where a package's
frozen semantics and an adapter's convenience disagree, the frozen semantics
win and the adapter refuses.
"""
