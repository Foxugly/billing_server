#!/usr/bin/env bash
# =============================================================================
# Foxugly Billing — script de déploiement (exécuté sous 'django' via OIDC->SSM).
#   /var/www/django_websites/billing_server/deploy/deploy.sh
# =============================================================================
set -euo pipefail
umask 027   # nouveaux dirs 750 / fichiers 640 depuis git/pip/collectstatic (§3.1/§3.2)

APP_DIR="/var/www/django_websites/billing_server"
VENV="$APP_DIR/.venv"

cd "$APP_DIR"

echo ">>> Installing dependencies..."
"$VENV/bin/pip" install --quiet -r requirements.txt

# Charger l'env fetché depuis SSM pour que manage.py ait SECRET_KEY, STATE, DB_*.
# Parsé littéralement (key=value), PAS via `source` : les valeurs peuvent contenir
# des caractères que le shell interpréterait (comportement d'EnvironmentFile).
ENV_FILE="/run/billing/.env"
if [ -f "$ENV_FILE" ]; then
    echo ">>> Loading env from $ENV_FILE..."
    while IFS='=' read -r _k _v || [ -n "$_k" ]; do
        case "$_k" in ''|\#*) continue ;; esac
        export "$_k=$_v"
    done < "$ENV_FILE"
    unset _k _v
else
    echo "WARNING: $ENV_FILE missing — has billing-env-fetch run? Trying without it." >&2
fi

echo ">>> Running migrations..."
"$VENV/bin/python" manage.py migrate --noinput

echo ">>> Collecting static files..."
"$VENV/bin/python" manage.py collectstatic --noinput

echo ">>> Normalizing permissions (dirs 750 / files 640, no o-rwx, no g-w)..."
# chown AVANT chmod : l'ordre inverse verrouille django hors de son propre venv (§3.1).
chown -R django:www-data "$APP_DIR"
chmod -R g-w,o-rwx "$APP_DIR"

echo ">>> Restarting services..."
sudo /bin/systemctl restart billing-gunicorn

echo ">>> Deploy complete."
