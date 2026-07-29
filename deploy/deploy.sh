#!/usr/bin/env sh
# Deploy/update Faro on the server. Run from the repo's deploy/ directory.
#   ./deploy.sh              # bundled Postgres (single-VPS default)
#   ./deploy.sh external-db  # managed Postgres (DATABASE_URL in .env)
set -eu

cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "deploy/.env not found — copy .env.example and fill in secrets first." >&2
    exit 1
fi

PROFILE_ARGS="--profile bundled-db"
if [ "${1:-}" = "external-db" ]; then
    PROFILE_ARGS=""
fi

git pull --ff-only

# --wait: returns only when healthchecks pass, so a failed deploy fails loudly
# here instead of surfacing as a broken site.
docker compose -f docker-compose.prod.yml $PROFILE_ARGS up -d --build --wait

docker image prune -f

echo "Deployed. Status:"
docker compose -f docker-compose.prod.yml $PROFILE_ARGS ps
