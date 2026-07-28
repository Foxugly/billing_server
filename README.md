# billing_server — facturation centralisée de la flotte Foxugly

Service Django qui détient Stripe pour l'ensemble des sites Foxugly : il reçoit les webhooks,
mire les objets Stripe (dj-stripe) et pousse aux applications les droits (entitlements) qui en
découlent. Les sites de la flotte ne parlent jamais à Stripe directement.

- Design : `docs/superpowers/specs/2026-07-28-billing-central-design.md`
- Ops : `OPERATIONS.md` du repo `Foxugly/foxugly-ops` (source de vérité)
- Port gunicorn : `127.0.0.1:8007` · vhosts `billing-api.foxugly.com` / `billing.foxugly.com`

## Développement

    python3.12 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/pytest -q
    .venv/bin/python manage.py runserver

La box de production tourne en **Python 3.12** — c'est la version que la CI utilise et qui fait
foi. Un venv local sur une version plus récente est toléré (comme sur Poker), mais c'est la CI
qui valide.
