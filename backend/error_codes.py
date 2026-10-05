"""Stable machine codes for errors that are still raised as plain English text.

The preferred way to fail is ``AppError(code, english, status_code=…, params=…)``
(``backend/errors.py``). Plenty of older raise sites still do
``HTTPException(status, "English sentence")`` or let a service ``ValueError``
travel up through ``HTTPException(detail=str(e))``. The user must not read
those English sentences, and a frontend that string-matches prose is brittle.

This module is the single bridge: ``describe_http_error(detail)`` maps the
well-known sentences to ``(code, params)``. The HTTPException
handler in ``main.py`` adds them to the response as ``error_code`` /
``error_params`` next to the untouched ``detail``, so API consumers keep the
English text they already parse and the web app renders ``errors.<code>`` in
the user's language.

Every code returned here MUST have ``errors.<code>`` in the es and en
catalogues; ``backend/scripts/check_error_codes.py`` (run by
``test_error_codes_translated.py``) fails the suite otherwise.

Params come from the sentence's named groups, so numbers stay numbers in the
translation instead of being baked into prose.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# (pattern, code). Patterns are matched with ``fullmatch`` against the detail
# string, so a sentence that merely CONTAINS one of these does not match.
_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.DOTALL), c) for p, c in [
        # ── Authentication / authorization ───────────────────────────────────
        (r"API key is invalid or expired", "api_key_invalid"),
        # FastAPI's own answers (no credential, unknown route, wrong verb) and
        # the per-key rate limit: sentences with a fixed shape that carried no
        # code, so a client could only branch on the status number.
        (r"Not authenticated", "unauthenticated"),
        (r"Not Found", "not_found"),
        (r"Method Not Allowed", "method_not_allowed"),
        (r"Rate limit exceeded: \d+ requests per minute per API key, .*", "rate_limited"),
        (r"Token expired", "token_expired"),
        (r"Invalid token( type)?(: .*)?", "token_invalid"),
        (r"Token has been revoked", "token_revoked"),
        (r"Access denied", "access_denied"),
        (r"API key not found", "api_key_not_found"),
        # ── Not found ────────────────────────────────────────────────────────
        (r"Session not found", "session_not_found"),
        (r"Session .+ not found", "session_not_found"),
        (r"Chat not found", "chat_not_found"),
        (r"Document not found", "document_not_found"),
        (r"File not found on disk", "document_file_missing"),
        (r"Artifact not found", "artifact_not_found"),
        (r"Webhook not found", "webhook_not_found"),
        (r"Tenant not found.*", "tenant_not_found"),
        (r"Transfer not found", "transfer_not_found"),
        (r"User not found", "user_not_found"),
        (r"Warehouse '(?P<warehouse>.+)' not found", "warehouse_not_found"),
        # ── Uploads ──────────────────────────────────────────────────────────
        (r"No filename provided", "file_name_missing"),
        (r"File is empty .*", "file_empty"),
        (r"Unsupported file type '\.(?P<ext>.*)'\. Allowed: .*", "file_type_unsupported"),
        (r"File type '(?P<ext>.*)' not supported\. Allowed: .*", "file_type_unsupported"),
        (r"File too large \((?P<size_mb>\d+) MB\)\. Max (?P<max_mb>\d+) MB\.", "file_too_large"),
        (r"File size (?P<size_mb>[\d.]+) MB exceeds limit of (?P<max_mb>\d+) MB",
         "file_too_large"),
        # ── Sessions / training / analyst ────────────────────────────────────
        (r"Session must be COMPLETED to query the analyst", "analyst_session_not_completed"),
        (r"Invalid state transition: (?P<current>\w+) → (?P<target>\w+)\..*",
         "session_invalid_transition"),
        (r"Too many active training jobs \((?P<active>\d+)\)\..*", "too_many_active_jobs"),
        (r"Demo dataset not bundled on this server", "demo_dataset_unavailable"),
        (r"'question' (field )?is required", "question_required"),
        (r"'question' exceeds maximum length of (?P<max>\d+) characters", "question_too_long"),
        (r"Rate limit exceeded: max (?P<max>\d+) messages per (?P<window>\d+)s .*",
         "chat_rate_limited"),
        (r"(ML library|forecasting_core) not available", "ml_library_unavailable"),
        (r"(Inspection|Analysis|Quality check) failed.*", "analysis_failed"),
        (r"Could not load dataset.*", "dataset_load_failed"),
        (r"AI generation failed", "ai_generation_failed"),
        # ── Purchase orders / inventory / transfers ──────────────────────────
        (r"No valid fields to update", "no_valid_fields"),
        (r"A SKU cannot be its own component", "bom_self_component"),
        (r"multiplier must be greater than 0", "multiplier_must_be_positive"),
        (r"Origin and destination warehouses must differ", "transfer_same_warehouse"),
        (r"Origin and destination warehouses are required", "transfer_warehouses_required"),
        (r"A transfer needs at least one item", "transfer_no_items"),
        (r"Every transfer line needs a SKU", "transfer_line_sku_required"),
        (r"Quantity for '(?P<sku>.+)' must be positive", "transfer_qty_must_be_positive"),
        (r"Insufficient stock of '(?P<sku>.+)' in '(?P<warehouse>.+)' "
         r"\((?P<available>[\d.]+) available, (?P<requested>[\d.]+) requested\)",
         "transfer_insufficient_stock"),
        (r"Insufficient stock of '(?P<sku>.+)' in '(?P<warehouse>.+)' .*",
         "transfer_insufficient_stock_concurrent"),
        (r"This transfer cannot be received \(status: (?P<status>\w+)\)",
         "transfer_not_receivable"),
        (r"Nothing to receive", "transfer_nothing_to_receive"),
        (r"SKU '(?P<sku>.+)' is not part of this transfer", "transfer_sku_not_in_transfer"),
        (r"'(?P<sku>.+)': receiving (?P<qty>[\d.]+) but only (?P<outstanding>[\d.]+) outstanding",
         "transfer_over_outstanding"),
        (r"Only in-transit transfers with nothing received can be cancelled",
         "transfer_not_cancellable"),
        # ── Users / preferences / messages ───────────────────────────────────
        (r"Invalid role\. Options: .*", "role_invalid"),
        (r"Invalid status\. Options: .*", "status_invalid"),
        (r"Invalid language\. Options: .*", "language_invalid"),
        (r"Invalid theme\. Options: .*", "theme_invalid"),
        (r"Message body is empty", "message_empty"),
        (r"Cannot send a message to yourself", "message_to_self"),
        (r"Recipient is not active", "message_recipient_inactive"),
        (r"Confirmation required: .*", "confirmation_required"),
        # ── Reports ──────────────────────────────────────────────────────────
        (r"Invalid report type\. Options: .*", "report_type_invalid"),
        (r"(formats must be a non-empty list|Unknown format.*)", "report_format_invalid"),
    ]
]

# Entitlement guards raise ``detail={"code": "PLAN_LIMIT_REACHED", …}`` and the
# HTTPException handler in ``main.py`` lifts that to ``error_code`` UNCHANGED:
# the upper-case code is part of the public contract (docs at /desarrolladores,
# the upgrade dialog). The web app derives the catalogue key from it with
# ``errors.<code lower-cased>``, and for a ceiling one key per limit
# (``errors.plan_limit_<limit>``) so each reads as its own sentence. Those keys
# are listed here so the translation check covers them.
PLAN_LIMIT_KEYS = (
    "max_skus", "max_users", "max_locations", "max_sessions",
    "max_concurrent_jobs", "max_dataset_size_mb",
)
_STRUCTURED_CODES = ("plan_upgrade_required", "trial_expired")


def all_bridge_codes() -> set[str]:
    """Every catalogue key suffix this module (and its frontend derivation) needs."""
    codes = {code for _, code in _RULES}
    codes.update(_STRUCTURED_CODES)
    codes.update(f"plan_limit_{key}" for key in PLAN_LIMIT_KEYS)
    return codes


def describe_http_error(detail: Any) -> Optional[tuple[str, dict[str, Any]]]:
    """Return ``(code, params)`` for a plain-sentence HTTPException ``detail``.

    Dict details are not handled here — they already carry their own code.
    """
    if isinstance(detail, str):
        for pattern, code in _RULES:
            m = pattern.fullmatch(detail)
            if m:
                return code, {k: v for k, v in m.groupdict().items() if v is not None}
    return None
