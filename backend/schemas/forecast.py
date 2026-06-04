from pydantic import BaseModel
from typing import Optional, Dict, Any


class PredictRequest(BaseModel):
    sku: str
    inputs: Dict[str, Any] = {}
    horizon: Optional[int] = None
