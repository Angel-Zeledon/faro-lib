"""The loop that looks at every due file-drop source.

A claim loop, not a cron: each due source is leased (`FOR UPDATE SKIP LOCKED`), so
it runs beside the job worker and a second instance is harmless. Sources are looked
at one after another by this thread; a slow server delays the next source by at
most the transport timeouts, and a company's interval is minutes, not seconds.
"""

from __future__ import annotations

import logging
import time

from backend.file_drop import service

log = logging.getLogger(__name__)

POLL_SECONDS = 15


def run_due_once(limit: int = 5) -> int:
    """Look at the sources that are due. Returns how many were looked at."""
    made = 0
    for row in service.claim_due(limit):
        try:
            service.poll(row)
        except Exception:  # noqa: BLE001 - poll() does not raise; this is the last net
            log.exception("file drop: source %s failed outside poll()", row.get("id"))
        made += 1
    return made


def loop() -> None:
    log.info("File drop loop started")
    while True:
        made = 0
        try:
            made = run_due_once()
        except Exception as e:  # noqa: BLE001
            log.error("File drop loop error: %s", e, exc_info=True)
        if made == 0:
            time.sleep(POLL_SECONDS)
