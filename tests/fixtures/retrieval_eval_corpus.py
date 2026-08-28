"""A small, fixed corpus and query set for measuring retrieval behaviour.

Data only -- no assertions and no thresholds. The harness that runs it lives in
`bartholomew/kernel/retrieval_eval.py`.

**This fixture exists to be measured against, not to be passed.** Its purpose
is to produce evidence about how retrieval actually behaves before anyone
changes a relevance threshold. Tuning a constant until these cases go green
would destroy exactly the evidence the fixture is for: the numbers would then
describe the tuning, not the retrieval. If a case fails, that is a measurement.

The corpus deliberately spans three loosely-related domains (household/estate,
travel, personal schedule) so that competing-memory and cross-domain cases are
real rather than contrived, and it is small enough that every expectation can
be justified by reading it.
"""

from __future__ import annotations

#: `memory_id -> (kind, key, text)`. IDs are stable so results can be compared
#: across runs and across modes.
CORPUS: dict[int, tuple[str, str, str]] = {
    1: (
        "note",
        "boiler_quote",
        "The plumber quoted 2,400 for replacing the boiler in the utility room.",
    ),
    2: (
        "note",
        "heating_fault",
        "Central heating stops working when the outside temperature drops below zero.",
    ),
    3: ("note", "roof_survey", "Roof survey found two slipped slates above the kitchen extension."),
    4: (
        "note",
        "lisbon_flights",
        "Flights to Lisbon in March were cheapest with a midweek departure.",
    ),
    5: (
        "note",
        "lisbon_hotel",
        "The hotel near Alfama had the best reviews for a quiet night's sleep.",
    ),
    6: (
        "user_schedule",
        "passport",
        "Passport expires in November, so renew it before booking anything abroad.",
    ),
    7: (
        "user_profile",
        "music_pref",
        "Prefers jazz in the evening and silence while concentrating.",
    ),
    8: (
        "user_schedule",
        "guitar",
        "Guitar lessons moved to Thursday evenings from the start of term.",
    ),
    9: ("birthday", "anna", "Anna's birthday is on the fourteenth of June."),
    10: ("user_schedule", "car_insurance", "The car insurance renews on the second of September."),
    11: ("note", "weekly_shop", "The weekly shop is usually done on Saturday mornings."),
    12: (
        "user_schedule",
        "dentist",
        "Dentist appointment rescheduled to the following Tuesday at ten.",
    ),
}


#: Each case: (category, query, expected memory ids).
#:
#: An empty expectation means "nothing in this corpus is a correct answer" --
#: the irrelevant cases, where returning nothing is the right behaviour and
#: returning something confidently is the failure mode that matters.
CASES: list[tuple[str, str, tuple[int, ...]]] = [
    # Direct lexical overlap. The easy baseline: any working mode should get
    # these, and a mode that cannot is broken rather than merely weak.
    ("lexical", "boiler quote from the plumber", (1,)),
    ("lexical", "roof survey slipped slates", (3,)),
    # Paraphrase: same meaning, most content words changed.
    ("paraphrase", "how much did the plumber say the new hot water system would cost", (1,)),
    ("paraphrase", "which nights are the guitar sessions on now", (8,)),
    # Semantic with LOW lexical overlap. This is the case the deterministic
    # embedder cannot do and a real one should: no meaningful term is shared
    # with the target record, only the meaning is.
    ("semantic_low_overlap", "what did the heating engineer charge", (1,)),
    ("semantic_low_overlap", "somewhere peaceful to stay in Portugal", (5,)),
    # Partially related: the corpus contains something adjacent but not a
    # direct answer. Returning the adjacent record is acceptable; returning it
    # with high confidence as though it answered the question is not.
    ("partial", "problems with the house in winter", (2,)),
    ("partial", "getting to Lisbon cheaply", (4,)),
    # Competing memories: more than one record is a legitimate answer, and the
    # question is whether retrieval surfaces the right *set* rather than
    # committing to one arbitrarily.
    ("competing", "what do I need to renew and when", (6, 10)),
    ("competing", "what is happening on a Tuesday", (12,)),
    # Irrelevant: nothing here answers the question.
    ("irrelevant", "how should I train for a marathon", ()),
    ("irrelevant", "what is the capital of Peru", ()),
    # Sparse / weak queries: one or two words, little to work with.
    ("sparse", "slates", (3,)),
    ("sparse", "June", (9,)),
    ("sparse", "jazz", (7,)),
]
