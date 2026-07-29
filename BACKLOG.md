# BACKLOG — billing_server (+ billing_frontend)

Issu d'une revue de session (2026-07-29), après les lots L1→L3 et L5a. Sévérités :
**P1** important · **P2/P3** à nettoyer. Le travail coché est commité/poussé sur
`Foxugly/billing_server` (`main`, CI verte).

État vérifié le 2026-07-29 : units `billing-gunicorn` / `-celery` / `-celery-beat` /
`-env-fetch` / `-frontend-runtime-fetch` actives, `/health/` 200, console 200, aucune erreur
en 24 h de journaux, 185 tests verts, 1 livraison `delivered` / 0 `failed`.

---

## ✅ Fait le 2026-07-29

- [x] **P1 — La suite de tests postait sur la production.** Les quatre tests de webhook
  laissaient partir la livraison : `CELERY_TASK_ALWAYS_EAGER` est actif en test, donc le
  `deliver_entitlement.delay()` déclenché par le webhook s'exécutait dans le processus de test
  et **postait pour de bon sur `https://poker-api.foxugly.com`** — 4 requêtes par exécution, à
  chaque `pytest` local et à chaque job CI (retrouvées dans `poker-access.log`). Rien ne le
  signalait : la prod répondait 401, le code de livraison traite un échec comme une reprise à
  programmer, et les tests restaient verts. **Seule la signature HMAC a empêché l'écriture de
  droits fictifs en production** — ce n'était pas le garde-fou prévu pour ça, juste celui qui a
  tenu. Corrigé par un `conftest.py` à la racine qui refuse tout appel réseau réel (héritant de
  `BaseException`, sans quoi le `except Exception` de la livraison l'avalerait), plus un patch
  de `deliver_entitlement.delay` dans les tests de webhook, qui portent sur la mise en file.
- [x] **P1 — Le défaut de `App.entitlement_path` était resté le chemin legacy.**
  `default="/api/billing/entitlement/"` → `/api/v1/billing/entitlement/` (migration `0005`).
  Les deux lignes en production (poker, pushit) avaient été corrigées à la main lors du passage
  à `/api/v1/` ; le défaut, lui, ne l'avait pas été — **toute app créée ensuite depuis la console
  ou l'admin repartait sur le mauvais chemin**. Et ça ne se voit pas : l'alias transitoire ne
  couvre pas les endpoints signés (la signature HMAC porte le chemin, OPERATIONS.md §3.18), donc
  chaque push est refusé sans que rien ne le signale côté central. La migration ne réécrit
  **pas** les lignes existantes — vérifier au cas par cas avec le bouton « tester » de la console.

## À faire

- [ ] **P2 — La console ne couvre pas encore tout le §10 du design.** Livrées : dashboard, apps,
  clients, droits, livraisons. Manquent :
  - **Plans** (CRUD, mapping vers les `Price` Stripe, quotas, ordre, visibilité) — le backend est
    déjà là (`PlanViewSet`, `GET/POST /api/v1/admin/plans/`), il ne manque que la page Angular.
  - **Événements Stripe** (liste des `djstripe.Event` avec statut de traitement et rejeu) — ni
    page ni viewset : c'est un lot backend + frontend.
- [ ] **P3 — Retirer le middleware d'alias `/api/` → `/api/v1/`.** `config/legacy_api_prefix.py`
  + sa ligne dans `MIDDLEWARE`. Un seul `legacy_api_prefix` journalisé depuis la mise en place
  (le 2026-07-28, à la vérification post-déploiement), rien depuis : personne n'appelle plus
  l'ancien chemin, ce qui est exactement le critère de suppression de §3.18. À faire de concert
  avec les consommateurs (`Poker_server`, `PushIT_server`), qui portent le même middleware.
- [ ] **P3 — Hygiène Sentry.** `billing-backend` : 7 issues non résolues, toutes ponctuelles et
  issues de sessions `manage.py shell` manuelles (dont le `celerybeat-schedule` permission denied,
  571 events, corrigé par la PR #5 et muet depuis). `billing-frontend` : 1 violation CSP du
  2026-07-28 12:37–12:40, antérieure au correctif nonce et jamais revue depuis. Rien de vivant :
  à passer en résolu pour que la boîte redevienne un signal.
  *Observation au passage : une erreur dans une commande de gestion (`manage.py shell`, scripts
  d'audit) remonte à Sentry comme une erreur applicative. Un filtre `before_send` sur les
  commandes de gestion rendrait la boîte plus fiable — à arbitrer.*
- [ ] **P3 — Les fixtures de test portent des hôtes de production.** 49 occurrences de
  `foxugly.com` dans les tests (`base_url` des apps, `success_url`…). Le garde-fou réseau rend
  la fuite impossible aujourd'hui, mais des hôtes non routables (`.invalid`, RFC 2606)
  supprimeraient la classe entière de risque. Diff mécanique mais large, et quelques tests
  d'anti-redirection ouverte dépendent du domaine : à faire d'un bloc, pas en passant.
- [ ] **P3 — Tests de la console très minces.** 2 fichiers `.spec.ts` (`auth.service`,
  `entitlements-list`) pour 7 features. La CI est verte, mais elle ne prouve pas grand-chose au
  delà de la compilation.

---

## Lots à venir (roadmap, pas dette)

Découpage complet : `docs/superpowers/specs/2026-07-28-billing-central-design.md` §12.

- **L4 — Poker consommateur** : fait, et au-delà du plan (PushIT l'est aussi ; les deux apps sont
  seedées, 4 plans actifs, une livraison poussée et acquittée).
- **L6 — mise en service réelle** : pas fait. Numérotation de facture au niveau du compte,
  produits/prix avec Stripe Tax, re-routage du webhook, cutover. L'endpoint webhook est déjà
  `livemode` et `enabled`, 8 `Price` / 6 `Product` sont mirés, mais **0 `Event`** traité.
  ⚠️ L'audit `tax_behavior` de l'étape 1 ne peut pas se faire depuis dj-stripe : son modèle
  `Price` n'expose ni `recurring` ni `tax_behavior` — passer par le SDK Stripe directement.
  Un premier essai d'archivage de prix a d'ailleurs buté sur « this price cannot be archived
  because it is the default price of its product » : il faut changer le prix par défaut du
  produit avant d'archiver l'ancien.
- **L7 — facturation directe (consulting)** : pas commencé.
