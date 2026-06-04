from pydantic import BaseModel, EmailStr
from typing import Optional


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    tenant_name: str
    full_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordVerifyRequest(BaseModel):
    email: EmailStr
    code: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class VerifyEmailRequest(BaseModel):
    token: str


class InviteUserRequest(BaseModel):
    email: EmailStr
    role: str = "analyst"
    full_name: Optional[str] = None


# ── Admin user management ─────────────────────────────────────────────────

class CreateUserRequest(BaseModel):
    email: EmailStr
    role: str = "analyst"
    full_name: Optional[str] = None


class UpdateUserRequest(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[EmailStr] = None


class UpdateUserStatusRequest(BaseModel):
    status: str


class UpdatePermissionsRequest(BaseModel):
    permissions: list[str]
