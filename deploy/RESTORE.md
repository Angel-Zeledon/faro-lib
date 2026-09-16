# Restoring Faro from a backup

A backup nobody has restored is a hope, not a backup. This runbook was written
by **performing** the restore on 2026-09-16, and the numbers below are that
run's real output. Do it yourself once, on a scratch machine, before you need
it — the rehearsal is the deliverable, not the document.

Budget: about 30 minutes for a small instance.

---

## What a complete backup is

Two halves. Either one alone gives you an instance that looks restored and is
not.

| Half | Holds | If you lose it |
|---|---|---|
| Postgres dump | every row: tenants, users, forecasts, stock, orders, the audit trail | everything |
| The `STORAGE_PATH` directory | uploaded datasets, trained model artifacts, indexed documents — **and `instance_secret.key`** | the files, and the ability to read every credential in the database |

**`instance_secret.key` is the half people miss.** When
`INTEGRATIONS_SECRET_KEY` is empty, Faro generates a Fernet key into
`STORAGE_PATH` on first use and encrypts every credential typed into
`/instalacion` with it. Restore the database without that file and the rows
come back meaning nothing. Measured, below.

### Know which directory `STORAGE_PATH` actually is

Three plausible directories exist in a checkout and only one is the data:

```sh
# Ask the app, do not guess
python -c "from backend.config import settings; print(settings.storage_path)"
```

* In the `deploy/` stack it is `/app/storage`, the `storage` volume. Correct.
* On a bare checkout with no `STORAGE_PATH` set it defaults to
  **`backend/storage`** — not the `storage/` at the repository root. Backing up
  the wrong one silently backs up nothing that matters.

### If you back up a Docker volume, check the name

```sh
docker volume ls | grep storage      # e.g. faro_storage, deploy_storage, myapp_storage
```

Compose prefixes volumes with the project name, which is the directory name
unless you set one. `docker run -v faro_storage:/s …` against a volume that is
actually called `deploy_storage` **creates an empty volume and tars it**: the
command succeeds, the archive is ~100 bytes, and nothing tells you. After every
backup:

```sh
# An archive this small is an empty volume, not a small instance
[ "$(stat -c%s /var/backups/faro-storage-$(date +%F).tar.gz)" -gt 10000 ] \
  || echo "STORAGE BACKUP IS EMPTY — CHECK THE VOLUME NAME"
```

---

## The drill

Run it against a scratch database and a scratch directory. Nothing below
touches the live instance.

### 1. Take both halves

```sh
docker exec faro-db-1 pg_dump -U faro faro | gzip > /tmp/drill/faro-db.sql.gz
docker run --rm -v faro_storage:/s -v /tmp/drill:/b alpine \
  tar czf /b/faro-storage.tar.gz -C /s .
```

### 2. Plant something that proves the restore worked

Row counts prove the dump loaded. They do **not** prove the credentials are
readable, which is the part that fails. Before the drill, note one secret you
have stored in `/instalacion` (a Resend key, a Twilio token) — after the
restore, that panel must show it as configured, not as missing.

### 3. Restore into a clean database

```sh
docker exec faro-db-1 psql -U faro -c "CREATE DATABASE faro_restored;"
gunzip -c /tmp/drill/faro-db.sql.gz | docker exec -i faro-db-1 psql -U faro -d faro_restored -q
```

The 2026-09-16 run: no errors, and

```
tenants=159 users=89 completed_sessions=42 stock_rows=2156
```

### 4. Restore the storage half

```sh
mkdir -p /tmp/drill/storage && tar xzf /tmp/drill/faro-storage.tar.gz -C /tmp/drill/storage
ls -l /tmp/drill/storage/instance_secret.key    # it must be there
```

### 5. Boot against both and check a secret decrypts

```sh
DATABASE_URL=postgresql://faro:…@localhost:5432/faro_restored \
STORAGE_PATH=/tmp/drill/storage \
  python -m uvicorn backend.main:app --port 8099
```

Then: sign in, open `/instalacion`, and confirm the services you had configured
still report as configured. `/health` should show `database: true` and the same
`services` states as the live instance.

### 6. Prove the failure mode once, so you recognise it

Delete `instance_secret.key` from the restored directory and boot again. The
2026-09-16 run produced exactly this:

```
No INTEGRATIONS_SECRET_KEY was set, so one was generated at …/instance_secret.key.
Cannot decrypt stored override for resend_api_key (scope=instance).
INTEGRATIONS_SECRET_KEY may have changed — the value must be entered again.
```

Row counts are **identical** — 159 tenants, 89 users, 2156 stock rows. Only the
credentials are gone. That is what a database-only restore looks like: an
instance that boots, lists every customer, and cannot send a single email.

It is at least loud: the app names the variable and says the value must be
entered again. If you see those lines after a real restore, you restored one
half.

---

## Taking the key out of the equation

The simplest way never to have this problem: set `INTEGRATIONS_SECRET_KEY` in
`deploy/.env` from your secret manager and the key never lives in the volume.

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Do this **before** storing any credential. Changing it later has the same
effect as losing it: every stored secret must be entered again.

---

## After a real restore

1. `SCHEDULER_ENABLED=true` in exactly one instance. Two schedulers send every
   daily alert twice.
2. Check `/health` → `loops`: each recurring loop reports the boundary it last
   completed. After a restore they will be stale; that is expected, and the
   catch-up window (6 hours daily, 3 days monthly) decides whether the missed
   pass runs or is recorded as skipped.
3. Re-run any training that was RUNNING when the backup was taken. A job row
   that was mid-flight comes back as a session nobody is working on.
