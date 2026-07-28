# Billing centralisé de la flotte Foxugly — design

**Date :** 2026-07-28
**Statut :** design validé, prêt pour le plan d'implémentation
**Repos concernés :** `Foxugly/billing_server` (nouveau), `Foxugly/billing_frontend` (nouveau),
`Foxugly/Poker_server` + `Foxugly/Poker_frontend` (site pilote, migration)

---

## 1. Objectif

Centraliser dans un service unique tous les paiements de la flotte Foxugly. Un seul endroit
détient les clés Stripe, reçoit les webhooks, mire les objets Stripe en base (dj-stripe) et
diffuse aux applications les **droits** (entitlements) qui en découlent. Chaque site de la
flotte reste maître de son propre gating fonctionnel, mais ne parle plus jamais à Stripe.

### Non-objectifs

- **Pas de SSO.** Le billing n'est pas un fournisseur d'identité ; les 7 applications gardent
  leur `CustomUser` email-only (OPERATIONS.md §3.16).
- **Pas de compte utilisateur billing.** Aucun visiteur ne se connecte au service billing ;
  le client, du point de vue du service, c'est *l'application* qui l'appelle.
- **Pas de dons.** Le soutien libre reste sur https://github.com/sponsors/Foxugly.
- **Pas de metering / facturation à l'usage** en v1.
- **Pas de Stripe Connect / multi-comptes.** Un seul compte Stripe pour toute la flotte.

---

## 2. État des lieux (vérifié le 2026-07-28)

- **Aucun autre site de la flotte ne touche Stripe.** Grep sur `D:\Projects` : uniquement des
  libs vendorées (fontawesome, social-core, pydantic).
- **Poker a déjà un billing Stripe complet** (`Poker_server/billing/`, PR #8) écrit avec le SDK
  `stripe` brut, **pas** dj-stripe : modèle `Subscription` local, plans `team1`/`team5` ×
  `monthly`/`yearly`, `CheckoutView`, `PortalView`, `BillingHistoryView` (lecture live Stripe),
  `WebhookView` signé, gating `user_is_paid` / `user_quota` / `team_is_paid` / `paid_required`
  (HTTP 402), bypass staff « compte offert » (`subscription_bypass` sur le user), et côté SPA
  les pages `features/pricing` + `features/account/subscription` + `core/billing/gating.ts`.
- **Ce billing est armé en production, pas inerte.** `/run/poker/.env` porte une clé
  `sk_live_…` (107 caractères) et les **4** `STRIPE_PRICE_*` sont renseignés, donc
  `billing_configured()` renvoie `True` et le gating est actif sur poker.foxugly.com.
- **`billing_subscription` contient 0 ligne** : aucune souscription n'a jamais été prise.
  → **Aucune donnée à migrer.** La bascule peut être une coupure franche.
- **Un endpoint webhook Stripe est donc déjà enregistré** côté Stripe vers
  `poker-api.foxugly.com/api/billing/webhook/`. Il devra être re-pointé au cutover.

---

## 3. Décisions actées

| Sujet | Décision |
|---|---|
| Modèle de revenu v1 | Abonnements récurrents + paiements one-shot. Dons hors scope (GitHub Sponsors). |
| Diffusion des droits | **Push** signé du central vers chaque app → **cache local** dans l'app. Pas de dépendance runtime. |
| Identité client | Pas de compte billing. Le pont est `(app, external_user_id)` → un `Customer` Stripe. |
| UI de paiement | **Stripe Checkout + Customer Portal hébergés.** Charge PCI nulle, CSP simple. |
| Rôle du SPA Angular | **Console d'admin opérateur**, login staff uniquement. |
| Site pilote | **Poker**, migré en consommateur. |
| Compte Stripe | **Unique** (Foxugly SRL). Produits taggés `metadata.app = poker|tm|…`. |
| TVA | **Stripe Tax activé** (calcul, collecte, OSS, factures conformes). |
| Relation avec l'app billing de Poker | Le central devient le seul à parler à Stripe ; l'app Poker devient un consommateur qui garde son modèle local et son gating. |
| Retour du Checkout | Pull synchrone au central sur `?billing=success`, pas de polling (§6.5). |
| Échec de paiement | Grâce en `past_due` avant fermeture — `BILLING_GRACE_DAYS`, **défaut 7**, réglable en SSM (§6.6). |
| Sorties comptables | **PDF Stripe *et* export CSV structuré**, les deux (§16). |
| Facturation directe (consulting) | Prévue au lot **L7** ; `AppCustomer.app` nullable posé **dès la première migration** (§5, §16). |
| Coordonnées | Collecte email + nom + adresse + n° TVA. **Téléphone non collecté** (§17). |

---

## 4. Placement dans la flotte

Mirroir de `quizonline` (implémentation de référence), checklist OPERATIONS.md §3.12 intégrale.

| Élément | Valeur |
|---|---|
| App id | `billing` |
| Repos | `Foxugly/billing_server`, `Foxugly/billing_frontend` |
| Chemins locaux | `D:\Projects\PycharmProjects\billing_server`, `D:\Projects\WebstormProjects\billing_frontend` |
| Chemin serveur | `/var/www/django_websites/billing_server` (+ `billing_frontend`) |
| Port gunicorn | **8007** (premier libre ; 8000–8006 pris) |
| Vhosts | `billing-api.foxugly.com` (API) · `billing.foxugly.com` (console) |
| TLS | Cert wildcard `*.foxugly.com` existant — rien à créer (§3.6) |
| Base | PostgreSQL box-local, db + rôle `billing`, convention `DB_*` 6 variables (§3.13) |
| Secrets | SSM `/billing/prod` (backend) + `/billing-frontend/prod` (SPA), noms nus (§3.5, §3.14) |
| Déploiement | GitHub Actions OIDC → SSM, rôles `billing-deploy` / `billing-frontend-deploy` (§3.11) |
| Units systemd | `billing-env-fetch` (oneshot root), `billing-gunicorn`, `billing-celery`, `billing-celery-beat` |
| Santé | `/health/` avec check DB, monitor UptimeRobot par vhost (§3.9) |
| Sentry | projets `billing-backend` + `billing-frontend`, org `foxugly-srl` (§3.8) |

**Stack backend :** Django 6.0.6, DRF 3.17, simplejwt, **dj-stripe 2.11.0** (publié le
2026-06-26, classifier `Framework :: Django :: 6.0`, `django>=5.2` — compatible vérifié),
`stripe` SDK, Celery + Redis (index **4** — 3 est pris par les Channels de Poker), psycopg 3,
drf-spectacular, sentry-sdk.

**Stack frontend :** Angular + PrimeNG + Transloco, conforme à `STANDARD-frontend-layout.md`.

---

## 5. Modèle de domaine

dj-stripe fournit le **miroir en base** des objets Stripe (`Customer`, `Product`, `Price`,
`Subscription`, `Invoice`, `Event`, `WebhookEventTrigger`). On ne réécrit aucune de ces tables
et on ne les modifie jamais à la main : Stripe reste le système de référence pour l'argent.

Au-dessus, cinq modèles métier dans l'app Django `core` du service :

### `App` — les tenants
| Champ | Type | Rôle |
|---|---|---|
| `slug` | `SlugField(unique)` | `poker`, `tm`, `pushit`, … |
| `name` | `CharField` | libellé console |
| `base_url` | `URLField` | racine API de l'app (ex. `https://poker-api.foxugly.com`) |
| `entitlement_path` | `CharField` | chemin du endpoint de réception (défaut `/api/billing/entitlement/`) |
| `shared_secret` | `CharField` | secret HMAC S2S, rotationnable depuis la console |
| `active` | `BooleanField` | coupe les livraisons sans supprimer l'historique |

### `Plan` — le catalogue
| Champ | Type | Rôle |
|---|---|---|
| `app` | FK `App` | |
| `code` | `CharField` | `team1`, `team5`, … unique avec `app` |
| `name`, `description` | | affichés par le SPA du site |
| `price_monthly`, `price_yearly` | FK `djstripe.Price` (nullable) | mapping vers Stripe |
| `quotas` | `JSONField` | ex. `{"teams": 1}` — poussé tel quel à l'app |
| `sort_order`, `public`, `active` | | affichage / retrait du catalogue |

Remplace les 6 variables `STRIPE_PRICE_*` + le dict `PLAN_QUOTAS` codés en dur chez Poker.

### `AppCustomer` — le pont
| Champ | Type |
|---|---|
| `app` | FK `App`, **nullable** — `NULL` = client direct (voir plus bas) |
| `external_user_id` | `CharField(blank)` — l'id du user **dans l'app** ; vide pour un client direct |
| `email` | `EmailField` — dernier email connu, pour la recherche console |
| `customer` | FK `djstripe.Customer` |

`unique_together = (app, external_user_id)`. C'est ce modèle qui permet de se passer d'un
compte utilisateur billing : l'app dit « mon user 42 », le central sait quel `Customer` Stripe
c'est.

**`app` est nullable dès la première migration.** Un `AppCustomer` avec `app=NULL` est un
**client direct** : quelqu'un que Foxugly facture sans qu'il soit utilisateur d'un site de la
flotte (prestation de consulting, cf. §16). Aucun `Entitlement` n'est calculé pour lui et
aucune livraison n'est émise. La colonne est posée maintenant, même si la facturation directe
n'arrive qu'au lot L7 : l'ajouter après coup imposerait une migration sur une table déjà
peuplée en production. L'alternative d'un pseudo-tenant `App(slug="direct")` est rejetée —
elle obligerait à filtrer « sauf direct » dans chaque requête d'entitlement.

**Décision explicite — un seul `Customer` Stripe par email, partagé entre les apps.** Si
`alice@x.be` s'abonne sur Poker puis sur TM, on crée deux `AppCustomer` (un par app) qui
pointent vers **le même** `djstripe.Customer`, résolu par email à la création. C'est le sens
même de la centralisation : un seul moyen de paiement enregistré, un seul historique de
factures, un seul portail. **Conséquence assumée :** le Customer Portal ouvert depuis Poker
montre aussi l'abonnement TM et permet de le résilier. L'alternative (un `Customer` par couple
app+email) isolerait les portails mais ferait ressaisir la carte à chaque site et éclaterait la
facturation — elle est rejetée.

### `Entitlement` — l'état dérivé
| Champ | Type |
|---|---|
| `app`, `external_user_id` | (unique ensemble) |
| `is_paid` | `BooleanField` |
| `status` | `CharField` — statut Stripe (`active`, `trialing`, `past_due`, `canceled`, …) |
| `plan_code`, `interval` | `CharField` |
| `quotas` | `JSONField` |
| `current_period_end` | `DateTimeField(null)` |
| `source` | `CharField` — `stripe` \| `manual` \| `bypass` |
| `computed_at` | `DateTimeField` |

C'est **le seul objet poussé** aux applications. `source=manual` permet d'offrir un accès
depuis la console sans passer par Stripe (le bypass local de Poker reste par ailleurs valable).

### `EntitlementDelivery` — la file de livraison
| Champ | Type |
|---|---|
| `id` | `UUIDField` — le `delivery_id`, clé d'idempotence côté app |
| `entitlement` | FK |
| `payload` | `JSONField` — figé à l'émission |
| `status` | `pending` \| `delivered` \| `failed` |
| `attempts`, `last_error`, `next_retry_at`, `delivered_at` | |

Rejouable manuellement depuis la console et depuis le Django admin.

---

## 6. Les flux

### 6.1 Achat (Checkout)

```
SPA Poker  ──POST /api/billing/checkout/ {plan, interval} (JWT)──▶  Poker backend
Poker backend ──POST /api/v1/checkout/ (HMAC S2S)──▶  billing
   {app, external_user_id, email, plan, interval, success_url, cancel_url}
billing : get_or_create AppCustomer + Customer Stripe
        → stripe.checkout.Session.create(mode=subscription, automatic_tax=on, …)
        ◀── {url}
Poker backend ◀── {url}  →  SPA  →  redirection vers Stripe
```

Poker ne détient **aucune** clé Stripe. Les `success_url` / `cancel_url` restent celles du SPA
appelant (`{FRONTEND_BASE_URL}/teams?billing=success|cancel` pour Poker), transmises par l'app
et validées côté central contre `App.base_url` et une liste d'origines autorisées.

### 6.2 Webhook Stripe → recalcul → push

```
Stripe ──▶ POST billing-api/api/v1/stripe/webhook/   (signature Stripe, dj-stripe)
   dj-stripe persiste l'Event (idempotence native sur event.id) et met à jour son miroir
   → handler : recompute_entitlement(app, external_user_id)
   → si changement : EntitlementDelivery(pending) + tâche Celery
   → POST {App.base_url}{App.entitlement_path}   (HMAC Foxugly)
   → l'app met à jour son cache local
```

Événements écoutés :

| Événement | Effet |
|---|---|
| `checkout.session.completed` | création de l'abonnement, premier calcul d'entitlement |
| `customer.subscription.created` / `.updated` / `.deleted` | recalcul (changement de plan, résiliation, fin de période) |
| `invoice.paid` | renouvellement — `current_period_end` avance |
| `invoice.payment_failed` | passage en `past_due` (voir la période de grâce, §6.6) |
| `customer.updated` | **coordonnées de facturation modifiées depuis le portail** — pas d'impact sur l'entitlement, mais indispensable pour que la console ne fige pas l'adresse au jour de la souscription |
| `customer.tax_id.created` / `.deleted` | numéro de TVA ajouté ou retiré — change le régime (autoliquidation) |

Les trois derniers ne déclenchent pas de livraison : ils ne modifient que le miroir dj-stripe.

Retries Celery : backoff exponentiel (1 min, 5 min, 15 min, 1 h, 6 h, 24 h) puis `failed`,
visible et rejouable dans la console. Le central répond **200 à Stripe dès que l'événement est
persisté** — la livraison aux apps est asynchrone et ne doit jamais faire échouer le webhook.

### 6.3 Portail client

Même chemin que le checkout : `POST /api/v1/portal/ {app, external_user_id, return_url}` →
`stripe.billing_portal.Session.create(customer=…)` → `{url}`. Aucun login billing nécessaire :
la session portail est un lien signé, à durée de vie courte, créé côté serveur.

### 6.4 Réconciliation (le filet)

Commande `manage.py sync_entitlements [--app slug] [--push-diff]`, plus un beat quotidien :
recalcule chaque `Entitlement` à partir du miroir dj-stripe et repousse ceux qui diffèrent du
dernier payload livré. Couvre le cas d'un push définitivement perdu ou d'une app restée
longtemps hors ligne.

### 6.5 Retour du Checkout — la course, et la règle qui la tranche

La redirection du navigateur vers `success_url` et le webhook Stripe partent **en parallèle** et
rien ne garantit leur ordre. Le webhook arrive typiquement en moins d'une seconde, mais s'il est
en retard, l'utilisateur revient sur une page qui lui annonce qu'il n'a pas d'abonnement — juste
après avoir payé.

**Règle figée :** quand le SPA revient avec `?billing=success`, l'application ne se contente
**pas** de lire son cache local. Elle appelle en synchrone
`GET /api/v1/entitlements/{app}/{external_user_id}/` au central, qui recalcule à la demande
(en interrogeant Stripe si son propre miroir est en retard), renvoie l'état à jour, et
déclenche la mise à jour du cache. Déterministe, sans polling ni temporisation arbitraire.

Ce pull est **le seul** appel synchrone au central sur un chemin utilisateur. S'il échoue, l'app
retombe sur son cache et affiche un message d'attente : le push finira d'arriver.

### 6.6 Échec de paiement — période de grâce

Un `invoice.payment_failed` fait passer l'abonnement en `past_due` chez Stripe, qui continue de
relancer la carte pendant plusieurs jours. Couper l'accès au premier échec punit un client dont
la carte a simplement expiré.

**Règle :** `is_paid` reste `True` pendant **`BILLING_GRACE_DAYS` jours** après l'entrée en
`past_due` (`grace_until = computed_at + N j`, porté dans le payload). Passé ce délai, **ou** dès
que Stripe bascule l'abonnement en `canceled` / `unpaid` (ce qui peut survenir avant), `is_paid`
tombe à `False` et le push ferme l'accès. La relance client est faite par Stripe (emails de
recouvrement natifs) — on ne réimplémente aucune séquence d'emails.

`BILLING_GRACE_DAYS` est une variable SSM, **défaut 7**, modifiable sans redéploiement ; `0`
désactive la grâce (fermeture au premier échec). Un beat quotidien recalcule les entitlements
dont le `grace_until` vient d'expirer — sans lui, un abonnement en grâce ne recevrait plus aucun
événement Stripe et resterait ouvert indéfiniment.

Côté application, cela ne change rien : `PAID_STATUSES` reste `{active, trialing}` chez Poker,
mais l'app ne décide plus — elle applique le `is_paid` reçu.

### 6.7 Historique de facturation

`GET /api/billing/history/` chez Poker devient un proxy vers
`GET /api/v1/history/?app=&external_user_id=`, servi depuis le miroir dj-stripe. Gain par
rapport à l'existant : plus d'appel Stripe en direct à chaque affichage de page. Les liens de
facture (`hosted_invoice_url`, `invoice_pdf`) restent des URLs Stripe signées.

---

## 7. Contrats d'API

Toutes les routes S2S sont sous `/api/v1/` sur `billing-api.foxugly.com` et exigent la
signature HMAC. Aucune n'est accessible depuis un navigateur.

| Méthode | Route | Corps / Query | Réponse |
|---|---|---|---|
| POST | `/api/v1/checkout/` | `{app, external_user_id, email, plan, interval, success_url, cancel_url}` | `{url}` |
| POST | `/api/v1/portal/` | `{app, external_user_id, return_url}` | `{url}` |
| GET | `/api/v1/plans/` | `?app=poker` | `[{code, name, description, quotas, prices:{monthly:{id,amount,currency},yearly:{…}}}]` |
| GET | `/api/v1/entitlements/{app}/{external_user_id}/` | — | l'entitlement courant (pull de secours) |
| GET | `/api/v1/history/` | `?app=&external_user_id=` | `{subscriptions:[…], invoices:[…]}` |
| POST | `/api/v1/stripe/webhook/` | (Stripe) | 200 — signature **Stripe**, pas HMAC Foxugly |
| GET | `/health/` | — | 200 + check DB |

Les routes de la console (`/api/admin/…`) sont authentifiées en JWT simplejwt avec
`IsAdminUser`, et ne sont **pas** signées HMAC.

### Payload d'entitlement poussé

```json
{
  "delivery_id": "0f3a…-uuid",
  "issued_at": "2026-07-28T10:12:03Z",
  "app": "poker",
  "external_user_id": "42",
  "is_paid": true,
  "status": "active",
  "plan": "team1",
  "interval": "monthly",
  "quotas": {"teams": 1},
  "current_period_end": "2026-08-28T10:12:03Z",
  "grace_until": null,
  "stripe_customer_id": "cus_…",
  "source": "stripe"
}
```

L'application réceptrice répond `200` (traité) ou `409` (`delivery_id` déjà vu → no-op, réponse
succès aussi côté central). Toute autre réponse déclenche un retry.

---

## 8. Sécurité service-à-service

- **HMAC-SHA256** sur `f"{timestamp}.{raw_body}"`, secret = `App.shared_secret`.
- En-têtes : `X-Foxugly-Timestamp`, `X-Foxugly-Signature: sha256=<hex>`, `X-Foxugly-App: <slug>`.
- Fenêtre de tolérance **300 s** ; hors fenêtre → 401.
- **Anti-rejeu** : le couple `(app, signature)` est mémorisé en cache Redis pendant la fenêtre.
- Comparaison en temps constant (`hmac.compare_digest`).
- Le secret vit en SSM des deux côtés : `/billing/prod/APP_SECRET_POKER` au central,
  `BILLING_APP_SECRET` dans `/poker/prod`. Rotation : la console génère le nouveau secret,
  le central accepte l'ancien **et** le nouveau pendant la fenêtre de rotation.
- Le trafic passe par nginx en HTTPS entre vhosts du même box — pas de mTLS (§3.10 : la
  surface est déjà limitée à localhost derrière nginx).
- Idempotence : `event.id` Stripe côté central (fourni par dj-stripe), `delivery_id` côté app.

---

## 9. Impact sur Poker (le pilote)

### Conservé sans modification

- Le modèle `billing.Subscription` — il **devient le cache local** de l'entitlement.
- Tout `billing/service.py` : `billing_configured()`, `user_is_paid()`, `user_quota()`,
  `team_is_paid()`, `paid_required()` (402), la constante `UNLIMITED`, le bypass
  `subscription_bypass`, et surtout le **mode inerte** (sans configuration → tout ouvert).
- Les pages SPA `features/pricing` et `features/account/subscription`, `core/billing/gating.ts`,
  les vues staff de bypass.
- Les tests de gating existants (`billing/tests/test_billing.py`).

### Remplacé

| Avant | Après |
|---|---|
| `CheckoutView` appelle Stripe | proxy S2S vers `/api/v1/checkout/` |
| `PortalView` appelle Stripe | proxy S2S vers `/api/v1/portal/` |
| `BillingHistoryView` lit Stripe en live | proxy S2S vers `/api/v1/history/` |
| `WebhookView` (signature Stripe) | `EntitlementView` (HMAC Foxugly, push entrant) |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, 4 × `STRIPE_PRICE_*` | `BILLING_BASE_URL`, `BILLING_APP_SECRET` |
| `PLAN_QUOTAS` en dur + `price_for()` / `plan_for_price()` | `quotas` reçu dans le payload, fallback local ; catalogue via `/api/v1/plans/` |

`billing_configured()` teste désormais la présence de `BILLING_BASE_URL` + `BILLING_APP_SECRET`.
La sémantique inerte est préservée à l'identique, ce qui permet de **déployer la migration sans
rien activer**, puis de basculer par un simple geste SSM.

Le paquet `stripe` disparaît des dépendances de Poker.

### Champs de `Subscription` alimentés par le push

`stripe_customer_id` n'a plus de sens côté Poker (il ne s'en sert que pour savoir s'il peut
ouvrir le portail) : on le remplace par un booléen `can_manage` fourni dans le payload, ou on
le conserve en champ opaque rempli par le central. **Décision : le conserver**, rempli par le
central, pour minimiser le delta sur `SubscriptionView` et sur le SPA.

---

## 10. Console Angular (`billing.foxugly.com`)

Login **staff-only** : simplejwt + garde `is_staff`, aucune page d'inscription, aucun mot de
passe oublié public. Conforme à `STANDARD-frontend-layout.md` (topmenu canonique, ordre d'actions
`theme → language → user`, `ThemeService` + anti-FOUC, `app-language-switcher` 5 langues,
`app-page-header` unique, `app-form-footer`, tokens SCSS, BEM, responsive, footer versionné).

**Périmètre de la page Clients — précision qui évite un malentendu.** Le central ne connaît un
individu qu'à partir du moment où **un tunnel de paiement a été ouvert pour lui** (création de
l'`AppCustomer` à l'étape checkout). La liste contient donc les payants et les abandons de
panier, mais **pas** les utilisateurs d'un site qui ne sont jamais allés sur la page tarifs.
C'est une liste de clients au sens facturation, **pas un annuaire d'utilisateurs** — celui-ci
reste dans le Django admin de chaque site, qui en est le responsable légitime.

| Page | Contenu |
|---|---|
| Dashboard | MRR, abonnements actifs par app, nouveaux / résiliations sur 30 j, livraisons en échec |
| Apps | CRUD, rotation du secret, bouton « tester la connectivité » (ping signé) |
| Plans | CRUD, mapping vers les `Price` Stripe, quotas, ordre, visibilité |
| Clients | Recherche par email ou app → détail : coordonnées de facturation, abonnements **toutes apps confondues**, factures, entitlement courant, historique des livraisons, action « offrir » (`source=manual`), lien profond vers le Django admin du site (`App.base_url` + `external_user_id`) |
| Événements Stripe | Liste des `djstripe.Event` avec statut de traitement et rejeu |
| Livraisons | File `EntitlementDelivery`, filtres, rejeu manuel |

---

## 11. Tests

**Central** (pytest + pytest-django) :
- Signature HMAC : valide / secret erroné / horodatage hors fenêtre / rejeu → 401 ou no-op.
- Webhook : fixtures JSON d'événements Stripe → recalcul d'entitlement attendu ; double
  livraison du même `event.id` → un seul recalcul.
- `recompute_entitlement` : matrice statuts Stripe × plans → `is_paid` / `quotas` attendus.
- Livraison : succès, échec réseau → retry programmé, 409 de l'app → marqué délivré.
- `sync_entitlements` : divergence détectée et repoussée.
- API S2S : plan inconnu → 400, app inactive → 403, `success_url` hors origine → 400.

**Poker** :
- Les tests de gating existants doivent passer **sans modification** (garantie de non-régression).
- Nouveau : `EntitlementView` — signature invalide → 401, `delivery_id` déjà vu → no-op,
  payload valide → `Subscription` mise à jour.
- Proxys : central injoignable → 503 propre, jamais de 500.

Aucun appel réseau Stripe dans les tests : fixtures JSON + `unittest.mock` sur le SDK.

---

## 12. Découpage en lots

| Lot | Contenu | Livrable vérifiable |
|---|---|---|
| **L1** | Scaffolding conforme flotte : repo, `config/`, `/health/`, CI, SSM, unit systemd, sudoers, nginx, deploy OIDC, Sentry, UptimeRobot | `billing-api.foxugly.com/health/` → 200 en prod |
| **L2** | dj-stripe + modèles `App`/`Plan`/`AppCustomer`/`Entitlement`/`EntitlementDelivery` + Django admin + webhook Stripe + `recompute_entitlement` | Un événement Stripe de test met à jour un entitlement |
| **L3** | API S2S (checkout, portal, plans, history, entitlements) + HMAC + Celery + retries + `sync_entitlements` | Checkout de bout en bout en **mode test** Stripe |
| **L4** | Migration de Poker en consommateur (backend + SPA + tests), déployée **inerte** | Poker déployé, gating inchangé, tests verts |
| **L5** | Console Angular | `billing.foxugly.com` en prod, staff-only |
| **L6** | Mise en service réelle : **numérotation de facture au niveau du compte**, produits/prix avec Stripe Tax, re-routage du webhook, secrets SSM des deux côtés, cutover Poker | Un abonnement live pris de bout en bout |
| **L7** | Facturation directe (consulting) : page « Nouvelle facture », lignes, codes fiscaux, brouillon → finalisation → envoi, suivi des impayés | Une facture de prestation émise et payée |

---

## 13. Cutover Poker (lot L6) — points d'attention

L'ordre compte, parce que le compte Stripe est **live** et qu'un webhook pointe déjà sur Poker :

1. Auditer les 4 `Price` existants : `tax_behavior` défini ? Sinon **créer de nouveaux prix**
   (les prix Stripe sont immuables sur ce point) et retirer les anciens du catalogue.
2. Activer Stripe Tax et déclarer les enregistrements TVA (Belgique + OSS) dans le dashboard.
3. Seeder `App(poker)` + `Plan(team1, team5)` dans le central, secret généré.
4. Déployer L4 sur Poker **sans** les variables SSM → Poker reste sur son chemin actuel.
5. Enregistrer le **nouveau** endpoint webhook vers `billing-api`, garder l'ancien actif.
6. Poser `BILLING_BASE_URL` + `BILLING_APP_SECRET` dans `/poker/prod`, retirer les
   `STRIPE_*` de `/poker/prod`, redémarrer `poker-asgi`.
7. Vérifier un cycle complet en mode test, puis désactiver l'ancien endpoint webhook.
8. `sync_entitlements --app poker --push-diff` pour aligner l'état.

Le risque est faible : **0 abonnement existant**, donc aucun client à ne pas casser. Le vrai
risque est un gating qui se refermerait par erreur — d'où le mode inerte conservé et l'étape 4.

---

## 14. Risques et points ouverts

| Risque | Traitement |
|---|---|
| Les 4 prix live existants n'ont pas de `tax_behavior` compatible Stripe Tax | Audit en L6, recréation des prix si besoin (aucun abonné à migrer) |
| Un push perdu laisse une app avec un droit périmé | Réconciliation quotidienne + `current_period_end` dans le cache local, qui expire tout seul |
| Le central tombe | Aucune app n'est bloquée : le cache local sert. Seuls checkout/portail/historique renvoient 503 |
| Secret HMAC compromis | Rotation double-secret depuis la console, sans coupure |
| Poker est le seul site ASGI de la flotte | Sans impact : le billing est un site WSGI/gunicorn classique |
| `client_reference_id` Stripe limité à 200 car. | `(app, external_user_id)` tient largement ; on y met aussi `app` pour la résolution de secours |
| **`current_period_end` a migré au niveau des *items* d'abonnement** dans les versions récentes de l'API Stripe. Le code actuel de Poker le lit à la racine (`stripe_sub.get("current_period_end")`) → `None` silencieux, donc un entitlement qui paraît expiré | Lire la date depuis `items.data[0].current_period_end` avec repli sur la racine ; un test dédié sur les deux formes de payload |
| Le portail client montre les abonnements de toutes les apps (cf. §5) | Assumé et documenté ; à réévaluer si un site exige l'isolation |

**Décidé mais à confirmer à l'exécution :** que le gating actuellement actif sur poker.foxugly.com
(billing armé, 0 abonné) soit l'état voulu — sinon le poser en inerte est un geste SSM immédiat,
indépendant de ce projet.

---

## 15. Ce que le projet ne fait pas encore, et qui viendra ensuite

- Intégration des autres sites (tm, pushit, quizonline) : le modèle `App`/`Plan` est prévu pour,
  chaque intégration est alors un petit lot par site.
- Paiements one-shot : le modèle les accepte (`mode=payment`, produit sans récurrence), mais
  aucun site n'en a l'usage aujourd'hui — implémenté quand un premier cas concret apparaît.
- Metering / crédits : explicitement hors scope, nécessiterait un modèle de consommation.
- Devis (Stripe Quotes) : adjacent à la facturation directe (§16), pas en v1.

---

## 16. Facturation directe — prestations de consulting (lot L7)

Le service ne facture pas que des abonnements de la flotte : il doit pouvoir émettre une
**facture ponctuelle de prestation** pour un client qui n'est utilisateur d'aucun site. Stripe
Invoicing le fait nativement et dj-stripe mire déjà `Invoice` / `InvoiceItem` — l'essentiel du
travail est dans la console.

**Mécanique :** créer ou retrouver un `AppCustomer` avec `app=NULL` (§5) → `InvoiceItem`
(description, quantité, prix unitaire, code fiscal) → `Invoice` en brouillon → finalisation →
envoi. Le client reçoit un lien de paiement hébergé (carte ou virement SEPA) ; une facture réglée
par virement direct peut être marquée payée hors Stripe. Stripe génère le PDF conforme et gère
les relances d'impayés.

**Aucun impact sur le cœur du projet :** pas d'entitlement, pas de livraison, pas de `Plan`.

### Trois points à ne pas rater

1. **Numérotation.** L'administration belge exige une séquence continue par émetteur. Stripe
   numérote **par client** par défaut ; il faut basculer le compte sur une numérotation au
   niveau du compte **avant la première facture émise** — le réglage n'est pas rétroactif. C'est
   pour cette raison qu'il figure au lot **L6**, et pas au lot L7 : il conditionne aussi les
   factures d'abonnement.
2. **Régime de TVA du consulting.** Ce n'est pas celui du SaaS : B2B intra-UE → autoliquidation
   si le numéro de TVA est valide (Stripe Tax le vérifie via VIES) ; B2B hors UE → hors champ ;
   B2C → règle du lieu de prestation. Stripe Tax couvre les trois **à condition** de poser le bon
   code fiscal sur chaque ligne. La console expose donc une petite liste de « catégories de
   prestation » configurables, chacune mappée sur un code fiscal Stripe.
3. **Le comptable — les deux sorties sont prévues.** La console offre à la fois le
   téléchargement des **PDF Stripe** (pièces justificatives) et un **export structuré CSV** sur
   une période donnée (date, numéro, client, n° TVA, HT, TVA, TTC, devise, statut, moyen de
   paiement, app ou « direct »). Pas d'arbitrage à faire : ce qui coûte cher, c'est de devoir
   ajouter le second après coup au moment d'une clôture.

---

## 17. Données personnelles, coordonnées et conservation

### Ce qui est collecté

La configuration Checkout retenue (`automatic_tax` + `customer_update: {address: "auto"}` +
`tax_id_collection`) fait collecter par Stripe :

| Donnée | Collectée | Destination |
|---|---|---|
| Email | toujours | `customer.email` |
| Nom (ou raison sociale en B2B) | oui | `customer.name` |
| Adresse de facturation complète | **oui, obligatoire** — Stripe Tax en a besoin | `customer.address` |
| Numéro de TVA | optionnel, à l'initiative du client | `customer.tax_ids` |
| Téléphone | **non — délibérément pas collecté** | — |
| Carte bancaire | jamais chez nous | reste chez Stripe (exposée masquée : réseau + 4 derniers chiffres) |

**Le téléphone est écarté par défaut** (`phone_number_collection` désactivé) : il n'est requis ni
par la facturation, ni par la TVA, ni par le support. Le collecter ajouterait une donnée
personnelle à protéger, exporter et effacer pour un usage inexistant.

Tout cela est mis en miroir par dj-stripe : **la console lit les coordonnées depuis notre base**,
sans appel à Stripe, et reste donc consultable si Stripe est injoignable. C'est `customer.updated`
(§6.2) qui maintient ce miroir à jour quand un client modifie son adresse depuis le portail.

### Où ces données ne vont pas

**Aucune application de la flotte ne voit l'adresse de facturation de ses utilisateurs.** Poker
ne détient que l'email et le pseudo ; adresse, numéro de TVA et historique de paiement restent
dans le service billing et chez Stripe. Une compromission d'un site de la flotte n'expose aucune
donnée de facturation — bénéfice direct de la centralisation, à faire figurer dans l'argumentaire
sécurité.

### Conformité

- **Responsable de traitement** : Foxugly SRL. **Sous-traitant** : Stripe (DPA standard,
  transferts encadrés). À refléter dans la politique de confidentialité de chaque site qui vend.
- **Conservation** : factures et données qui les fondent sont des pièces comptables →
  **7 ans** en Belgique. Une demande d'effacement RGPD ne les efface pas ; elle ne porte que sur
  ce qui excède l'obligation légale.
- **Droit d'accès / portabilité** : la page détail client de la console y répond directement,
  Stripe fournissant les PDF de facture.
