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

# --- database secrets engine (gap #9: dynamic short-lived DB credentials) --
# Vault connects to Postgres as the superuser defined by compose and issues
# per-call, time-limited roles that only have DML access (never DDL/owner).
echo "[seed] configuring database secrets engine ..."
if ! vault secrets list -format=json | grep -q '"database/"'; then
    vault secrets enable database
    echo "[seed] enabled database secrets engine"
fi

vault write database/config/cloudguardian \
    plugin_name=postgresql-database-plugin \
    allowed_roles="cloudguardian-app" \
    connection_url="postgresql://{{username}}:{{password}}@postgres:5432/cloudguardian?sslmode=disable" \
    username="cloudguardian" \
    password="cloudguardian" >/dev/null
echo "[seed] database/config/cloudguardian configured (superuser connection)"

vault write database/roles/cloudguardian-app \
    db_name=cloudguardian \
    default_ttl="1h" \
    max_ttl="24h" \
    creation_statements='
CREATE ROLE "{{name}}" WITH LOGIN PASSWORD '"'"'{{password}}'"'"' VALID UNTIL '"'"'{{expiration}}'"'"';
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{{name}}";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{{name}}";
' \
    revocation_statements='
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM "{{name}}";
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM "{{name}}";
ALTER ROLE "{{name}}" NOLOGIN;
DROP ROLE IF EXISTS "{{name}}";
' >/dev/null
echo "[seed] database/roles/cloudguardian-app configured (TTL 1h / max 24h)"
echo "[seed] all done"