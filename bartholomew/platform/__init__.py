"""
Platform control plane: authentication, authorisation and request identity.

S8 (authentication / network exposure). Deliberately a separate package from
`bartholomew.kernel` and from the API bridge:

* the kernel is one *personal* Bartholomew runtime and knows nothing about
  how many of them exist -- that property is what makes per-user isolation
  structural rather than a filtering convention;
* the control plane is shared, owns identity, and never holds personal
  memory.

Nothing here decides whether Bartholomew *may act*. Authentication answers
"who is asking", authorisation answers "what may this identity request", and
Governance -- unchanged, and downstream of both -- answers "may Bartholomew
actually do this". An authorised capability is still refused by the Parking
Brake, the consent gate and policy.
"""
