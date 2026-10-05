"""The limits and entitlements a tenant runs under.

**2026-10-05 owner decision (supersedes the "every tenant gets every feature"
rule below):** three things are paid-only — API access (`sk_live_*` keys), MCP
access and the WhatsApp bot. Everything else is still on every tier. They are
the boolean fields `api_access` / `mcp_access` / `whatsapp_bot` on `PlanDef`;
`free` and `demo` have none, `paid` (the "Full" plan, with LIMITED ceilings)
and `corporate` (every commercial ceiling lifted) have all three. The single
check is `entitlements.service.ensure_feature`, which raises the structured
403 `plan_feature_locked`. The historical text that follows describes the
older, simpler model; read it with that exception in mind.

One product, two ceilings (historical).

StockAI shipped three tiers once — starter / professional / enterprise — each with
its own feature set, and a good part of the product was spent telling people
what they could not use. That is not what this is. **Every tenant gets every
feature**, on both tiers: the same screens, the same forecasting, the same
assistant, the same public API. What differs is only *how much* of it fits.

- `free` is a real, permanent home for a small operation, not a countdown. It
  is deliberately narrow: a distributor who grows past a hundred SKUs, a second
  warehouse or a third teammate has outgrown it, and that is the moment we want
  a conversation.
- `paid` lifts every commercial ceiling. What stays is **infrastructure**:
  numbers that protect the server, identical on both tiers.

There is no checkout. A tenant moves to `paid` because somebody talked to us
and we set `tenants.tier`. That is the whole billing system, on purpose.

- `demo` is the throwaway account the landing hands out to somebody who wants
  to look before talking to us (`backend/trial/`). Same features again — the
  ceilings are just small enough that nobody runs a business on it, and it is
  erased 24 hours after it was created. Nobody signs up into it and nobody is
  moved to it by hand.

`None` means unlimited.
"""

from dataclasses import dataclass

FREE = "free"
PAID = "paid"
CORPORATE = "corporate"
DEMO = "demo"


@dataclass(frozen=True)
class PlanDef:
    max_skus: int | None
    max_users: int | None
    max_locations: int | None
    max_sessions: int | None
    max_api_keys: int | None
    max_api_calls_per_day: int | None
    max_concurrent_jobs: int | None
    max_dataset_size_mb: int | None
    # Training launches per calendar day in the TENANT's timezone (a launch is
    # one run of the models: a family fan-out counts once; back-tests and
    # re-forecasts that only reload stored models do not count). The cost
    # ceiling behind the "Full" plan: training is the one expensive thing a
    # tenant can do on our hardware.
    max_trainings_per_day: int | None
    # Paid-only features (booleans, never None). Deliberately the LAST fields:
    # `service._LIMIT_FIELDS` is every field up to here, so these never leak
    # into the `limits` map the frontend already reads.
    api_access: bool = False
    mcp_access: bool = False
    whatsapp_bot: bool = False


# The feature keys, as they appear in `plan_feature_locked` params and in the
# `features` map of GET /entitlements. Value: the PlanDef field that decides it.
FEATURE_FIELDS: dict[str, str] = {
    "api": "api_access",
    "mcp": "mcp_access",
    "whatsapp_bot": "whatsapp_bot",
}

# The cheapest tier that includes every feature above (error param
# `required_plan`).
FEATURE_REQUIRED_PLAN = PAID


# Infrastructure ceilings — the same on both tiers, because they are not for
# sale. A tenant training eight models at once is already using every worker
# thread there is, and the ninth job waiting in the queue is what keeps the
# ninth tenant's first job from waiting behind it.
_MAX_CONCURRENT_JOBS = 8

PLANS: dict[str, PlanDef] = {
    FREE: PlanDef(
        # A hundred SKUs runs a small shop's whole catalog, and stops being
        # enough the moment the catalog is a real distributor's.
        max_skus=100,
        # Two: the owner and one more. A team is the third person.
        max_users=2,
        # One warehouse. Multi-warehouse transfers, the optimizer's whole
        # reason to exist, need a second one.
        max_locations=1,
        max_sessions=3,
        # No API on the free tier (api_access False below), so no keys and no
        # calls: the ceilings say the same thing the entitlement does.
        max_api_keys=0,
        max_api_calls_per_day=0,
        max_concurrent_jobs=_MAX_CONCURRENT_JOBS,
        # 25 MB is roughly 4 years of daily sales over 100 SKUs — the history
        # that fits the SKU ceiling above, and no more.
        max_dataset_size_mb=25,
        # One training a day: enough to retrain after loading the day's sales.
        max_trainings_per_day=1,
    ),
    # The "Full" plan: first tier with API + MCP + the WhatsApp bot, and still
    # LIMITED. Proposed numbers, easy to edit. A mid-size distributor fits in
    # 1,000 SKUs (owner decision 2026-10-05, raised from 500); five seats and
    # three warehouses are a team with a few sites; twenty saved runs is a year
    # of monthly retrains for a few variants; 3 keys is ERP + BI + an AI client;
    # 2000 calls/day per key is a nightly push plus polling every minute during
    # business hours; 100 MB is ~5 years of daily sales over 500 SKUs; ten
    # trainings a day is a scheduled retrain plus several manual experiments.
    # Past this is `corporate`.
    PAID: PlanDef(
        max_skus=1000,
        max_users=5,
        max_locations=3,
        max_sessions=20,
        max_api_keys=3,
        max_api_calls_per_day=2000,
        max_concurrent_jobs=_MAX_CONCURRENT_JOBS,
        max_dataset_size_mb=100,
        max_trainings_per_day=10,
        api_access=True,
        mcp_access=True,
        whatsapp_bot=True,
    ),
    # Every commercial ceiling lifted (the old `paid`). Infrastructure stays.
    CORPORATE: PlanDef(
        max_skus=None,
        max_users=None,
        max_locations=None,
        max_sessions=None,
        max_api_keys=None,
        max_api_calls_per_day=None,
        max_concurrent_jobs=_MAX_CONCURRENT_JOBS,
        # The one number still bounded on a customer's own data, and it
        # stays because an upload is read into memory before it is anything
        # else. For scale: 3 years of daily sales over 5.000 SKUs is ~200 MB.
        max_dataset_size_mb=2000,
        # Unlimited: the corporate customer's demand is committed, and its
        # retrains are the point.
        max_trainings_per_day=None,
        api_access=True,
        mcp_access=True,
        whatsapp_bot=True,
    ),
    DEMO: PlanDef(
        # Room to walk every screen once, and nothing more. The bundled demo
        # history is 5 SKUs; 30 leaves space to upload a small sample of
        # one's own catalogue and see it forecast.
        max_skus=30,
        # The visitor alone. Inviting somebody is an email to a real address.
        max_users=1,
        # Two, so transfers and the network view can actually be tried.
        max_locations=2,
        # The seeded run plus one upload of their own.
        max_sessions=2,
        # A throwaway account has no API, MCP or bot (all False by default).
        max_api_keys=0,
        max_api_calls_per_day=0,
        # Lower than the infrastructure ceiling: a burst of visitors must not
        # take every worker thread from the tenants who run on this server.
        max_concurrent_jobs=1,
        max_dataset_size_mb=5,
        # The bundled demo run, once.
        max_trainings_per_day=1,
    ),
}

# What an unrecognised (or missing) `tenants.tier` resolves to. Free, never
# paid: a column that has drifted must not hand out an unlimited account.
DEFAULT_TIER = FREE
