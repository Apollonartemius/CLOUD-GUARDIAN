#!/bin/sh
# CloudGuardian point-in-time recovery: replay a physical base backup plus
# the archived WAL bucket to a specific moment, in an ISOLATED scratch
# container (the live database is never touched). This is the demo for
# "accidental data loss page-back".
#
#   scripts/db_pitr_restore.sh <basebackup-timestamp> <target-time> [wal-volume]
#
#   <target-time>  any Postgres recovery_target_time value, e.g.
#                  '2026-09-10 11:45:00+00'
#                  (use 'immediate' to replay all the way to the end of the WAL)
set -eu

TS="${1:?usage: db_pitr_restore.sh <basebackup-ts> <target-time> [wal-volume]}"
TARGET_TIME="${2:?missing recovery target time}"
WAL_VOL="${3:-$(docker volume ls -q | grep cloudguardian-wal | head -1)}"
CONF="/tmp/opencode/cg-restore-${TS}.conf"
CNT="pg-pitr-check"

if [ -z "${WAL_VOL}" ]; then
  echo "FATAL: cloudguardian-wal volume not found" >&2
  exit 1
fi

[ -n "$(docker ps -q -f name=${CNT})" ] && docker rm -f "${CNT}" >/dev/null

if [ "${TARGET_TIME}" = "immediate" ]; then
  TARGET_LINE=""
else
  TARGET_LINE="recovery_target_time = '${TARGET_TIME}'"
fi

cat > "${CONF}" <<EOF
recovery_target_action = 'promote'
${TARGET_LINE}
restore_command = 'cp /wal/%f %p'
port = 5432
listen_addresses = '0.0.0.0'
EOF

echo "== Replaying backup ${TS} to ${TARGET_TIME:-end-of-WAL} (volume ${WAL_VOL}) =="
docker run --rm -d --name "${CNT}" \
  -e POSTGRES_PASSWORD=x \
  -v "${WAL_VOL}:/wal:ro" \
  -v "${CONF}:/etc/postgresql/restore.conf:ro" \
  -v cg-pitr-scratch:/var/lib/postgresql/data \
  postgres:16-alpine \
  sh -c '
    rm -rf /var/lib/postgresql/data/*
    cp -a /wal/basebackups/'"${TS}"'/. /var/lib/postgresql/data/
    chown -R postgres:postgres /var/lib/postgresql/data
    touch /var/lib/postgresql/data/recovery.signal
    exec docker-entrypoint.sh postgres -c config_file=/etc/postgresql/restore.conf
  ' >/dev/null

echo "== waiting for recovery/promotion =="
for i in $(seq 1 60); do
  if docker exec "${CNT}" pg_isready -U cloudguardian -h localhost -d cloudguardian >/dev/null 2>&1; then
    echo "ready after ${i}s"
    break
  fi
  sleep 1
done

echo "== recovered state (${CNT}) =="
docker exec "${CNT}" psql -U cloudguardian -h localhost -d cloudguardian \
  -c "SELECT count(*) AS incidents, max(id) AS max_id, max(action_started_at) AS newest FROM incidents;" \
  -c "SELECT count(*) AS metric_readings FROM metric_readings;" \
  -c "SELECT id, service_name, incident_type, action_started_at FROM incidents ORDER BY id DESC LIMIT 3;"
echo "== cleanup =="
docker rm -f "${CNT}" >/dev/null && rm -f "${CONF}"
echo "done"