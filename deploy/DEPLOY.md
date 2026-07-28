# Déploiement — billing

Modèle flotte : GitHub Actions → OIDC → SSM (OPERATIONS.md §3.11). Aucun `git pull` manuel sur
la box, aucun push GitHub depuis l'EC2.

| Élément | Valeur |
|---|---|
| Arbre | `/var/www/django_websites/billing_server` |
| Port | `127.0.0.1:8007` |
| Units | `billing-env-fetch` (oneshot root), `billing-gunicorn` |
| Env | `/run/billing/.env` (tmpfs, 640 django:www-data) ← SSM `/billing/prod` |
| vhost | `billing-api.foxugly.com` → `deploy/nginx/billing-api.conf` |
| Rôle OIDC | `billing-deploy`, épinglé sur `repo:Foxugly/billing_server:environment:production` |

Celery (`billing-celery`, `billing-celery-beat`, Redis db4) arrive au lot L3, avec la file de
livraison des entitlements — inutile d'installer un worker qui ne consomme aucune tâche.

## Recharger les secrets après un changement en SSM

    sudo systemctl restart billing-env-fetch
    sudo systemctl restart billing-gunicorn

## Diagnostic

    systemctl status billing-gunicorn
    journalctl -u billing-gunicorn -n 100 --no-pager
    curl -s https://billing-api.foxugly.com/health/
