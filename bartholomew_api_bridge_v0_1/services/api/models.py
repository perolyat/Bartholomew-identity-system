from pydantic import BaseModel


class ChatIn(BaseModel):
    message: str


class ChatOut(BaseModel):
    reply: str
    tone: str | None = None
    emotion: str | None = None

    # WP-A2b / safety gate S2's provenance analogue. Set (True / the failure
    # text) only when the turn's reply was genuinely produced but its
    # required provenance record -- the chat Reflection, S5.3 Decision E.2's
    # explanation-grade context -- failed to persist. The reply itself is
    # real and is reported normally; these fields keep the response from
    # presenting as *full* success. None on every healthy turn, so clients
    # that ignore them are unaffected.
    audit_degraded: bool | None = None
    audit_error: str | None = None


class WaterLogIn(BaseModel):
    ml: int
    timestamp: str | None = None  # ISO8601


class WaterTodayOut(BaseModel):
    date: str
    total_ml: int


class ConversationItem(BaseModel):
    id: str
    timestamp: str
    role: str
    content: str


class ConversationList(BaseModel):
    items: list[ConversationItem]
