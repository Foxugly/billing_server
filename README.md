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

## Authentification de la console (JWT opérateur)

Le refresh de la console voyage dans un **cookie HttpOnly** quand la requête porte
`X-Auth-Mode: cookie` (design du pilote PushIT_server #69, code dans `accounts/refresh_cookie.py`) :
`billing_refresh`, `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth/`, sans `Domain`, retiré du
corps JSON. Login (`remember_me: false` → cookie de session), `token/refresh/` (lit le cookie si le
corps n'a pas de `refresh` ; un `refresh` dans le corps sert à la migration unique depuis
localStorage), `password/change/` (repose le cookie) et `token/blacklist/` (sans Bearer, efface
toujours le cookie, 204). Toute requête en mode cookie exige un `Origin` présent dans
`CORS_ALLOWED_ORIGINS` (403 `origin_not_allowed`) ; CORS admet les credentials pour cette liste
explicite seulement, plus l'en-tête `x-auth-mode`. Sans l'en-tête, le contrat « jetons dans le
corps » est inchangé. L'API HMAC des apps n'est pas concernée. Aucun paramètre SSM à ajouter.
