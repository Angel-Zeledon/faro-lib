from pydantic import BaseModel, EmailStr, Field
from typing import Optional

# E.164 with country code, e.g. +50688887777 — same rule PATCH /users/me applies.
E164_PATTERN = r"\+[1-9]\d{7,14}"


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    tenant_name: str
    full_name: Optional[str] = None
    # Required since PENDIENTES #1: purchase orders are delivered to the
    # buyer's own WhatsApp for them to forward to their supplier.
    whatsapp_number: str = Field(pattern=E164_PATTERN)


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


class ResendVerificationRequest(BaseModel):
    email: EmailStr


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
