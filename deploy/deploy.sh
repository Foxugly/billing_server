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

# The venv must run on $PYTHON (deadsnakes python3.14, root-owned in /usr/bin).
# A venv on another version is rebuilt at its final path — never built
# elsewhere and moved, since console-script shebangs (.venv/bin/gunicorn,
# .venv/bin/celery, used by the units) hard-code the venv path. The old one is
# kept as .venv.prev and put back if the rebuild fails. gunicorn, celery and
# celery-beat all run from this venv and are restarted at the end.
PYTHON=/usr/bin/python3.14
PY_WANT="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_HAVE="$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo none)"
if [ "$PY_HAVE" != "$PY_WANT" ]; then
    echo ">>> Rebuilding the venv: Python $PY_HAVE -> $PY_WANT..."
    rm -rf "$VENV.prev"
    if [ -e "$VENV" ]; then mv "$VENV" "$VENV.prev"; fi
    if ! { "$PYTHON" -m venv "$VENV"            && "$VENV/bin/pip" install --quiet --require-hashes --no-deps -r requirements.txt            && "$VENV/bin/pip" check; }; then
        echo "ERROR: venv rebuild failed; restoring the previous venv." >&2
        rm -rf "$VENV"
        if [ -e "$VENV.prev" ]; then mv "$VENV.prev" "$VENV"; fi
        exit 1
    fi
fi

echo ">>> Installing dependencies..."
# requirements.txt is a hash-pinned lock (uv pip compile requirements.in, Linux /
# Python 3.14). A plain `pip install -r` never upgrades a package already
# installed inside its range, so prod silently missed releases CI had tested.
# Installing the exact lock makes prod run what CI ran; --no-deps means nothing
# is resolved on the box, and pip check fails the deploy on an incomplete lock.
"$VENV/bin/pip" install --quiet --require-hashes --no-deps -r requirements.txt
"$VENV/bin/pip" check

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
sudo /bin/systemctl restart billing-celery
sudo /bin/systemctl restart billing-celery-beat

echo ">>> Deploy complete."
