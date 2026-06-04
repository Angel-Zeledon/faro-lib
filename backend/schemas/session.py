from pydantic import BaseModel
from typing import Optional, List


class SessionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    tags: List[str] = []


class SessionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
