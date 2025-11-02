from pydantic import BaseModel


class ChatIn(BaseModel):
    message: str


class ChatOut(BaseModel):
    reply: str
    tone: str | None = None
    emotion: str | None = None


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
