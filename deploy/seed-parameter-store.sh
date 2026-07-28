#!/usr/bin/env bash
# =============================================================================
# Foxugly Billing — Seed AWS SSM /billing/prod/* (à lancer HORS BOX, identité admin).
#
# Noms nus (§3.5/§3.14). Vrais secrets = SecureString ; tout le reste = String.
# Idempotent : rejouable pour mettre à jour.
#
# SECRET_KEY et DB_PASSWORD sont GÉNÉRÉS ici, jamais copiés-collés : le mot de
# passe de la base est ensuite relu depuis SSM par la box elle-même (voir
# deploy/DEPLOY.md), il ne transite donc par aucun presse-papier.
# =============================================================================
set -euo pipefail
REGION="eu-west-1"
P="/billing/prod"

put()   { aws ssm put-parameter --region "$REGION" --name "$P/$1" --type String       --overwrite --value "$2" >/dev/null && echo "  ok $1"; }
secret(){ aws ssm put-parameter --region "$REGION" --name "$P/$1" --type SecureString --overwrite --value "$2" >/dev/null && echo "  ok $1 (secret)"; }

# --- Runtime / env ---
put STATE "PROD"
put DEBUG "False"
put ALLOWED_HOSTS "billing-api.foxugly.com,127.0.0.1,localhost"
put CORS_ALLOWED_ORIGINS "https://billing.foxugly.com"
put CSRF_TRUSTED_ORIGINS "https://billing.foxugly.com,https://billing-api.foxugly.com"
put FRONTEND_BASE_URL "https://billing.foxugly.com"

# --- Base de données (PostgreSQL box-local, convention DB_* 6 variables §3.13) ---
put DB_ENGINE "postgresql"
put DB_HOST "127.0.0.1"
put DB_PORT "5432"
put DB_NAME "billing"
put DB_USER "billing"
secret DB_PASSWORD "$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 40)"

# --- Django ---
secret SECRET_KEY "$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"

# --- Sentry (§3.8/§3.14 : le DSN est public, il ship dans les bundles → String) ---
put SENTRY_DSN "<SENTRY_DSN>"
put SENTRY_ENVIRONMENT "production"
put SENTRY_TRACES_SAMPLE_RATE "0.0"

echo
echo "Contrôle du piège flotte (valeur seedée en ciphertext KMS brut) :"
aws ssm get-parameters-by-path --region "$REGION" --path "$P" --recursive --with-decryption \
    --query "Parameters[?starts_with(Value, 'AQIC')].Name" --output text
echo "(sortie vide attendue ci-dessus)"
