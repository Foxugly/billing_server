# CLAUDE.md

billing_server — facturation centralisée de la flotte (Django + DRF + dj-stripe + Celery, **WSGI/gunicorn**,
`127.0.0.1:8007`, `billing-api.foxugly.com`). Architecture et dev local : `README.md`, déploiement :
`deploy/DEPLOY.md`. Modèle flotte/ops : `~/.claude/CLAUDE.md` et `foxugly-ops/OPERATIONS.md`.

## Commandes

```bash
.venv/Scripts/python -m pytest -q          # suite complète (CI : PostgreSQL 16, Python 3.12)
ruff check .                               # ruff==0.16.10, comme la CI
.venv/Scripts/python manage.py check       # inclut les checks admin_2fa.E00x
```

## Admin 2FA

`/admin/` exige un second facteur TOTP pour **tous** les comptes staff (django-otp 1.7.5 + qrcode 8.2,
app `admin_2fa/`, implémentation de référence de la flotte : Poker_server). La console JWT
(`/api/v1/auth/`, `/api/v1/admin/`) et l'API HMAC des apps ne voient rien de tout cela.

- **Câblage** : `"admin_2fa.apps.OTPAdminConfig"` *remplace* `"django.contrib.admin"` (il fait
  d'`admin.site` un `Admin2FASite`, dont `has_permission` exige aussi `user.is_verified()`) ; plus
  `django_otp`, `otp_totp`, `otp_static`, `admin_2fa` dans INSTALLED_APPS et
  `django_otp.middleware.OTPMiddleware` juste après `AuthenticationMiddleware`. `OTP_TOTP_ISSUER` est
  le libellé dans l'appli d'authentification. Une pièce manquante fait échouer `manage.py check`
  (`admin_2fa.E001-E004`). Un `AdminSite` personnalisé devrait hériter d'`Admin2FASite`.
- **Parcours** : mot de passe sur `/admin/login/` → `/admin/2fa/setup/` (1re fois : QR + clé, un code
  pour confirmer, 10 codes de secours à usage unique affichés **une seule fois**) ou `/admin/2fa/verify/`
  (code TOTP ou code de secours). Templates admin rendus côté serveur, CSRF, `never_cache`, aucun
  script ni style inline.
- **Force brute** : django-otp freine chaque appareil (1 s, 2 s, 4 s… après chaque mauvais code) et
  nginx met `/admin/2fa/` dans la même zone `billing_admin_login` que le login (10 r/min par IP).
- **Téléphone perdu / compte bloqué** (sur la box, comme l'unité de l'app le lancerait) :

  ```bash
  sudo systemd-run --wait --pipe --quiet --uid=django --gid=www-data \
    -p EnvironmentFile=/run/billing/.env -p WorkingDirectory=/var/www/django_websites/billing_server \
    /var/www/django_websites/billing_server/.venv/bin/python manage.py admin_2fa_reset <email>
  ```

  supprime les appareils de l'utilisateur (réenrôlement au prochain login admin) ; `--unlock` vide
  seulement le délai de throttling.
- **Tests de pages admin** : `admin_2fa.testing.force_admin_login(client, user)`, pas
  `client.force_login` (l'admin redirigerait vers le second facteur).
- **Secrets** : ne jamais journaliser ni envoyer à Sentry la clé TOTP, le QR ou un code de secours —
  les vues les gardent dans des `Redacted` et le champ du formulaire s'appelle `token` (nettoyé par
  Sentry).
