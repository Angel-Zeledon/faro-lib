# Deploying Faro

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
   The cron loops (daily alert emails, monthly snapshots, integration sync)
   run **only** in the primary `worker` (`SCHEDULER_ENABLED=true` exactly
   once) — turning them on in a second instance duplicates every daily email.

## Backups

The bundled Postgres needs an external backup. On the host's crontab:

```sh
# Nightly dump, 30-day retention, e.g. synced to B2/R2 with rclone afterwards
0 3 * * * docker exec faro-db-1 pg_dump -U faro faro | gzip > /var/backups/faro-$(date +\%F).sql.gz
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
