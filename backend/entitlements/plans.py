"""The limits a tenant runs under. One product, two ceilings.

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

`None` means unlimited.
"""

from dataclasses import dataclass

FREE = "free"
PAID = "paid"


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
        max_api_keys=1,
        # A nightly ERP push and the polling around it fits in 500. A live
        # integration that reads all day does not.
        max_api_calls_per_day=500,
        max_concurrent_jobs=_MAX_CONCURRENT_JOBS,
        # 25 MB is roughly 4 years of daily sales over 100 SKUs — the history
        # that fits the SKU ceiling above, and no more.
        max_dataset_size_mb=25,
    ),
    PAID: PlanDef(
        max_skus=None,
        max_users=None,
        max_locations=None,
        max_sessions=None,
        max_api_keys=None,
        max_api_calls_per_day=None,
        max_concurrent_jobs=_MAX_CONCURRENT_JOBS,
        # The one number still bounded on a paying customer's own data, and it
        # stays because an upload is read into memory before it is anything
        # else. For scale: 3 years of daily sales over 5.000 SKUs is ~200 MB.
        max_dataset_size_mb=2000,
    ),
}

# What an unrecognised (or missing) `tenants.tier` resolves to. Free, never
# paid: a column that has drifted must not hand out an unlimited account.
DEFAULT_TIER = FREE
