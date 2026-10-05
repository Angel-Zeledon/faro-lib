#!/usr/bin/env bash
# StockAI nightly backup: Postgres dump + the storage volume, with a success
# marker the app can read.
#
# This is the repository copy of /opt/stockai-ops/backup.sh. The server copy is
# updated by hand (see deploy/RESTORE.md, "Updating the server's backup
# script"); this file changes nothing on the server by itself.
#
# What it guarantees that a bare `pg_dump | gzip` does not:
#   * a dump is written to a temp name and renamed only if pg_dump exited 0
#     (a pipe's exit status is the LAST command's, so `set -o pipefail`);
#   * the storage archive is refused if it is suspiciously small (an empty
#     Docker volume tars to ~100 bytes and "succeeds");
#   * `last_success.json` is rewritten ONLY after both halves passed, so its
#     age is the age of the last backup that was actually good. A failed run
#     writes `last_failure.json` and leaves the success marker untouched;
#   * the marker carries sha256 + sizes, which scripts/restore_drill.py checks
#     before it restores.
#
# Marker (JSON, one object):
#   {"status":"ok","finished_at":"2026-10-05T03:12:44Z","db_dump":"faro-2026-10-05.sql.gz",
#    "db_bytes":123,"db_sha256":"...","storage_archive":"faro-storage-2026-10-05.tar.gz",
#    "storage_bytes":456,"storage_sha256":"...","retention_days":14}
#
# The API reads it through BACKUP_STATUS_PATH (see deploy/RESTORE.md for the
# read-only mount that makes the folder visible inside the container).

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups}"
DB_CONTAINER="${DB_CONTAINER:-faro-db-1}"
DB_USER="${DB_USER:-faro}"
DB_NAME="${DB_NAME:-faro}"
# Check with `docker volume ls | grep storage` - compose prefixes the name.
STORAGE_VOLUME="${STORAGE_VOLUME:-faro_storage}"
# The figure the privacy policy and the DPA state. Keep them in step.
RETENTION_DAYS="${RETENTION_DAYS:-14}"
MIN_STORAGE_BYTES="${MIN_STORAGE_BYTES:-10000}"

DAY="$(date +%F)"
DB_FILE="faro-${DAY}.sql.gz"
STORAGE_FILE="faro-storage-${DAY}.tar.gz"
mkdir -p "$BACKUP_DIR"

now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

fail() {
  # Never touch last_success.json: its age is the alarm.
  printf '{"status":"failed","finished_at":"%s","reason":"%s"}\n' \
    "$(now_utc)" "$1" > "${BACKUP_DIR}/last_failure.json.tmp"
  mv "${BACKUP_DIR}/last_failure.json.tmp" "${BACKUP_DIR}/last_failure.json"
  echo "BACKUP FAILED: $1" >&2
  exit 1
}

# 1. Database dump (renamed into place only when pg_dump AND gzip succeeded).
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "${BACKUP_DIR}/${DB_FILE}.tmp" \
  || fail "pg_dump_failed"
gzip -t "${BACKUP_DIR}/${DB_FILE}.tmp" || fail "dump_not_valid_gzip"
mv "${BACKUP_DIR}/${DB_FILE}.tmp" "${BACKUP_DIR}/${DB_FILE}"

# 2. Storage volume (datasets, artifacts, documents, instance_secret.key).
docker run --rm -v "${STORAGE_VOLUME}:/s:ro" -v "${BACKUP_DIR}:/b" alpine \
  tar czf "/b/${STORAGE_FILE}.tmp" -C /s . || fail "storage_tar_failed"
STORAGE_BYTES="$(stat -c%s "${BACKUP_DIR}/${STORAGE_FILE}.tmp")"
if [ "$STORAGE_BYTES" -lt "$MIN_STORAGE_BYTES" ]; then
  rm -f "${BACKUP_DIR}/${STORAGE_FILE}.tmp"
  fail "storage_archive_too_small_check_volume_name"
fi
mv "${BACKUP_DIR}/${STORAGE_FILE}.tmp" "${BACKUP_DIR}/${STORAGE_FILE}"

# 3. Retention.
find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'faro-*.sql.gz' -o -name 'faro-storage-*.tar.gz' \) \
  -mtime "+${RETENTION_DAYS}" -delete

# 4. Success marker, last and atomic.
DB_BYTES="$(stat -c%s "${BACKUP_DIR}/${DB_FILE}")"
DB_SHA="$(sha256sum "${BACKUP_DIR}/${DB_FILE}" | cut -d' ' -f1)"
ST_SHA="$(sha256sum "${BACKUP_DIR}/${STORAGE_FILE}" | cut -d' ' -f1)"
printf '{"status":"ok","finished_at":"%s","db_dump":"%s","db_bytes":%s,"db_sha256":"%s","storage_archive":"%s","storage_bytes":%s,"storage_sha256":"%s","retention_days":%s}\n' \
  "$(now_utc)" "$DB_FILE" "$DB_BYTES" "$DB_SHA" "$STORAGE_FILE" "$STORAGE_BYTES" "$ST_SHA" "$RETENTION_DAYS" \
  > "${BACKUP_DIR}/last_success.json.tmp"
mv "${BACKUP_DIR}/last_success.json.tmp" "${BACKUP_DIR}/last_success.json"
chmod 644 "${BACKUP_DIR}/last_success.json"
echo "backup ok: ${DB_FILE} ${STORAGE_FILE}"
