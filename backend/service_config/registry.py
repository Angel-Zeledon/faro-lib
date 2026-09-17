"""The single source of truth for every knob this deployment has.

Before this module the answer to "what does Faro need to run, and what stops
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
            default="ForecastPlatform", example="Faro",
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
            default="Faro <onboarding@resend.dev>",
            example="Faro <onboarding@resend.dev>",
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
    summary="WhatsApp via Twilio — daily alerts, supplier messages and the bot.",
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
            example="faro-documents",
        ),
        ConfigField(
            key="pinecone_environment", env="PINECONE_ENVIRONMENT",
            doc="Pinecone region. Informational for serverless indexes.",
            example="us-east-1-aws",
        ),
    ),
)


INTEGRATIONS = Service(
    key="integrations",
    kind="external",
    probe="probe_integrations",
    summary="Accounting integrations — Alegra and Siigo.",
    what_breaks=(
        "No accounting connection can be created or synced: the credentials "
        "cannot be stored, because storing them unencrypted is not an option "
        "the code offers."
    ),
    docs_note=(
        "`INTEGRATIONS_SECRET_KEY` is a Fernet key and is environment-only on "
        "purpose. It encrypts every stored credential — including the ones this "
        "very panel writes — so a panel that could rewrite it would make its own "
        "stored secrets unreadable with one click. Generate it with:\n\n"
        "    python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\"\n\n"
        "Losing it means every stored credential must be re-entered. It belongs "
        "in your secret manager, not in a backup of the database it protects."
    ),
    fields=(
        ConfigField(
            key="integrations_secret_key", env="INTEGRATIONS_SECRET_KEY",
            required=True, secret=True, editable=False,
            doc="Fernet key encrypting every credential stored in the database — "
                "accounting connections and everything written from the "
                "configuration panel. Environment only.",
            example="generate-with-fernet-generate-key",
        ),
        ConfigField(
            key="alegra_base_url", env="ALEGRA_BASE_URL",
            doc="Alegra API base. Override only to point at a sandbox.",
            default="https://api.alegra.com/api/v1",
            example="https://api.alegra.com/api/v1",
        ),
        ConfigField(
            key="siigo_base_url", env="SIIGO_BASE_URL",
            doc="Siigo API base. Override only to point at a sandbox.",
            default="https://api.siigo.com/v1",
            example="https://api.siigo.com/v1",
        ),
    ),
)


CONTACT = Service(
    key="contact",
    requires_any=(("contact_whatsapp",), ("contact_email",)),
    kind="external",
    summary="How a customer reaches you to lift the free tier's ceilings.",
    what_breaks=(
        "The 'write to us' buttons disappear. A free tenant that hits a ceiling "
        "then has no way to ask for more room — which is the entire commercial "
        "surface of the product, since there is no checkout."
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
        "the scheduled recalculations, the integration sync and the monthly "
        "snapshot never fire."
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
            doc="Runs the cron loops: scheduled jobs, daily alerts, integration "
                "sync, monthly snapshot. Exactly one instance may have this on.",
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


SERVICES: tuple[Service, ...] = (
    CORE,
    LLM,
    EMAIL,
    WHATSAPP,
    SMS,
    RAG,
    INTEGRATIONS,
    CONTACT,
    WORKER,
    LIMITS,
    API_SURFACE,
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
