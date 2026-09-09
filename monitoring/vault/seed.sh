#!/usr/bin/env sh
# CloudGuardian AI - Vault secret seed
# ------------------------------------
# Idempotent. On first boot it generates a strong random JWT signing key
# and the admin password, stores them in Vault under KV v2 (secret/). It
# persists the generated secret to a host file (`vault-secrets.env`) so
# restarts keep the SAME key (otherwise every token would invalidate).
#
# Usage (inside the vault container / on the host with the CLI):
#   VAULT_ADDR=http://vault:8200 VAULT_TOKEN=<root> ./seed.sh
set -eu

: "${VAULT_ADDR:=http://vault:8200}"
: "${VAULT_TOKEN:=cloudguardian-root}"
SECRETS_FILE="/vault-secrets.env"

echo "[seed] using VAULT_ADDR=$VAULT_ADDR"

# --- load or generate the JWT signing secret -------------------------------
JWT_SECRET=""
ADMIN_EMAIL="admin@cloudguardian.ai"
ADMIN_PASSWORD=""
if [ -f "$SECRETS_FILE" ]; then
    . "$SECRETS_FILE" 2>/dev/null || true
fi
if [ -z "$JWT_SECRET" ]; then
    JWT_SECRET="$(head -c 48 /dev/urandom | base64 | tr -d '\n')"
    ADMIN_PASSWORD="$(head -c 18 /dev/urandom | base64 | tr -d '\n')"
    printf 'JWT_SECRET=%s\nADMIN_EMAIL=%s\nADMIN_PASSWORD=%s\n' "$JWT_SECRET" "$ADMIN_EMAIL" "$ADMIN_PASSWORD" > "$SECRETS_FILE"
    chmod 600 "$SECRETS_FILE"
    echo "[seed] generated new secrets -> $SECRETS_FILE"
else
    echo "[seed] reused existing secrets from $SECRETS_FILE"
fi

# --- write secrets into Vault KV v2 ----------------------------------------
echo "[seed] writing secrets to Vault at $VAULT_ADDR ..."
vault kv put "secret/cloudguardian/global" \
    JWT_SECRET="$JWT_SECRET" \
    ADMIN_EMAIL="$ADMIN_EMAIL" \
    ADMIN_PASSWORD="$ADMIN_PASSWORD" >/dev/null
echo "[seed] done - secret/cloudguardian/global updated"