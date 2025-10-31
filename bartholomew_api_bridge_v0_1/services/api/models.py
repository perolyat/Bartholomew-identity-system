
from pydantic import BaseModel
from typing import Optional, List

class ChatIn(BaseModel):
    message: str

class ChatOut(BaseModel):
    reply: str
    tone: Optional[str] = None
    emotion: Optional[str] = None

class WaterLogIn(BaseModel):
    ml: int
    timestamp: Optional[str] = None  # ISO8601

class WaterTodayOut(BaseModel):
    date: str
    total_ml: int

class ConversationItem(BaseModel):
    id: str
    timestamp: str
    role: str
    content: str

class ConversationList(BaseModel):
    items: List[ConversationItem]
