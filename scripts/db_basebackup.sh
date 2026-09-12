#!/bin/sh
# CloudGuardian full physical backup (base backup) for point-in-time recovery.
#
# Writes a plain-directory pg_basebackup into the shared `cloudguardian-wal`
# volume (under basebackups/<timestamp>), whose WAL archive is replayed by
# `db_pitr_restore.sh`. Run this from the host while the stack is up:
#
#   scripts/db_basebackup.sh [wal-volume-name]
#
# Defaults to auto-detecting the compose WAL volume.
set -eu

WAL_VOL="${1:-$(docker volume ls -q | grep cloudguardian-wal | head -1)}"
if [ -z "${WAL_VOL}" ]; then
  echo "FATAL: cloudguardian-wal volume not found" >&2
  exit 1
fi

TS="$(date -u +%Y%m%d-%H%M%S)"
TARGET="/var/lib/postgresql/wal_archive/basebackups/${TS}"

echo "== Taking physical base backup -> ${TS} =="
docker compose exec -T postgres sh -c "
  su-exec postgres sh -c 'mkdir -p /var/lib/postgresql/wal_archive/basebackups &&
  pg_basebackup -h localhost -U cloudguardian -D '${TARGET}' -X stream -P'
"
echo "== Backup dir: ${TARGET} (wal volume: ${WAL_VOL}) =="
echo "== Size: $(docker run --rm -v "${WAL_VOL}:/wal:ro" -e TS="${TS}" postgres:16-alpine \
  sh -c 'du -sh /wal/basebackups/$TS | cut -f1') =="
echo "${TS}"