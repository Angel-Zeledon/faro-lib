from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth.guards import CurrentUser, get_current_user
from backend.preferences import service as pref_svc
from backend.schemas.common import ok

router = APIRouter(prefix="/me/preferences", tags=["preferences"])

_VALID_LANG  = {"es", "en"}
_VALID_THEME = {"dark", "light"}


class PreferencesUpdate(BaseModel):
    language: Optional[str] = None
    theme:    Optional[str] = None


@router.get("")
def get_preferences(user: CurrentUser = Depends(get_current_user)):
    return ok(pref_svc.get_preferences(user.tenant_id, user.user_id))


@router.patch("")
def update_preferences(body: PreferencesUpdate, user: CurrentUser = Depends(get_current_user)):
    if body.language is not None and body.language not in _VALID_LANG:
        raise HTTPException(400, f"Invalid language. Options: {sorted(_VALID_LANG)}")
    if body.theme is not None and body.theme not in _VALID_THEME:
        raise HTTPException(400, f"Invalid theme. Options: {sorted(_VALID_THEME)}")
    return ok(pref_svc.update_preferences(user.tenant_id, user.user_id, body.language, body.theme))
