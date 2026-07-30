"""
Standalone worker process — `python -m backend.workers`.

Runs the same loops the API embeds in single-process mode, but in a dedicated
container/VM so training CPU never starves the API. Which loops run is
governed by WORKER_ENABLED / SCHEDULER_ENABLED, exactly as in-process.

The API owns schema migrations (they run in its startup); this process only
waits until the database answers, then starts its loops. On a fresh stack the
worker may boot before the API has migrated — the loops' own error backoff
covers that window.
"""

import logging
import sys
import time

from backend.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("backend.workers")

_DB_WAIT_SECONDS = 2.0
_DB_WAIT_MAX_ATTEMPTS = 60


def _wait_for_db() -> None:
    """Open this process's pool, retrying until the database answers.

    The connection pool is per-process. The API gets one because its FastAPI
    startup calls `init_pool`; nothing runs that startup here, so without this
    the worker waits out all 60 attempts against a pool that was never opened
    ("DB pool not initialized"), exits 1, and the container restarts forever —
    an API that serves fine next to a deployment that trains nothing and sends
    no daily alerts.

    Opening the pool IS the connectivity check: psycopg2 connects `min_conn`
    times eagerly, so a database that is not up yet fails right here and is
    retried on the next attempt.
    """
    from backend.db.connection import init_pool, pool_is_initialized, query_one

    for attempt in range(1, _DB_WAIT_MAX_ATTEMPTS + 1):
        try:
            if not pool_is_initialized():
                # Same sizing as the API: the training loop runs several
                # threads and each holds a connection while it writes.
                init_pool(settings.database_url, min_conn=5, max_conn=20)
            query_one("SELECT 1 AS ok")
            log.info("Database reachable")
            return
        except Exception as e:
            log.info(f"Waiting for database ({attempt}/{_DB_WAIT_MAX_ATTEMPTS}): {e}")
            time.sleep(_DB_WAIT_SECONDS)
    log.critical("Database never became reachable — exiting so the container restarts")
    sys.exit(1)


def main() -> None:
    from backend.workers import worker

    if not (settings.worker_enabled or settings.scheduler_enabled):
        log.critical(
            "Neither WORKER_ENABLED nor SCHEDULER_ENABLED is true — a dedicated "
            "worker process with nothing to run is a misconfiguration. Exiting."
        )
        sys.exit(1)

    _wait_for_db()
    threads = worker.start()
    log.info(f"Worker process up (id={worker.worker_id()}) — components: {[t.name for t in threads]}")

    # The loops are daemon threads; keep the main thread alive and exit
    # non-zero if any loop dies so the orchestrator restarts the container.
    while True:
        dead = [t.name for t in threads if not t.is_alive()]
        if dead:
            log.critical(f"Component thread(s) died: {dead} — exiting for restart")
            sys.exit(1)
        time.sleep(10)


if __name__ == "__main__":
    main()
