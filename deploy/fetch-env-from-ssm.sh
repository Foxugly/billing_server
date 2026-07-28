#!/usr/bin/env bash
# =============================================================================
# Foxugly Billing — récupère l'environnement depuis AWS SSM vers un tmpfs.
#
# Exécuté en root par billing-env-fetch.service (oneshot) au boot, AVANT
# gunicorn. Le fichier vit dans /run (tmpfs) : jamais sur disque, re-fetché à
# chaque boot. Source de vérité = SSM /billing/prod/* (eu-west-1), lu via le
# rôle d'instance EC2 par IMDS (aucune clé AWS sur disque).
#
# §3.10 : ce script tourne en root, il ne doit donc PAS vivre dans l'arbre
# écrivable par django. Il est installé root:root 0755 dans
# /usr/local/sbin/billing-env-fetch.sh. Ce fichier-ci est la source versionnée,
# jamais la cible d'exécution.
# =============================================================================
set -euo pipefail
umask 077   # les fichiers temporaires contiennent brièvement des secrets déchiffrés.

SSM_PREFIX="/billing/prod"
AWS_REGION="eu-west-1"
RUN_DIR="/run/billing"
ENV_FILE="$RUN_DIR/.env"
TMP_FILE="$RUN_DIR/.env.tmp"
RAW_FILE="$RUN_DIR/.ssm.json"
OWNER="django:www-data"

mkdir -p "$RUN_DIR"
# 750 root:www-data — root l'écrit ; django (groupe www-data) peut traverser ;
# le .env reste 640 pour que son contenu reste protégé (§3.5).
chmod 750 "$RUN_DIR"
chown root:www-data "$RUN_DIR"

aws ssm get-parameters-by-path \
    --path "$SSM_PREFIX" \
    --recursive \
    --with-decryption \
    --region "$AWS_REGION" \
    --output json > "$RAW_FILE"

python3 - "$SSM_PREFIX" "$TMP_FILE" "$RAW_FILE" <<'PY'
import json, sys

prefix, tmp_path, raw_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(raw_path) as fh:
    params = json.load(fh).get("Parameters", [])

if not params:
    sys.stderr.write(f"ERROR: no parameters under {prefix}; refusing to write an empty env.\n")
    sys.exit(1)

lines = []
for p in params:
    key = p["Name"][len(prefix):].lstrip("/")
    value = p["Value"].strip("\r\n")
    if "\n" in value or "\r" in value:
        sys.stderr.write(f"ERROR: value for {key} contains an internal newline; refusing.\n")
        sys.exit(1)
    lines.append(f"{key}={value}")

with open(tmp_path, "w") as fh:
    fh.write("\n".join(sorted(lines)) + "\n")
PY

rm -f "$RAW_FILE"

if [ ! -s "$TMP_FILE" ]; then
    echo "ERROR: assembled env file is empty; keeping previous $ENV_FILE." >&2
    rm -f "$TMP_FILE"
    exit 1
fi

chmod 640 "$TMP_FILE"
chown "$OWNER" "$TMP_FILE"
mv -f "$TMP_FILE" "$ENV_FILE"

echo "Wrote $(wc -l < "$ENV_FILE") variables to $ENV_FILE."
