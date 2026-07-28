# Billing — Lot L3 : signature HMAC, livraison des droits et API service-à-service

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rendre le service utilisable par les applications de la flotte : une API
service-à-service signée, une file de livraison des droits qui réessaie, et une réconciliation
quotidienne qui rattrape ce qui s'est perdu.

**Architecture:** Toute communication entre billing et une app est signée en HMAC-SHA256, dans
les deux sens. Le central pousse les droits via Celery (Redis db4) avec reprise exponentielle ;
les apps appellent l'API pour ouvrir un Checkout, un portail, lire le catalogue ou l'historique.
Un seul appel synchrone au central existe sur un chemin utilisateur : le pull de secours au
retour du Checkout.

**Tech Stack:** Celery 5 + Redis (broker **db4**, cache Django **db5** — db0-db3 sont pris,
vérifié le 2026-07-28), DRF, `stripe` SDK (mocké dans les tests).

**Branche :** `feat/l3-hmac-delivery-api` → PR. **Ne jamais pousser sur `main`.**

## Global Constraints

- Reprendre les contraintes L1/L2.
- **Aucun appel réseau dans les tests.** Stripe est mocké, la livraison HTTP aussi.
- **Le cache Django doit être Redis, pas LocMem.** L'anti-rejeu s'appuie dessus : avec LocMem,
  chaque worker gunicorn aurait son propre cache et un rejeu passerait une fois par worker.
- **Signature = HMAC-SHA256 sur `f"{timestamp}.{corps_brut}"`**, en-têtes `X-Foxugly-App`,
  `X-Foxugly-Timestamp`, `X-Foxugly-Signature: sha256=<hex>`, fenêtre **300 s**, comparaison en
  temps constant, anti-rejeu par empreinte de signature en cache.
- **Rotation du secret :** `App` gagne `previous_shared_secret` + `secret_rotated_at`. L'ancien
  secret est accepté pendant **24 h** après rotation, puis rejeté. Sans ça, une rotation
  couperait le service le temps que l'app redéploie.
- **Le corps brut doit être lu AVANT que DRF ne parse la requête.** La vérification vit donc
  dans une `permission_class` (qui s'exécute avant l'accès à `request.data`) : lire `request.body`
  après le parsing lève `RawPostDataException`.

---

### Task 1 : Signature HMAC — signer, vérifier, rejeter

**Files:** `core/signing.py`, `core/tests/test_signing.py` ; `core/models.py` (rotation) ;
`config/settings/base.py` (CACHES Redis).

**Produces:** `sign_payload(secret, body: bytes, timestamp: int) -> str`,
`verify_signature(app, body, timestamp, signature) -> bool`, `SIGNATURE_WINDOW_SECONDS = 300`,
et `App.rotate_secret()`.

- [ ] Écrire `core/tests/test_signing.py` : signature valide ; mauvais secret ; horodatage hors
  fenêtre (avant **et** après) ; corps altéré d'un octet ; rejeu refusé au second passage ;
  ancien secret accepté pendant 24 h après rotation puis refusé ; signature malformée sans
  `sha256=` ; en-tête absent.
- [ ] Vérifier qu'ils échouent.
- [ ] Implémenter `core/signing.py` avec `hmac.compare_digest`.
- [ ] Ajouter à `App` : `previous_shared_secret`, `secret_rotated_at`, `rotate_secret()`.
- [ ] Configurer `CACHES` sur Redis db5, avec repli LocMem si `REDIS_URL` est absent (dev/test).
- [ ] Migration, tests verts, commit.

### Task 2 : Permission DRF et point d'entrée signé

**Files:** `core/permissions.py`, `core/api_urls.py`, `core/api_views.py`, tests.

**Produces:** `HasValidAppSignature` (pose `request.billing_app`), et
`POST /api/v1/ping/` qui renvoie `{"app": "<slug>"}` — le moyen de tester la connectivité et le
secret depuis la console sans effet de bord.

- [ ] Tests : requête signée → 200 ; non signée → 401 ; app inactive → 403 ; app inconnue → 401.
- [ ] Implémenter, câbler `path("api/v1/", include("core.api_urls"))`, tests verts, commit.

### Task 3 : Celery et la file de livraison

**Files:** `config/celery.py`, `config/__init__.py`, `core/tasks.py`, tests ;
`deploy/systemd/billing-celery.service`, `billing-celery-beat.service` ;
`.github/workflows/deploy.yml` (boucle des units) ; `deploy/deploy.sh` (redémarrages) ;
`/etc/sudoers.d/billing-deploy` (à étendre **hors bande**).

**Produces:** `deliver_entitlement(delivery_id)` — tâche Celery idempotente, et
`RETRY_SCHEDULE = [60, 300, 900, 3600, 21600, 86400]`.

- [ ] Tests avec la livraison HTTP mockée : succès → `delivered` + `delivered_at` ; `409` de
  l'app → considéré comme livré (elle a déjà vu ce `delivery_id`) ; erreur réseau → `attempts+1`,
  `next_retry_at` posé selon le rang ; après le dernier rang → `failed` ; une livraison déjà
  `delivered` n'est pas renvoyée ; app inactive → aucune livraison.
- [ ] Implémenter, ajouter les units, étendre le workflow et `deploy.sh`, tests verts, commit.
- [ ] ⚠️ **Le sudoers de la box doit être étendu** aux deux nouvelles units **avant** que le
  déploiement ne tente de les redémarrer, sinon le deploy échoue à la dernière étape.

### Task 4 : API de lecture — catalogue et droits

**Files:** `core/api_views.py`, `core/serializers.py`, tests.

**Produces:** `GET /api/v1/plans/?app=<slug>` et
`GET /api/v1/entitlements/<slug>/<external_user_id>/` (recalcul à la demande — c'est le pull de
secours du retour Checkout, §6.5).

- [ ] Tests : catalogue filtré sur `public=True` et `active=True`, trié par `sort_order`, avec
  les montants des prix Stripe mirés ; un plan sans prix configuré est exclu ; le pull renvoie
  l'état à jour et crée l'entitlement s'il n'existe pas.
- [ ] Implémenter, tests verts, commit.

### Task 5 : API Stripe — checkout, portail, historique

**Files:** `core/api_views.py`, `core/stripe_gateway.py`, tests.

**Produces:** `POST /api/v1/checkout/`, `POST /api/v1/portal/`, `GET /api/v1/history/`.

- [ ] Tests, SDK Stripe mocké : plan inconnu → 400 ; intervalle sans prix → 400 ;
  `success_url` hors de `App.base_url` et hors du domaine de l'app → 400 (anti-redirection
  ouverte) ; Stripe non configuré → 503 ; portail sans customer → 400 ; l'`AppCustomer` est créé
  et réutilisé ; `metadata` et `client_reference_id` sont bien posés sur la session.
- [ ] Implémenter avec `automatic_tax`, `customer_update={"address": "auto"}`,
  `tax_id_collection` (§17), tests verts, commit.

### Task 6 : Réconciliation

**Files:** `core/management/commands/sync_entitlements.py`, tests ; `config/settings/base.py`
(planification beat).

**Produces:** `sync_entitlements [--app slug] [--push-diff]` + une tâche quotidienne.

- [ ] Tests : un entitlement dont la grâce a expiré est recalculé et repoussé ; un entitlement
  inchangé ne produit **aucune** livraison ; `--app` restreint bien ; sans `--push-diff` rien
  n'est émis.
- [ ] Implémenter, tests verts, commit, PR.

---

## Critère de sortie

1. `pytest -q` vert, aucun appel réseau
2. CI de la PR verte
3. `sudo -l -U django` sur la box montre les deux nouvelles units **avant** le premier déploiement
   qui les redémarre
4. Après merge : `systemctl is-active billing-celery billing-celery-beat` → `active`

## Ce que ce lot ne fait PAS

La console Angular (L5), la mise en service Stripe réelle (L6), la migration de Poker (L4).
