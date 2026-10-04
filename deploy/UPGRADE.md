# Upgrading StockAI, and going back

The property this whole procedure rests on: **migrations are additive**.
`backend/db/migrations.py` is `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT
EXISTS`, `CREATE INDEX IF NOT EXISTS` and one-time backfills guarded by "only
rows that predate the column".

That is what makes rollback cheap: **the old code runs against the new
schema**. It ignores columns it does not know about. You do not restore a
database to go back a version — you put the previous image back.

**One migration in the product's history breaks that, and it is named here
rather than discovered.** `drop_integration_connections` (2026-09-20) deletes
the table the Alegra and Siigo integrations used, because the feature was
removed and a table of encrypted third-party ERP credentials should not outlive
it. Consequences, stated plainly:

* Rolling back **across** that release to a version that still has the
  `/integraciones` screen leaves it reading a table that is gone. Take the
  backup first and restore it if you go back that far — putting the old image
  back is not enough for this one hop.
* Rolling back to any release **after** it is the usual cheap hop.
* If you had a connection configured, the credentials are gone with the table.
  They were never readable in plaintext, so there is nothing to migrate; the
  ERP account itself is untouched.

Two other things the additive property does not cover, both called out below: a
release that drops something on purpose, and data written in a shape the old
version cannot read.

---

## Upgrading

```sh
cd /opt/faro/deploy
git fetch --tags && git checkout v1.4.0     # or: pull the tagged image
docker compose -f docker-compose.prod.yml up -d --build
docker compose logs -f api | head -40
```

Migrations run **at startup**, inside an advisory lock, so several containers
booting at once cannot race. A migration that fails is logged at ERROR with its
SQLSTATE and the boot is aborted — the process does not come up on a half-built
schema.

### Before you press it

1. **Take the backup and know it restores.** `RESTORE.md`. An upgrade is the
   most likely moment to need it and the worst moment to discover the drill was
   never done.
2. **Read the release notes for a "breaking" line.** Everything else is
   additive by construction; a release that is not is labelled — and there is
   exactly one so far, named at the top of this file.
3. **One scheduler.** Unchanged by an upgrade, but worth re-checking after any
   compose edit: `SCHEDULER_ENABLED=true` in exactly one service.

### After

```sh
curl -s localhost:8000/health | jq '{status, version, database, services}'
```

* `status: "ok"` and `database: true`.
* `version` is the one you just deployed. If it still reads the old number the
  container did not actually restart.
* `services` shows the same states as before the upgrade. A service that has
  gone from configured to not means a setting did not survive — check
  `INTEGRATIONS_SECRET_KEY` first.
* `loops` shows each recurring loop's last completed boundary. After a restart
  they are stale by design; a boundary missed by less than the catch-up window
  (6 hours daily, 3 days monthly) runs by itself, and one missed by more is
  recorded as skipped rather than run late.

Then sign in and generate one purchase order. Five minutes of a real path is
worth more than any status endpoint.

---

## Going back

```sh
cd /opt/faro/deploy
git checkout v1.3.0
docker compose -f docker-compose.prod.yml up -d --build
```

No database work. The extra columns the newer version added stay where they
are, unread, and the older code behaves exactly as it did.

**When that is not enough:** if the release notes say a version changed the
*meaning* of stored data rather than adding to it, rolling the code back leaves
rows the old version misreads. That case is a restore, not a checkout, and the
notes will say so. As of 2026-09-16 no release has done this.

---

## Versioning

* `APP_VERSION` is what `/health` and the OpenAPI document report. Set it in
  `deploy/.env` when you tag, so an instance can always say what it is running.
* Tags are `vMAJOR.MINOR.PATCH`. MINOR adds, PATCH fixes, MAJOR is reserved for
  a release whose notes carry a migration that is not additive.
* `CHANGELOG.md` at the repository root is the record. A release with no entry
  there is not a release.

---

## What is deliberately not here

**There is no CI/CD.** That is the owner's standing decision, not an oversight:
the build and the deploy are a person running the commands above on a machine
they can see. The consequence to be aware of is that nothing but a person runs
the tests before a tag — `python scripts/run_tests.py`, and the browser smoke
path in `scripts/smoke_browser.md`, are what stands between a green checkout and
a customer's instance.
