"""The single source of truth for every knob this deployment has.

Before this module the answer to "what does StockAI need to run, and what stops
working without it?" was spread across four places that disagreed:

  - `backend/config.py`      — 45 Settings fields, the real list
  - `backend/.env.example`   — documented 20 of them
  - each consumer            — its own `if not settings.x` guard, with its own
                               idea of what "not configured" means
  - nowhere                  — what a missing key actually costs the user

Everything downstream is generated from the descriptors below: the admin
panel's fields, the status endpoint, `.env.example`, `docs/configuration.md`,
and the test that fails when a new Setting is added without documenting it.
Add a Setting and forget this file and `test_registry_covers_every_setting`
turns red — which is the point.

Two rules the descriptors encode, because they are the difference between a
service that is off and a service that is broken:

  1. **`required` is what makes the service run at all.** A service whose
     required fields are all present is `configured`; one with any missing is
     `not_configured`, and the status names the exact variables. It is never a
     crash and never a 500 — the feature says it is off, and why.
  2. **`what_breaks` is written for the person who has to decide.** Not "LLM
     disabled" but which screens go quiet. It is the text the panel shows next
     to a service that is off.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FieldKind = Literal["str", "int", "float", "bool", "list"]
ServiceKind = Literal["external", "deployment", "core"]


@dataclass(frozen=True)
class ConfigField:
    """One environment variable / one `Settings` attribute."""

    key: str
    """Attribute name on `backend.config.Settings` (e.g. `deepseek_api_key`)."""

    env: str
    """The environment variable that sets it (e.g. `DEEPSEEK_API_KEY`)."""

    doc: str
    """What it does — English, one or two sentences, written for a buyer."""

    kind: FieldKind = "str"
    secret: bool = False
    """Never returned in cleartext by the API; the panel shows a masked hint."""

    required: bool = False
    """The service does not run without it."""

    editable: bool = True
    """Settable from the admin panel. False = environment only, and the panel
    says so instead of offering a field that would not take effect."""

    instance_only: bool = False
    """Editable by the operator, but NEVER per tenant — even on a service that
    is otherwise tenant-scoped.

    The case it exists for: `WHATSAPP_WEBHOOK_BASE_URL` sits on the WhatsApp
    service, which a tenant may configure with its own sender. But the inbound
    webhook has to verify Twilio's signature BEFORE it knows which tenant the
    message belongs to, so it can only ever read the instance value. Offering
    the field per tenant would store a setting that is displayed as in effect
    and read by nothing — the exact silent no-op this whole layer exists to
    make impossible."""

    default: str = ""
    """The default `Settings` carries, rendered for documentation."""

    example: str = ""
    """A shape-correct sample for `.env.example` — never a real credential."""


@dataclass(frozen=True)
class Service:
    """A capability of the product that can be on, off, or failing."""

    key: str
    kind: ServiceKind
    summary: str
    """What the service is — English, one line."""

    what_breaks: str
    """What the user loses while it is off. Shown by the panel."""

    fields: tuple[ConfigField, ...] = ()

    requires_any: tuple[tuple[str, ...], ...] = ()
    """Alternative ways to be configured — ANY one group, fully present, is
    enough. `required` is an AND across fields; this is the OR that some
    services genuinely are, and leaving it out is not a modelling nicety.

    Email is the case that proves it: it runs on a Resend key OR on an SMTP
    user and password. With no way to say that, no field could be marked
    `required` without lying about the other path — so nothing was, and the
    service reported itself `ready` on a deployment where mail could not leave
    at all. The most expensive kind of wrong: the one that looks fine.

    Groups are listed cheapest-first; the status names the first unsatisfied
    one as the missing piece and carries the rest as alternatives."""

    borrows: tuple[str, ...] = ()
    """Field keys owned by ANOTHER service that this one also needs. Twilio's
    account SID is one credential feeding two channels; duplicating it would
    give the panel two inputs that must agree, and they would eventually not."""

    editable: bool = True
    """Whether the panel may write an instance-level override at all."""

    tenant_scoped: bool = False
    """Whether a single tenant may override it. True only for the channels that
    carry a tenant's own identity to its own people — a tenant sending from its
    own WhatsApp number is a real need; a tenant with its own LLM key is not,
    and every extra scope is another place a secret can leak across a border."""

    probe: str = ""
    """Name of the function in `probes.py` that tests the credentials live.
    Empty means there is nothing to reach — the service is local config."""

    docs_note: str = ""
    """Anything a reader of the source needs that the fields do not say."""

    switch: str = ""
    """Key of a bool field that turns the whole service off WITHOUT deleting its
    credentials. While it is false the service reports `off`, whatever else is
    configured — "the operator paused it" and "nobody set it up" are different
    statements and the panel has to be able to make both."""


# ─────────────────────────────────────────────────────────────────────────────
# Core — the process does not start without these, and the panel never
# offers to edit them. A UI that could rewrite its own database URL is a UI
# that can lock everyone out of the database.
# ─────────────────────────────────────────────────────────────────────────────

CORE = Service(
    key="core",
    kind="core",
    editable=False,
    summary="Process identity, database and signing key.",
    what_breaks="Nothing starts. These are read once at boot.",
    docs_note=(
        "Environment only, on purpose: these are read at import time by the "
        "connection pool and the JWT signer, so a value written from the panel "
        "would not take effect until a restart — and a wrong one would prevent "
        "the restart from succeeding."
    ),
    fields=(
        ConfigField(
            key="secret_key", env="SECRET_KEY", required=True, secret=True,
            editable=False,
            doc="Signs every access and refresh token. Changing it logs everyone "
                "out immediately. Use a long random string, never a guessable one.",
            example="change-me-to-a-long-random-string",
        ),
        ConfigField(
            key="database_url", env="DATABASE_URL", required=True, secret=True,
            editable=False,
            doc="PostgreSQL connection string. The job queue IS a table in this "
                "database, so the worker and the API must point at the same one.",
            example="postgresql://postgres:password@localhost:5432/faro",
        ),
        ConfigField(
            key="frontend_url", env="FRONTEND_URL", required=True, editable=False,
            doc="Public base URL of the web app. Every link the backend emails "
                "(verification, password reset, invitation) is built from it, so "
                "a wrong value sends working mail to a dead address.",
            example="http://localhost:5000",
        ),
        ConfigField(
            key="allowed_origins", env="ALLOWED_ORIGINS", kind="list",
            editable=False,
            doc="CORS origins allowed to call the API from a browser. The "
                "frontend's own origin must be in the list or every call fails "
                "in the browser while working perfectly from curl.",
            default="http://localhost:3000, http://localhost:4000, http://localhost:5000",
            example="http://localhost:5000",
        ),
        ConfigField(
            key="instance_admin_emails", env="INSTANCE_ADMIN_EMAILS", kind="list",
            editable=False,
            doc="Comma-separated addresses allowed to see and edit this "
                "instance's service configuration. `admin` is a role inside a "
                "tenant, so it cannot grant deployment-wide access. Empty means "
                "nobody edits it from the app — the panel becomes read-only for "
                "a tenant's own channels and says why.",
            default="", example="you@yourcompany.com",
        ),
        ConfigField(
            key="environment", env="ENVIRONMENT", editable=False,
            doc="development | staging | production. In production the server "
                "REFUSES to boot with TESTING_MODE=true.",
            default="development", example="development",
        ),
        ConfigField(
            key="app_name", env="APP_NAME", editable=False,
            doc="Product name in email subjects and the API's OpenAPI title.",
            default="ForecastPlatform", example="StockAI",
        ),
        ConfigField(
            key="app_version", env="APP_VERSION", editable=False,
            doc="Version string reported by /health and the OpenAPI document.",
            default="1.0.0", example="1.0.0",
        ),
        ConfigField(
            key="access_token_expire_minutes", env="ACCESS_TOKEN_EXPIRE_MINUTES",
            kind="int", editable=False,
            doc="Access-token lifetime. The frontend refreshes silently, so this "
                "is a security window, not a UX one.",
            default="15", example="15",
        ),
        ConfigField(
            key="algorithm", env="ALGORITHM", editable=False,
            doc="JWT signing algorithm. Leave it at HS256 unless you are also "
                "changing the key material.",
            default="HS256", example="HS256",
        ),
        ConfigField(
            key="storage_path", env="STORAGE_PATH", editable=False,
            doc="Directory holding uploaded datasets, model artifacts and "
                "documents. Postgres holds the metadata; these are the bytes. "
                "It is NOT in the database backup — back it up separately.",
            default="backend/storage", example="./storage",
        ),
        ConfigField(
            key="testing_mode", env="TESTING_MODE", kind="bool", editable=False,
            doc="Bypasses ALL quotas, rate limits and upload caps. For load and "
                "functional testing only. The server refuses to boot with this "
                "on when ENVIRONMENT=production.",
            default="false", example="false",
        ),
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# External services — every one of these is optional, and every one degrades
# to a stated, visible "off" rather than an error.
# ─────────────────────────────────────────────────────────────────────────────

LLM = Service(
    key="llm",
    kind="external",
    probe="probe_llm",
    summary="DeepSeek — the only LLM backend. Powers every AI feature.",
    what_breaks=(
        "The assistant stops answering, the morning narrative and the inventory "
        "insight fall back to their rule-based text, the RAG analyst goes off, "
        "and the data-quality diagnosis reports only its deterministic checks. "
        "Nothing errors: each surface says the assistant is unavailable."
    ),
    docs_note=(
        "There is exactly one provider and no fallback chain. A previous "
        "version picked whichever key happened to be set, which meant a typo in "
        "the variable name produced working AI features answered by a different "
        "vendor — visible only on the invoice."
    ),
    fields=(
        ConfigField(
            key="deepseek_api_key", env="DEEPSEEK_API_KEY",
            required=True, secret=True,
            doc="DeepSeek API key. Without it every AI feature reports itself "
                "unavailable instead of answering from somewhere else.",
            example="sk-...",
        ),
        ConfigField(
            key="deepseek_model", env="DEEPSEEK_MODEL",
            doc="'deepseek-chat' is the cheap general model. 'deepseek-reasoner' "
                "costs more and returns its reasoning separately.",
            default="deepseek-chat", example="deepseek-chat",
        ),
        ConfigField(
            key="deepseek_base_url", env="DEEPSEEK_BASE_URL",
            doc="API base. Point it at a compatible gateway to route through "
                "your own proxy — the wire format is OpenAI-shaped.",
            default="https://api.deepseek.com", example="https://api.deepseek.com",
        ),
    ),
)


EMAIL = Service(
    key="email",
    requires_any=(("resend_api_key",), ("smtp_user", "smtp_pass")),
    kind="external",
    probe="probe_email",
    tenant_scoped=True,
    summary="Transactional email — Resend first, SMTP as the fallback.",
    what_breaks=(
        "No email leaves: account verification, password reset, invitations, "
        "the daily inventory digest, the monthly ROI recap and purchase orders "
        "sent to suppliers. Each send is logged and reported as not delivered — "
        "the app never claims it mailed something it did not."
    ),
    docs_note=(
        "Resend wins when its key is present; SMTP is used otherwise. With "
        "neither, `_transport_send` RAISES rather than returning quietly: a "
        "silent no-op here made callers report invitations as sent that nobody "
        "ever received.\n\n"
        "A tenant override applies ONLY to mail addressed to that tenant's own "
        "people and its suppliers. Account-lifecycle mail — verification, "
        "password reset, invitations — always uses the instance transport, "
        "because a tenant must not be able to send the message that grants "
        "access to an account."
    ),
    fields=(
        ConfigField(
            key="resend_api_key", env="RESEND_API_KEY", secret=True,
            doc="Resend API key. When set, Resend is the transport and SMTP is "
                "not consulted.",
            example="re_...",
        ),
        ConfigField(
            key="email_from", env="EMAIL_FROM",
            doc="Sender shown to the recipient. 'onboarding@resend.dev' works "
                "without domain verification and is fine for a trial; a real "
                "deployment should send from its own verified domain.",
            default="StockAI <onboarding@resend.dev>",
            example="StockAI <onboarding@resend.dev>",
        ),
        ConfigField(
            key="smtp_server", env="SMTP_SERVER",
            doc="SMTP host for the fallback transport.",
            default="smtp.gmail.com", example="smtp.gmail.com",
        ),
        ConfigField(
            key="smtp_port", env="SMTP_PORT", kind="int",
            doc="SMTP port. 587 is STARTTLS, which is what the client uses.",
            default="587", example="587",
        ),
        ConfigField(
            key="smtp_user", env="SMTP_USER",
            doc="SMTP username; also the From address on the SMTP path.",
            example="you@example.com",
        ),
        ConfigField(
            key="smtp_pass", env="SMTP_PASS", secret=True,
            doc="SMTP password. For Gmail this is an app password, not the "
                "account password.",
            example="your-app-password",
        ),
    ),
)


WHATSAPP = Service(
    key="whatsapp",
    kind="external",
    probe="probe_twilio",
    tenant_scoped=True,
    summary=(
        "WhatsApp via Twilio — supplier messages on every plan; the daily "
        "alerts and the bot only for tenants on the Full or Corporate plan."
    ),
    what_breaks=(
        "No WhatsApp message is sent or received: the daily inventory alert "
        "loses its WhatsApp channel (email still goes if configured), purchase "
        "orders cannot be sent to a supplier over WhatsApp, and the "
        "conversational bot never replies because the webhook has nothing to "
        "answer with."
    ),
    docs_note=(
        "`WHATSAPP_WEBHOOK_BASE_URL` is the one that looks optional and is not, "
        "once the bot is in use. Twilio signs the inbound webhook over the "
        "PUBLIC url; behind a proxy the backend sees an internal one, the "
        "signature never matches, and every inbound message is rejected with "
        "403. Empty falls back to X-Forwarded-* headers, then to request.url."
    ),
    fields=(
        ConfigField(
            key="twilio_account_sid", env="TWILIO_ACCOUNT_SID",
            required=True, secret=True,
            doc="Twilio account SID. Shared with the SMS channel.",
            example="AC...",
        ),
        ConfigField(
            key="twilio_auth_token", env="TWILIO_AUTH_TOKEN",
            required=True, secret=True,
            doc="Twilio auth token. Also the key Twilio signs inbound webhooks "
                "with, so an outdated value rejects incoming messages as well "
                "as failing to send.",
            example="...",
        ),
        ConfigField(
            key="twilio_whatsapp_from", env="TWILIO_WHATSAPP_FROM", required=True,
            doc="Sender, WITH the 'whatsapp:' prefix. Twilio's sandbox number "
                "works for testing and only reaches people who joined the "
                "sandbox.",
            example="whatsapp:+14155238886",
        ),
        ConfigField(
            key="whatsapp_webhook_base_url", env="WHATSAPP_WEBHOOK_BASE_URL",
            instance_only=True,
            doc="Public scheme+host Twilio POSTs to, used to rebuild the signed "
                "url behind a proxy. Set it whenever the bot runs behind TLS "
                "termination or the frontend proxy.",
            example="https://app.example.com",
        ),
        ConfigField(
            key="whatsapp_bot_generic_mode", env="WHATSAPP_BOT_GENERIC_MODE",
            kind="bool",
            doc="When true the bot skips the LLM and replies with a fast, honest "
                "generic message. Confirmations still execute deterministically. "
                "A stopgap for when no LLM is funded.",
            default="false", example="false",
        ),
    ),
)


SMS = Service(
    key="sms",
    kind="external",
    probe="probe_twilio",
    tenant_scoped=True,
    borrows=("twilio_account_sid", "twilio_auth_token"),
    summary="SMS via Twilio — heads-up for team messages.",
    what_breaks=(
        "Team-message heads-ups are not texted. The message itself is still "
        "delivered in the app; only the SMS nudge is lost."
    ),
    docs_note=(
        "Shares the Twilio credentials with the WhatsApp service and adds one "
        "field: the sender. A 'whatsapp:'-prefixed number CANNOT send SMS, "
        "which is why this is a separate variable and not a reuse."
    ),
    fields=(
        ConfigField(
            key="twilio_sms_from", env="TWILIO_SMS_FROM", required=True,
            doc="Plain E.164 sender for SMS, without the 'whatsapp:' prefix.",
            example="+14155238886",
        ),
    ),
)


RAG = Service(
    key="rag",
    kind="external",
    probe="probe_rag",
    summary="Document search — Voyage AI embeddings over a Pinecone index.",
    what_breaks=(
        "Uploaded documents stop being indexed and the analyst can no longer "
        "cite them. The assistant still answers from the tenant's own data; it "
        "just has no documents to quote."
    ),
    docs_note=(
        "Needs the `voyageai` and `pinecone` packages installed as well as the "
        "keys — a missing package disables the service the same way a missing "
        "key does, and says which. The index must be 1024 dimensions, cosine "
        "metric, to match the embedding model."
    ),
    fields=(
        ConfigField(
            key="voyageai_api_key", env="VOYAGEAI_API_KEY",
            required=True, secret=True,
            doc="Voyage AI key, used to embed both documents and queries.",
            example="pa-...",
        ),
        ConfigField(
            key="pinecone_api_key", env="PINECONE_API_KEY",
            required=True, secret=True,
            doc="Pinecone API key for the vector store.",
            example="pcsk_...",
        ),
        ConfigField(
            key="pinecone_index", env="PINECONE_INDEX", required=True,
            doc="Index name. Must be 1024 dims, cosine, serverless.",
            example="stockai-documents",
        ),
        ConfigField(
            key="pinecone_environment", env="PINECONE_ENVIRONMENT",
            doc="Pinecone region. Informational for serverless indexes.",
            example="us-east-1-aws",
        ),
    ),
)


SECRET_STORAGE = Service(
    key="secret_storage",
    kind="core",
    # Nothing here is panel-editable, and the service has to say so: its one
    # field is the key that protects everything the panel writes, so a panel
    # able to rewrite it would make its own stored secrets unreadable. Left at
    # the default `True` the generated documentation said "Editable from the
    # panel: yes" above a table where every row reads "environment only".
    editable=False,
    probe="probe_secret_storage",
    summary="The Fernet key that encrypts every secret `/instalacion` stores.",
    what_breaks=(
        "Nothing, immediately — and that is the part worth knowing. With the "
        "variable unset the deployment GENERATES a key into "
        "`<STORAGE_PATH>/instance_secret.key`, so the panel keeps saving "
        "secrets; the state below reads 'not configured' because the variable "
        "is empty, not because the feature is off, and the panel says which "
        "key is in effect. What is lost is durability: that file must be backed "
        "up with `storage/`, and two processes on separate volumes generate "
        "DIFFERENT keys and cannot read each other's secrets. Only when no key "
        "can be written either — a read-only disk — does every secret field "
        "start refusing, which it does out loud rather than storing anything "
        "unencrypted."
    ),
    docs_note=(
        "`INTEGRATIONS_SECRET_KEY` is a Fernet key and is environment-only on "
        "purpose. It encrypts the secrets this very panel writes, so a panel "
        "that could rewrite it would make its own stored secrets unreadable "
        "with one click. Generate it with:\n\n"
        "    python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\"\n\n"
        "With it unset the deployment generates one into "
        "`<STORAGE_PATH>/instance_secret.key` on first use and logs a WARNING "
        "naming the file — which is what makes a virgin install usable. Two "
        "processes on different volumes then generate DIFFERENT keys and cannot "
        "read each other's secrets, so promote the generated value into this "
        "variable before splitting API and worker.\n\n"
        "Losing it means every stored secret must be re-entered. It belongs in "
        "your secret manager, not in a backup of the database it protects "
        "(`deploy/RESTORE.md` walks exactly that failure)."
    ),
    fields=(
        ConfigField(
            key="integrations_secret_key", env="INTEGRATIONS_SECRET_KEY",
            required=True, secret=True, editable=False,
            doc="Fernet key encrypting every secret written from the "
                "configuration panel. Environment only. The name is historical "
                "and kept on purpose: renaming it would silently orphan every "
                "existing deployment's stored secrets.",
            example="generate-with-fernet-generate-key",
        ),
    ),
)


CONTACT = Service(
    key="contact",
    requires_any=(("contact_whatsapp",), ("contact_email",)),
    kind="external",
    summary=(
        "How a customer reaches you to lift a plan's ceilings, to turn on "
        "the API, MCP and the WhatsApp bot, or to ask for a corporate quote."
    ),
    what_breaks=(
        "The 'write to us' buttons disappear. A tenant that hits a ceiling, or "
        "on the free plan wants the API, MCP or the bot, "
        "then has no way to ask for it. Unless online payments (the billing "
        "service) are configured, that is the entire commercial surface of the "
        "product — and corporate plans are only ever sold this way."
    ),
    docs_note=(
        "Empty channels are HIDDEN rather than shown broken: a button opening an "
        "empty wa.me link is worse than no button. Configure at least one."
    ),
    fields=(
        ConfigField(
            key="contact_whatsapp", env="CONTACT_WHATSAPP",
            doc="E.164 without the '+', the way wa.me wants it.",
            example="50688887777",
        ),
        ConfigField(
            key="contact_email", env="CONTACT_EMAIL",
            doc="Address the 'write to us' button opens.",
            example="hola@example.com",
        ),
        ConfigField(
            key="upgrade_notify_email", env="UPGRADE_NOTIFY_EMAIL",
            doc="Where in-app upgrade requests are emailed. Falls back to "
                "CONTACT_EMAIL when empty. The request is also stored in "
                "`upgrade_requests`, so a failed email never loses the ask.",
            example="ventas@example.com",
        ),
    ),
)


_BILLING_WEBHOOK = "<FRONTEND_URL>/api/v1/billing/{provider}/webhook"

BILLING = Service(
    key="billing",
    kind="external",
    # EITHER provider, complete, turns online payment on; the other simply is
    # not offered. A provider with any field missing is never offered: a
    # checkout we could sell but never confirm would take the money and leave
    # the customer on the free plan.
    requires_any=(
        ("stripe_secret_key", "stripe_webhook_secret", "stripe_price_id_full"),
        ("paypal_client_id", "paypal_client_secret", "paypal_webhook_id",
         "paypal_plan_id_full"),
    ),
    summary="Buy the Full plan online — Stripe (card) and PayPal, hosted pages only.",
    what_breaks=(
        "The 'Upgrade to Full' button and the billing section's checkout "
        "disappear; GET /billing/status says payments are off and names the "
        "variables to set. Everything else is unchanged: tenants reach you "
        "through the 'write to us' dialog and you set the tier by hand, as "
        "before. Subscriptions already sold keep their tier until their webhook "
        "secret is removed — then their renewals and cancellations stop being "
        "applied, which is why a configured provider should never be emptied "
        "while it has customers."
    ),
    docs_note=(
        "Hosted checkout only: no card number, CVC or PayPal password ever "
        "reaches this server or the app's JavaScript. The ONLY thing that "
        "changes a tenant's tier is a webhook whose signature was verified "
        "(Stripe: HMAC-SHA256 of the Stripe-Signature header, 5-minute "
        "tolerance; PayPal: the verify-webhook-signature API). Webhook URLs to "
        "register at each provider:\n\n"
        f"    {_BILLING_WEBHOOK.format(provider='stripe')}\n"
        f"    {_BILLING_WEBHOOK.format(provider='paypal')}\n\n"
        "Stripe events: checkout.session.completed, customer.subscription."
        "created / updated / deleted, invoice.paid, invoice.payment_failed. "
        "PayPal events: BILLING.SUBSCRIPTION.ACTIVATED / CANCELLED / SUSPENDED "
        "/ EXPIRED / PAYMENT.FAILED and PAYMENT.SALE.COMPLETED.\n\n"
        "Only the Full plan (`paid`) is sold, monthly; corporate is never "
        "purchasable. A past-due subscription keeps the plan for 7 days; a "
        "lapsed one moves the tenant to `free` and deletes nothing. A tenant "
        "whose `paid` tier was set by hand is never touched by billing."
    ),
    fields=(
        ConfigField(
            key="stripe_secret_key", env="STRIPE_SECRET_KEY", secret=True,
            doc="Stripe secret API key (sk_live_... or sk_test_... for test "
                "mode). Creates Checkout and Customer Portal sessions and reads "
                "subscriptions when their webhooks arrive.",
            example="sk_test_...",
        ),
        ConfigField(
            key="stripe_webhook_secret", env="STRIPE_WEBHOOK_SECRET", secret=True,
            doc="Signing secret of the Stripe webhook endpoint "
                + _BILLING_WEBHOOK.format(provider="stripe")
                + ". Without it no Stripe event can be verified, so none is "
                "applied.",
            example="whsec_...",
        ),
        ConfigField(
            key="stripe_price_id_full", env="STRIPE_PRICE_ID_FULL",
            doc="ID of the recurring MONTHLY Stripe Price of the Full plan. Its "
                "amount must equal BILLING_PRICE_USD_FULL.",
            example="price_...",
        ),
        ConfigField(
            key="paypal_client_id", env="PAYPAL_CLIENT_ID",
            doc="Client ID of the PayPal REST app (Developer Dashboard > Apps "
                "& Credentials), for the mode set in PAYPAL_MODE.",
            example="AY...",
        ),
        ConfigField(
            key="paypal_client_secret", env="PAYPAL_CLIENT_SECRET", secret=True,
            doc="Secret of that PayPal REST app.",
            example="EL...",
        ),
        ConfigField(
            key="paypal_webhook_id", env="PAYPAL_WEBHOOK_ID",
            doc="ID PayPal gives the webhook registered at "
                + _BILLING_WEBHOOK.format(provider="paypal")
                + ". Every event is verified against it with PayPal's "
                "verify-webhook-signature API.",
            example="1JE4291016473214C",
        ),
        ConfigField(
            key="paypal_plan_id_full", env="PAYPAL_PLAN_ID_FULL",
            doc="ID of the PayPal billing plan (monthly) of the Full plan. Its "
                "price must equal BILLING_PRICE_USD_FULL.",
            example="P-...",
        ),
        ConfigField(
            key="paypal_mode", env="PAYPAL_MODE",
            doc="'sandbox' or 'live'. Decides which PayPal API the credentials "
                "above belong to; any other value turns PayPal off.",
            default="sandbox", example="sandbox",
        ),
        ConfigField(
            key="billing_price_usd_full", env="BILLING_PRICE_USD_FULL", kind="float",
            doc="Monthly price of the Full plan in USD, as the app SHOWS it. "
                "What is charged is the Stripe Price / PayPal plan; keep them "
                "equal. The pricing page offers online purchase only while this "
                "matches its own figure.",
            default="59.0", example="59",
        ),
    ),
)


_CALLBACK = "<FRONTEND_URL>/api/v1/auth/oauth/{provider}/callback"

SOCIAL_LOGIN = Service(
    key="social_login",
    kind="external",
    probe="probe_social_login",
    switch="social_login_enabled",
    # ANY one provider, complete, is enough for the service to run; the others
    # simply do not get a button. Google first: the cheapest to set up.
    requires_any=(
        ("google_oauth_client_id", "google_oauth_client_secret"),
        ("microsoft_oauth_client_id", "microsoft_oauth_client_secret"),
        ("apple_oauth_service_id", "apple_oauth_team_id",
         "apple_oauth_key_id", "apple_oauth_private_key"),
    ),
    summary="Sign in with Google, Microsoft or Apple, next to email + password.",
    what_breaks=(
        "The 'Continue with Google / Microsoft / Apple' buttons disappear from "
        "the login and signup screens; email + password keeps working exactly "
        "as before. People who only ever signed in with a provider must use "
        "'forgot password' to set one. Off by default — a source install shows "
        "only the email form until the operator configures a provider AND "
        "turns SOCIAL_LOGIN_ENABLED on."
    ),
    docs_note=(
        "Each provider shows its button only when the master switch is on AND "
        "every one of its fields is set; a half-filled provider is never "
        "offered. Every provider console asks for the redirect (callback) URL, "
        "which is built from FRONTEND_URL:\n\n"
        f"    {_CALLBACK.format(provider='google')}\n"
        f"    {_CALLBACK.format(provider='microsoft')}\n"
        f"    {_CALLBACK.format(provider='apple')}\n\n"
        "Step-by-step console instructions: `docs/social-login.md`. An existing "
        "account is linked only through an email address the PROVIDER says it "
        "verified; when the local account had never verified that address, its "
        "password is removed on linking, because whoever chose it never proved "
        "they own the mailbox. Microsoft does not verify the `email` claim of "
        "work or school accounts, so there an address counts as verified only "
        "when Microsoft sends `xms_edov` (add it as an optional ID-token claim "
        "in the app registration) or `email_verified`; personal Microsoft "
        "accounts (outlook.com, hotmail.com, ...) are accepted because "
        "Microsoft verifies those addresses itself. Without that proof a "
        "Microsoft sign-in cannot create or link an account, only sign in an "
        "identity linked earlier."
    ),
    fields=(
        ConfigField(
            key="social_login_enabled", env="SOCIAL_LOGIN_ENABLED", kind="bool",
            doc="Master switch. False hides every social button without deleting "
                "the credentials below, so the feature can be paused and resumed.",
            default="false", example="false",
        ),
        ConfigField(
            key="google_oauth_client_id", env="GOOGLE_OAUTH_CLIENT_ID",
            doc="OAuth client ID of a 'Web application' client in Google Cloud "
                "Console. Authorized redirect URI: "
                + _CALLBACK.format(provider="google") + ".",
            example="1234567890-abc.apps.googleusercontent.com",
        ),
        ConfigField(
            key="google_oauth_client_secret", env="GOOGLE_OAUTH_CLIENT_SECRET",
            secret=True,
            doc="Client secret of that Google OAuth client. Without it (or the "
                "ID) the Google button is not shown.",
            example="GOCSPX-...",
        ),
        ConfigField(
            key="microsoft_oauth_client_id", env="MICROSOFT_OAUTH_CLIENT_ID",
            doc="Application (client) ID of a Microsoft Entra app registration "
                "whose supported account types are 'Accounts in any "
                "organizational directory and personal Microsoft accounts'. "
                "Web redirect URI: " + _CALLBACK.format(provider="microsoft")
                + ". Add the optional ID-token claim `xms_edov`, or work and "
                "school accounts cannot create or link accounts.",
            example="00000000-0000-0000-0000-000000000000",
        ),
        ConfigField(
            key="microsoft_oauth_client_secret", env="MICROSOFT_OAUTH_CLIENT_SECRET",
            secret=True,
            doc="Client secret VALUE (not the secret ID) of that app "
                "registration. It expires (24 months at most): renew it before "
                "then or the Microsoft button starts failing. Without it (or "
                "the ID) the Microsoft button is not shown.",
            example="abc8Q~...",
        ),
        ConfigField(
            key="apple_oauth_service_id", env="APPLE_OAUTH_SERVICE_ID",
            doc="Identifier of the Sign in with Apple SERVICES ID (not the App "
                "ID). Return URL: " + _CALLBACK.format(provider="apple")
                + ". Apple only accepts https return URLs.",
            example="es.stockai.signin",
        ),
        ConfigField(
            key="apple_oauth_team_id", env="APPLE_OAUTH_TEAM_ID",
            doc="10-character Apple Developer Team ID; the issuer of the client "
                "secret this server signs for every Apple sign-in.",
            example="ABCDE12345",
        ),
        ConfigField(
            key="apple_oauth_key_id", env="APPLE_OAUTH_KEY_ID",
            doc="Key ID of the Sign in with Apple private key (.p8).",
            example="XYZ987WXYZ",
        ),
        ConfigField(
            key="apple_oauth_private_key", env="APPLE_OAUTH_PRIVATE_KEY",
            secret=True,
            doc="Contents of the .p8 key file, BEGIN/END lines included. Pasted "
                "on one line is fine; the line breaks are restored. Without it "
                "Apple cannot be asked for a token and its button is not shown.",
            example="-----BEGIN PRIVATE KEY-----...-----END PRIVATE KEY-----",
        ),
    ),
)


INBOUND_EMAIL = Service(
    key="inbound_email",
    kind="deployment",
    editable=False,
    summary="Receive sales files forwarded by e-mail to a private per-account address.",
    what_breaks=(
        "The 'Sales by e-mail' card says this installation cannot receive e-mail "
        "yet, and POST /api/v1/inbound/email answers a structured "
        "`inbound_email_disabled` error. Everything else keeps working: files are "
        "still uploaded by hand."
    ),
    docs_note=(
        "Needs a mail provider that can forward inbound mail to a webhook "
        "(Postmark, Mailgun, Resend or any relay) and an MX record for the "
        "inbound domain. The webhook is authenticated by INBOUND_EMAIL_SECRET: "
        "either an `X-StockAI-Signature` HMAC header or HTTP Basic auth whose "
        "password is the secret. Step by step: `docs/inbound-email.md`."
    ),
    fields=(
        ConfigField(
            key="inbound_email_domain", env="INBOUND_EMAIL_DOMAIN",
            required=True, editable=False,
            doc="Domain the per-account addresses live on (sales+<token>@<domain>). "
                "Its MX record must point at your inbound mail provider.",
            example="in.example.com",
        ),
        ConfigField(
            key="inbound_email_secret", env="INBOUND_EMAIL_SECRET",
            required=True, secret=True, editable=False,
            doc="Shared secret that authenticates the provider's webhook calls. "
                "Use a long random string; changing it requires updating the "
                "provider's webhook settings too.",
            example="change-me-to-a-long-random-string",
        ),
    ),
)


ENTERPRISE_SSO = Service(
    key="enterprise_sso",
    kind="external",
    switch="enterprise_sso_enabled",
    # The one thing the feature cannot run without: the redirect URI and every
    # link back to the app are built from it.
    borrows=("frontend_url",),
    summary="Company sign-in through the customer's own OpenID Connect provider.",
    what_breaks=(
        "The 'Sign in with your company' option disappears from the login "
        "screen and tenant admins cannot configure a provider. Email + password "
        "keeps working for everyone, and any 'enforce SSO' setting a tenant "
        "already saved is suspended while this is off (so nobody is locked "
        "out). Off by default - a source install shows only the email form."
    ),
    docs_note=(
        "Each tenant admin configures their own provider (issuer URL, client "
        "id and secret, allowed e-mail domains) in the app; the secret is "
        "stored encrypted and so needs secret storage to be available. The "
        "redirect URI to register at the identity provider is built from "
        "FRONTEND_URL:\n\n"
        "    <FRONTEND_URL>/api/v1/auth/sso/callback\n\n"
        "People are created just-in-time inside the tenant that owns their "
        "e-mail domain, never as administrators. Only OpenID Connect is "
        "supported (no SAML)."
    ),
    fields=(
        ConfigField(
            key="enterprise_sso_enabled", env="ENTERPRISE_SSO_ENABLED", kind="bool",
            doc="Master switch for enterprise single sign-on. False hides the "
                "company sign-in option and suspends every tenant's provider "
                "and 'enforce SSO' setting without deleting them.",
            default="false", example="false",
        ),
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Deployment — reported, never editable from the panel. These decide the shape
# of the deployment, and a running process cannot change its own shape.
# ─────────────────────────────────────────────────────────────────────────────

WORKER = Service(
    key="worker",
    kind="deployment",
    editable=False,
    summary="Background training worker and the scheduled-job loops.",
    what_breaks=(
        "With the worker off, training sessions queue forever: they are accepted "
        "and never run. With the scheduler off, the 8:00 UTC inventory alert, "
        "the scheduled recalculations and the monthly snapshot never fire."
    ),
    docs_note=(
        "Both default to true so a bare `uvicorn backend.main:app` behaves like "
        "the single-process dev setup. In a split deployment the API container "
        "sets both false and one dedicated worker container runs the loops.\n\n"
        "SCHEDULER_ENABLED must be true in EXACTLY ONE instance. Two schedulers "
        "send every daily alert twice, and the second one is indistinguishable "
        "from a bug in the alert."
    ),
    fields=(
        ConfigField(
            key="worker_enabled", env="WORKER_ENABLED", kind="bool", editable=False,
            doc="Runs the job-claim/training loop in this process.",
            default="true", example="true",
        ),
        ConfigField(
            key="scheduler_enabled", env="SCHEDULER_ENABLED", kind="bool",
            editable=False,
            doc="Runs the cron loops: scheduled jobs, daily alerts, monthly "
                "snapshot. Exactly one instance may have this on.",
            default="true", example="true",
        ),
        ConfigField(
            key="worker_id", env="WORKER_ID", editable=False,
            doc="Identity used to claim jobs and to recover this instance's "
                "orphaned RUNNING jobs after a crash. Empty falls back to the "
                "host name — give a long-lived worker a FIXED id so its orphans "
                "are still recognised after the container is recreated.",
            example="worker-1",
        ),
        ConfigField(
            key="max_concurrent_jobs", env="MAX_CONCURRENT_JOBS", kind="int",
            editable=False,
            doc="Training jobs this worker runs at once.",
            default="2", example="2",
        ),
        ConfigField(
            key="worker_poll_interval_seconds", env="WORKER_POLL_INTERVAL_SECONDS",
            kind="float", editable=False,
            doc="Seconds between polls of the jobs table.",
            default="2.0", example="2.0",
        ),
    ),
)


LIMITS = Service(
    key="limits",
    kind="deployment",
    editable=False,
    summary="Size ceilings that protect memory and the database.",
    what_breaks=(
        "Nothing turns off. These are refusals, not features: exceeding one is "
        "always a stated rejection, never a silent truncation."
    ),
    docs_note=(
        "These are infrastructure ceilings and are NOT the commercial tier "
        "limits. Per-tenant allowances live in `backend/entitlements/plans.py`."
    ),
    fields=(
        ConfigField(
            key="max_upload_size_mb", env="MAX_UPLOAD_SIZE_MB", kind="int",
            editable=False,
            doc="Hard ceiling on an uploaded file, above the tier's own limit.",
            default="200", example="200",
        ),
        ConfigField(
            key="dataset_editor_max_rows", env="DATASET_EDITOR_MAX_ROWS",
            kind="int", editable=False,
            doc="Rows the in-app dataset editor will open. Checked from stored "
                "row_count BEFORE reading the file, so a huge file is never "
                "loaded into memory to find out it is too big.",
            default="50000", example="50000",
        ),
        ConfigField(
            key="dataset_editor_max_mb", env="DATASET_EDITOR_MAX_MB", kind="int",
            editable=False,
            doc="Same guard, by file size.",
            default="10", example="10",
        ),
        ConfigField(
            key="sql_materialize_max_rows", env="SQL_MATERIALIZE_MAX_ROWS",
            kind="int", editable=False,
            doc="Row ceiling when snapshotting a SQL data source into a CSV "
                "dataset. Exceeding it is a refusal, never a truncation.",
            default="500000", example="500000",
        ),
        ConfigField(
            key="accuracy_degradation_threshold_pct",
            env="ACCURACY_DEGRADATION_THRESHOLD_PCT", kind="float", editable=False,
            doc="How much worse (in percent, relative) a session's forecast must "
                "be doing against real sales than it did at training time before "
                "the app raises its one 'forecast is degrading' alert. 25 means "
                "a realised WAPE of 25% over the training WAPE (and at least 5 "
                "points worse); it is a notification only, nothing retrains by "
                "itself.",
            default="25.0", example="25.0",
        ),
        ConfigField(
            key="reforecast_full_refit_days", env="REFORECAST_FULL_REFIT_DAYS",
            kind="int", editable=False,
            doc="Age, in days, past which a scheduled retrain set to "
                "'re-forecast daily, refit periodically' stops re-forecasting "
                "from the stored models and trains them again. Younger than "
                "this, new sales only advance the forecast (seconds, no "
                "retraining); at or beyond it the models are refitted.",
            default="7", example="7",
        ),
    ),
)


API_SURFACE = Service(
    key="api_surface",
    kind="deployment",
    editable=False,
    summary="Public-API-only mode.",
    what_breaks=(
        "With it on, this instance serves ONLY the endpoints a customer's own "
        "system is invited to call, plus /health. The web app served from this "
        "host stops working entirely — which is the point: it is meant to run "
        "as a second instance of the same image."
    ),
    docs_note=(
        "What it does NOT buy: isolation from the database. Both instances "
        "still share one Postgres, so a database problem takes down the "
        "customer's integration and the app together."
    ),
    fields=(
        ConfigField(
            key="public_api_only", env="PUBLIC_API_ONLY", kind="bool",
            editable=False,
            doc="Serve only the public integration surface on this instance.",
            default="false", example="false",
        ),
    ),
)


OPERATIONS = Service(
    key="operations",
    kind="deployment",
    editable=False,
    summary="Thresholds behind the installation status panel (queue, worker, disk, backup, latency).",
    what_breaks=(
        "Nothing turns off. These decide when `GET /service-config/ops` and the "
        "'Installation status' panel call a reading degraded; they never block "
        "a request or a job."
    ),
    docs_note=(
        "These are the service-level objectives of the installation, in one "
        "place. Request latency is measured in the API process that answers "
        "`/service-config/ops` (an in-memory rolling window, lost on restart); "
        "queue, worker heartbeat and failed jobs are read from the database and "
        "therefore cover every process. The backup readings need the backup "
        "script's success marker to be visible to the API container: see "
        "`deploy/RESTORE.md`."
    ),
    fields=(
        ConfigField(
            key="ops_queue_wait_degraded_minutes",
            env="OPS_QUEUE_WAIT_DEGRADED_MINUTES", kind="float", editable=False,
            doc="A job that has been QUEUED longer than this many minutes makes "
                "the queue degraded.",
            default="10.0", example="10",
        ),
        ConfigField(
            key="ops_running_job_degraded_minutes",
            env="OPS_RUNNING_JOB_DEGRADED_MINUTES", kind="float", editable=False,
            doc="A job RUNNING longer than this many minutes is reported as "
                "possibly stuck.",
            default="180.0", example="180",
        ),
        ConfigField(
            key="ops_worker_heartbeat_stale_seconds",
            env="OPS_WORKER_HEARTBEAT_STALE_SECONDS", kind="float", editable=False,
            doc="The worker's last heartbeat older than this many seconds means "
                "nobody is claiming jobs.",
            default="120.0", example="120",
        ),
        ConfigField(
            key="ops_disk_free_min_percent",
            env="OPS_DISK_FREE_MIN_PERCENT", kind="float", editable=False,
            doc="Free space below this percentage on the storage or backup "
                "volume is degraded.",
            default="10.0", example="10",
        ),
        ConfigField(
            key="ops_backup_max_age_hours",
            env="OPS_BACKUP_MAX_AGE_HOURS", kind="float", editable=False,
            doc="The last successful backup older than this many hours is "
                "degraded. 36 tolerates one missed night.",
            default="36.0", example="36",
        ),
        ConfigField(
            key="ops_pool_saturation_percent",
            env="OPS_POOL_SATURATION_PERCENT", kind="float", editable=False,
            doc="Database connections in use, as a percentage of the pool, at "
                "or above which the pool is degraded.",
            default="85.0", example="85",
        ),
        ConfigField(
            key="ops_latency_slo_ms",
            env="OPS_LATENCY_SLO_MS", kind="float", editable=False,
            doc="A route family whose p95 latency (recent window) exceeds this "
                "many milliseconds is degraded.",
            default="3000.0", example="3000",
        ),
        ConfigField(
            key="ops_slow_query_ms",
            env="OPS_SLOW_QUERY_MS", kind="float", editable=False,
            doc="Statements slower than this many milliseconds are counted as "
                "slow queries (count and worst only; parameters are never kept).",
            default="1000.0", example="1000",
        ),
        ConfigField(
            key="backup_status_path",
            env="BACKUP_STATUS_PATH", editable=False,
            doc="Path, as the API container sees it, of the JSON success marker "
                "the nightly backup script writes. Empty means the backup "
                "readings report 'unknown'.",
            example="/backups/last_success.json",
        ),
        ConfigField(
            key="backup_dir",
            env="BACKUP_DIR", editable=False,
            doc="Folder the backups are written to, as the API container sees "
                "it; used to report its free disk space. Empty skips that "
                "reading.",
            example="/backups",
        ),
    ),
)


SQL_SOURCES = Service(
    key="sql_sources",
    kind="deployment",
    editable=False,
    summary="Connections to customers' own databases (SQL data sources).",
    what_breaks=(
        "Nothing turns off. These decide which network addresses a SQL data "
        "source may reach and how many connections one tenant may hold open "
        "at once; a refused address or a full set of slots is always a stated "
        "refusal on the screen, never a silent failure."
    ),
    docs_note=(
        "Hosted deployments keep private hosts refused: a tenant must not be "
        "able to point a 'database' at this server's own network. A "
        "self-hosted installation that connects to an ERP database on its "
        "LAN sets SQL_SOURCES_ALLOW_PRIVATE_HOSTS=true. Link-local and cloud "
        "metadata addresses (169.254.0.0/16, fe80::/10 and the known metadata "
        "IPs) are refused either way."
    ),
    fields=(
        ConfigField(
            key="sql_sources_allow_private_hosts",
            env="SQL_SOURCES_ALLOW_PRIVATE_HOSTS", kind="bool", editable=False,
            doc="Let SQL data sources connect to loopback and private-network "
                "addresses (RFC 1918, CGNAT, IPv6 unique-local). Off by default; "
                "turn it on only on a self-hosted installation whose databases "
                "live on its own network.",
            default="false", example="false",
        ),
        ConfigField(
            key="sql_sources_max_concurrent_per_tenant",
            env="SQL_SOURCES_MAX_CONCURRENT_PER_TENANT", kind="int", editable=False,
            doc="Connections one tenant may have open to its databases at the "
                "same time (tests, queries, exports, refreshes). The next one "
                "waits up to 10 seconds, then is refused with a clear message.",
            default="4", example="4",
        ),
    ),
)


SERVICES: tuple[Service, ...] = (
    CORE,
    LLM,
    EMAIL,
    WHATSAPP,
    SMS,
    RAG,
    SECRET_STORAGE,
    CONTACT,
    BILLING,
    SOCIAL_LOGIN,
    INBOUND_EMAIL,
    ENTERPRISE_SSO,
    WORKER,
    LIMITS,
    SQL_SOURCES,
    API_SURFACE,
    OPERATIONS,
)

BY_KEY: dict[str, Service] = {s.key: s for s in SERVICES}


def all_fields() -> dict[str, ConfigField]:
    """Every declared field, keyed by its `Settings` attribute name."""
    out: dict[str, ConfigField] = {}
    for service in SERVICES:
        for f in service.fields:
            out[f.key] = f
    return out


def field_owner(field_key: str) -> Service | None:
    """The service that declares `field_key` (the one whose panel edits it)."""
    for service in SERVICES:
        if any(f.key == field_key for f in service.fields):
            return service
    return None


def service_fields(service: Service) -> tuple[ConfigField, ...]:
    """A service's own fields plus the ones it borrows, in declaration order.

    The borrowed ones are what make `sms` honest: it cannot run without the
    Twilio credentials that `whatsapp` owns, so its status has to consider them
    even though its panel does not offer to edit them twice.
    """
    owned = tuple(service.fields)
    if not service.borrows:
        return owned
    registry = all_fields()
    borrowed = tuple(registry[k] for k in service.borrows if k in registry)
    return owned + borrowed


def editable_fields(
    service: Service, *, tenant_scope: bool = False
) -> tuple[ConfigField, ...]:
    """Fields the panel may write for this service, in the given scope.

    `tenant_scope=True` also drops the `instance_only` ones — a field a tenant
    could save and nothing would ever read.
    """
    if not service.editable:
        return ()
    fields = tuple(f for f in service.fields if f.editable)
    if tenant_scope:
        fields = tuple(f for f in fields if not f.instance_only)
    return fields


def required_fields(service: Service) -> tuple[ConfigField, ...]:
    """Fields without which the service does not run — its own and borrowed."""
    return tuple(f for f in service_fields(service) if f.required)


def requirement_groups(service: Service) -> tuple[tuple[ConfigField, ...], ...]:
    """The alternative ways to satisfy this service, as fields.

    Empty when the service has no OR in it — callers then fall back to
    `required_fields`, which is the AND.
    """
    registry = all_fields()
    groups = []
    for group in service.requires_any:
        fields = tuple(registry[k] for k in group if k in registry)
        if fields:
            groups.append(fields)
    return tuple(groups)
