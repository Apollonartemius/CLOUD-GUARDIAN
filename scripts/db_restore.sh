#!/usr/bin/env bash
#
# CloudGuardian AI - Postgres backup restore helper (Phase 7 / gap #3)
# --------------------------------------------------------------------
# Backups are written automatically by the `pg-backup` compose service
# (daily custom-format dumps into the `cloudguardian-backups` volume,
# 7-day retention).
#
# This script:
#   1. lists what is backed up
#   2. verifies a chosen archive is readable
#   3. restores it into a scratch DB (`cloudguardian_restore_check`)
#      and prints the recovered incident count so you can eyeball it
#
# Usage:
#   scripts/db_restore.sh                    # list backups
#   scripts/db_restore.sh <backup-name>      # verify + restore into scratch db
#
# Promote the check DB when you are satisfied:
#   docker compose exec postgres psql -U cloudguardian -d cloudguardian -c \
#     "DROP DATABASE cloudguardian WITH (FORCE);" \
#     -c "ALTER DATABASE cloudguardian_restore_check RENAME TO cloudguardian;"
set -euo pipefail

dump="${1:-}"

echo "== Available backups (oldest..newest) =="
docker compose exec -T pg-backup sh -c \
  'ls -1ht /backups/*.dump.gz 2>/dev/null | tac || echo "  none yet - give pg-backup a cycle or docker compose restart pg-backup"'

if [[ -z "${dump}" ]]; then
  echo
  echo "usage: $0 <backup-name>   e.g. cloudguardian-20260910-01.dump.gz"
  exit 0
fi

src="/backups/${dump}"
echo
echo "== Verifying ${dump} =="
docker compose exec -T pg-backup sh -c "gunzip -c ${src} | pg_restore -l - >/dev/null" \
  && echo "  archive OK: ${dump}"

echo
echo "== Restoring into scratch db cloudguardian_restore_check =="
docker compose exec -T postgres psql -U cloudguardian -d cloudguardian -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS cloudguardian_restore_check WITH (FORCE);" \
  -c "CREATE DATABASE cloudguardian_restore_check;"
docker compose exec -T pg-backup sh -c \
  "gunzip -c ${src} | pg_restore -h postgres -U cloudguardian -d cloudguardian_restore_check --no-owner"
echo "  restore finished"

echo
echo "== Recovered rows in scratch db =="
incidents=$(docker compose exec -T postgres psql -U cloudguardian -d cloudguardian_restore_check -tAc 'SELECT count(*) FROM incidents;')
maxid=$(docker compose exec -T postgres psql -U cloudguardian -d cloudguardian_restore_check -tAc 'SELECT max(id) FROM incidents;')
echo "  incidents restored: ${incidents} (max id ${maxid})"

echo
echo "Backup verified. To make scratch db the live db:"
echo "  docker compose exec postgres psql -U cloudguardian -d cloudguardian -c \\
        \"DROP DATABASE cloudguardian WITH (FORCE);\" -c \\
        \"ALTER DATABASE cloudguardian_restore_check RENAME TO cloudguardian;\""