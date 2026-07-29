# BACKLOG — billing_server (+ billing_frontend)

Issu d'une revue de session (2026-07-29), après les lots L1→L3 et L5a. Sévérités :
**P1** important · **P2/P3** à nettoyer. Le travail coché est commité/poussé sur
`Foxugly/billing_server` (`main`, CI verte).

État vérifié le 2026-07-29, après les PR #15/#16/#17 déployées : units `billing-gunicorn` /
`-celery` / `-celery-beat` / `-env-fetch` / `-frontend-runtime-fetch` actives, `/health/` 200,
console 200, `/api/v1/admin/apps/` 401 et `/api/admin/apps/` 404 (l'alias est bien retiré),
migration `0005` appliquée, aucune erreur dans les journaux, 184 tests verts, 1 livraison
`delivered` / 0 `failed`.

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
- [x] **P3 — Middleware d'alias `/api/` → `/api/v1/` retiré (2026-07-29).** Une seule réécriture
  journalisée depuis sa mise en place (le 2026-07-28, à la vérification post-déploiement), et les
  deux consommateurs signent déjà `/api/v1/` (`billing/client.py:91` des deux côtés) : le critère
  de suppression de §3.18 était rempli. Un test pin désormais le 404 sur l'ancien préfixe.
- [ ] **P3 — L'alias reste en place ailleurs dans la flotte, et c'est justifié pour l'instant.**
  Relevé le 2026-07-29 sur les journaux de la box :
  - **quizonline** — 595 réécritures en 3 jours, ~68/h de façon régulière jusqu'au 2026-07-29
    08:20 UTC, puis plus rien. Un appelant automatique qui n'apparaît **pas** dans
    `quizonline-access.log` : il ne passe donc pas par nginx. À identifier avant de retirer
    quoi que ce soit.
  - **poker** — 5 réécritures en 7 jours, dont 4 étaient la suite de tests de ce dépôt (corrigé,
    voir plus haut) et une un appel navigateur du 2026-07-28 (`/api/teams/`), vraisemblablement
    un bundle en cache. À re-vérifier après une fenêtre calme.
- [x] **P3 — Hygiène Sentry (2026-07-29).** Les 7 issues `billing-backend` et l'unique
  `billing-frontend` passées en résolu, chacune avec le motif en commentaire : le
  `celerybeat-schedule` permission denied (571 events, corrigé par la PR #5 et muet depuis), le
  `djstripe` absent du venv pendant L2, quatre erreurs ponctuelles de sessions `manage.py shell`
  manuelles, et la violation CSP antérieure au correctif nonce. Rien de vivant.
- [ ] **P3 — Une erreur de commande de gestion remonte comme une erreur applicative.** Quatre des
  huit issues ci-dessus étaient des fautes de frappe dans des `manage.py shell` d'audit. Une boîte
  Sentry qui mélange ça avec de vraies erreurs de production cesse d'être un signal. Un
  `before_send` qui écarte (ou étiquette) ce qui vient d'une commande de gestion la rendrait
  fiable — à arbitrer.
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
