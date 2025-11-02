# privacy_guard.py
SENSITIVE_KEYWORDS = [
    "name",
    "address",
    "location",
    "phone",
    "email",
    "bank",
    "password",
    "routine",
    "health",
    "private",
    "account",
]


def is_sensitive(text: str) -> bool:
    return any(keyword in text.lower() for keyword in SENSITIVE_KEYWORDS)


async def request_permission_to_store(text: str) -> bool:
    # TEMPORARY placeholder for user consent
    # Replace with UI or voice prompt later
    print(
        f'[Bartholomew] I detected something sensitive:\n"{text}"\n'
        "Do you want me to remember this? (yes/no)",
    )
    response = input("> ").strip().lower()
    return response in ("yes", "y")
