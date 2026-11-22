# Déploiement — billing

Modèle flotte : GitHub Actions → OIDC → SSM (OPERATIONS.md §3.11). Aucun `git pull` manuel sur
la box, aucun push GitHub depuis l'EC2.

| Élément | Valeur |
|---|---|
| Arbre | `/var/www/django_websites/billing_server` |
| Port | `127.0.0.1:8007` |
| Units | `billing-env-fetch` (oneshot root), `billing-gunicorn`, `billing-celery` (`--pool=solo`), `billing-celery-beat` |
| Redis | db4 (broker Celery), db10 (cache Django) |
| Env | `/run/billing/.env` (tmpfs, 640 django:www-data) ← SSM `/billing/prod` |
| vhost | `billing-api.foxugly.com` → `deploy/nginx/billing-api.conf` |
| Rôle OIDC | `billing-deploy`, épinglé sur `repo:Foxugly/billing_server:environment:production` |

Celery livre les entitlements aux apps ; beat planifie la reprise des livraisons en attente
(toutes les 5 min), la réconciliation (04:30) et la purge de rétention (05:00) —
`CELERY_BEAT_SCHEDULE` dans `config/settings/base.py`. Les quatre units sont installées par root
depuis le miroir nu `/var/lib/fleet-deploy/billing_server.git` au SHA poussé (`deploy.yml`), jamais
depuis l'arbre django.

Python : le venv (`.venv`, utilisé par les trois units applicatives) tourne sur
`/usr/bin/python3.14` (deadsnakes ; le `python3` système reste en 3.12). `deploy/deploy.sh`, lancé
après le `git reset` du checkout, le reconstruit **sur place** s'il est sur une autre version
(l'ancien gardé en `.venv.prev`, remis en place si l'installation du verrou échoue), puis relance
gunicorn, celery et celery-beat.

## Recharger les secrets après un changement en SSM

    sudo systemctl restart billing-env-fetch
    sudo systemctl restart billing-gunicorn billing-celery billing-celery-beat

## Diagnostic

    systemctl status billing-gunicorn billing-celery billing-celery-beat
    journalctl -u billing-gunicorn -n 100 --no-pager
    journalctl -u billing-celery -n 100 --no-pager
    curl -s https://billing-api.foxugly.com/health/
