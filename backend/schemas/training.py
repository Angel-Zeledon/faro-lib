from pydantic import BaseModel
from typing import Optional, Dict, Any


class TrainRequest(BaseModel):
    """Optional overrides to apply before starting training."""
    model_override: Optional[Dict[str, Any]] = None
