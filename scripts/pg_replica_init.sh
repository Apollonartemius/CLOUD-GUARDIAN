#!/bin/sh
# pg-replica bootstrap: clone the primary once (physical copy + streaming),
# then just start Postgres in standby mode. Safe to rerun - idempotent.
set -e

DATA_DIR=/var/lib/postgresql/data

if [ ! -s "${DATA_DIR}/PG_VERSION" ]; then
  echo "[pg-replica] no local data found - cloning primary with pg_basebackup"
  mkdir -p "${DATA_DIR}"
  chown -R postgres:postgres "${DATA_DIR}"
  su-exec postgres pg_basebackup \
    -h postgres \
    -U cloudguardian \
    -D "${DATA_DIR}" \
    -X stream \
    -R \
    -P -v
  echo "[pg-replica] basebackup complete (standby.signal + primary_conninfo written)"
fi

echo "[pg-replica] starting standby"
exec docker-entrypoint.sh postgres -c config_file=/etc/postgresql/replica.conf