# Deploying StockAI

Single-VPS production deployment with Docker Compose. Target: a 4 vCPU / 8 GB
Linux box (Hetzner CPX31-class). Containers: Caddy (TLS) → Next.js frontend →
FastAPI API, plus a dedicated worker (training + cron loops) and a bundled
Postgres.

## First deploy

```sh
# On the server (Docker + compose plugin installed, DNS pointing at it):
git clone <repo> faro && cd faro/deploy
cp .env.example .env      # fill in DOMAIN, SECRET_KEY, POSTGRES_PASSWORD, keys
chmod +x deploy.sh
./deploy.sh
```

`deploy.sh` is also the update path: it pulls, rebuilds and waits for
healthchecks — a broken deploy fails in your terminal, not in production.

## Topology and the three growth paths

The stack is pre-wired for the three ways it will need to grow, in order:

1. **Training must not starve the API.** Already solved: the worker is its own
   container with a CPU cap (`cpus: 3.0`), and orphan-job recovery is scoped by
   `WORKER_ID`, so redeploying the API never kills a running training. If one
   machine stops being enough, move the worker service to a second VM — it only
   needs the same `.env` and a shared storage path (that is the moment to move
   `storage/` to object storage).

2. **Managed Postgres.** The bundled `db` service is a compose *profile*.
   To migrate: `pg_dump` → restore into the managed instance → set
   `DATABASE_URL` in `.env` → `./deploy.sh external-db`. Nothing else changes.

3. **More workers.** The job queue claims with `FOR UPDATE SKIP LOCKED`, so
   extra claim-only workers are safe:
   `docker compose -f docker-compose.prod.yml --profile scale up -d --scale worker-extra=2`.
   The cron loops (daily alert emails, monthly snapshots)
   run **only** in the primary `worker` (`SCHEDULER_ENABLED=true` exactly
   once) — turning them on in a second instance duplicates every daily email.

## Backups

The bundled Postgres needs an external backup. On the host's crontab:

```sh
# Nightly dump. Production (stockai.es) runs /opt/stockai-ops/backup.sh with a
# 14-day retention — the figure the privacy policy and DPA state; keep them in step.
0 3 * * * docker exec faro-db-1 pg_dump -U faro faro | gzip > /var/backups/faro-$(date +\%F).sql.gz
```

**The database dump is not enough.** The `storage` volume holds the uploaded
datasets, the model artifacts, the documents — and, if you left
`INTEGRATIONS_SECRET_KEY` empty, `instance_secret.key`, which is the only thing
that can decrypt the credentials stored in that database. A restore with the
dump alone comes back with every stored credential unreadable: the log says the
key changed, the panel reports those services as not configured, and the
credentials have to be entered again — the rows survive and mean nothing.

```sh
# The other half of the backup
0 4 * * * docker run --rm -v faro_storage:/s -v /var/backups:/b alpine tar czf /b/faro-storage-$(date +\%F).tar.gz -C /s .
```

**Check the volume name first.** Compose prefixes volumes with the project
name — the directory name unless you set one — so the volume may be
`deploy_storage`, not `faro_storage`. Against a name that does not exist,
`docker run -v` **creates an empty volume and tars that**: the command
succeeds, the archive is about a hundred bytes, and nothing says so.

```sh
docker volume ls | grep storage
# and after each run, refuse to trust an archive that small:
[ "$(stat -c%s /var/backups/faro-storage-$(date +%F).tar.gz)" -gt 10000 ]   || echo "STORAGE BACKUP IS EMPTY — CHECK THE VOLUME NAME"
```

Restoring is its own runbook, written by doing it: **[`RESTORE.md`](RESTORE.md)**.
The repository copy of the nightly script (with a success marker the status
panel reads) is `ops/backup.sh`, and `scripts/restore_drill.py` rehearses the
restore monthly; both are described at the end of `RESTORE.md`.

Or take the key out of the equation: put a Fernet key in
`INTEGRATIONS_SECRET_KEY` in `deploy/.env` and it never touches the volume.
That is the better answer if your secrets already live in a manager.

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Notes that save an afternoon

- `BACKEND_URL` is baked into the frontend image at **build** time (Next.js
  rewrites): it must be the in-network name `http://api:8010`, and it already
  defaults to that in the Dockerfile. The API is never exposed publicly.
- The API owns schema migrations (they run at its startup); the worker waits
  for the API's healthcheck. An empty database bootstraps itself.
- `ENVIRONMENT=production` makes the server refuse to boot with
  `TESTING_MODE=true` — that refusal is a feature, not a bug to work around.
  It matters more since 2026-08-22: `TESTING_MODE` also bypasses **every plan
  limit**, so a production boot with it on would hand the free tier away.
- **Your own tenant starts on the free tier.** New tenants default to
  `tier = 'free'` (100 SKUs, 2 users, 1 warehouse). The grandfathering
  migration only promotes tenants that existed before 2026-08-22, and a fresh
  production database has none — so the first account you create for yourself
  is capped like a customer's. Flip it once, by hand:

  ```sh
  docker exec -it faro-db-1 psql -U faro -d faro     -c "UPDATE tenants SET tier = 'paid' WHERE slug = '<your-slug>';"
  ```

  That single UPDATE is the entire billing system, by design.
- **AI features: `DEEPSEEK_API_KEY` is required, and there is no fallback.**
  DeepSeek is the only backend as of 2026-08-23 — Anthropic and the local
  Ollama shim were removed. With the key unset, the narrative, the analyst, the
  chat and the data-quality diagnosis all raise `LLMNotConfigured` and degrade
  to their rule-based text, which is honest but is not the product you are
  selling. (Running a local model on this box was never viable anyway: it needs
  more RAM than the entire rest of the stack.)
- Logs: `docker compose -f docker-compose.prod.yml logs -f api worker`.
- **Caddy does not see an edited Caddyfile until it is recreated.** The file is
  bind-mounted, and replacing it (git pull, tar extract) gives it a new inode
  the running container never sees: new routes answer with the old config. After
  any change to `Caddyfile*`:
  `docker compose -f docker-compose.prod.yml up -d --force-recreate caddy`.
