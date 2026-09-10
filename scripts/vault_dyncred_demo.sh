#!/usr/bin/env sh
# CloudGuardian AI - Vault dynamic database credential demo (gap #9)
# -------------------------------------------------------------------
# Proof that Vault issues SHORT-LIVED, per-call Postgres roles:
#   1. Ask Vault for db creds (creates role v-cloudguardian-app-XXXX)
#   2. Use them to SELECT from Postgres      -> works (the account exists)
#   3. Revoke the lease
#   4. Use the same creds again              -> authentication FAILS
# The app never needs a static DB password: every run gets its own.
#
# Runs on the host / any box with the vault CLI + docker. Requires the
# compose network 'cloudguardian-net' and the postgres image to be present.
set -eu

: "${VAULT_ADDR:=http://localhost:8200}"
: "${VAULT_TOKEN:=cloudguardian-root}"
NET=${CLOUDGUARDIAN_NET:-cloudguardian-net}
PG_IMAGE=${PG_IMAGE:-postgres:16-alpine}

echo "[dyncred] VAULT_ADDR=$VAULT_ADDR"

# Use the host vault CLI when installed, otherwise the one baked into the
# running 'vault' container (keeps this script dependency-free).
if command -v vault >/dev/null 2>&1; then
    vault() { command vault "$@"; }
else
    VAULT_CONTAINER=${VAULT_CONTAINER:-vault}
    vault() {
        docker exec -e VAULT_ADDR=http://vault:8200 -e VAULT_TOKEN="$VAULT_TOKEN" \
            "$VAULT_CONTAINER" vault "$@"
    }
fi

CREDS="$(vault read -format=json database/creds/cloudguardian-app)"
DYN_USER="$(printf '%s' "$CREDS" | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["username"])')"
DYN_PASS="$(printf '%s' "$CREDS" | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["password"])')"
LEASE="$(printf '%s' "$CREDS" | python3 -c 'import sys,json;print(json.load(sys.stdin)["lease_id"])')"

echo "[dyncred] issued dynamic role : $DYN_USER"
echo "[dyncred] lease id             : $LEASE"

psql_here() {
    docker run --rm --network "$NET" -e PGPASSWORD="$DYN_PASS" "$PG_IMAGE" \
        psql -h postgres -U "$DYN_USER" -d cloudguardian -tAc "$1"
}

echo "[dyncred] step 1 - dynamic account authenticates and can read the DB"
echo "[dyncred] incident count from dynamic user: $(psql_here 'SELECT count(*) FROM incidents;')"

echo "[dyncred] step 2 - revoking the lease (Vault drops the role)"
vault lease revoke "$LEASE" >/dev/null

echo "[dyncred] step 3 - the same credentials must now be rejected"
if psql_here 'SELECT 1;' >/dev/null 2>&1; then
    echo "[dyncred] FAIL: credentials still work after revocation"
    exit 1
fi
echo "[dyncred] OK: dynamic role no longer exists - revoked credentials fail to authenticate"
echo "[dyncred] done"