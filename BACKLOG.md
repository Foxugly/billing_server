# BACKLOG — billing_server (+ billing_frontend)

Issu d'une revue de session (2026-07-29), après les lots L1→L3, L5a, la console (L5) et L7.
Sévérités : **P1** important · **P2/P3** à nettoyer. Le travail coché est commité/poussé sur
`Foxugly/billing_server` (`main`, CI verte).

État vérifié le 2026-07-29, après les PR #15 à #20 déployées : units `billing-gunicorn` /
`-celery` / `-celery-beat` / `-env-fetch` / `-frontend-runtime-fetch` actives, `/health/` 200,
console 200, `/api/v1/admin/apps/` 401 et `/api/admin/apps/` 404 (l'alias est bien retiré),
migration `0005` appliquée, aucune erreur dans les journaux, **211 tests backend** et **24 tests
console** verts, 1 livraison `delivered` / 0 `failed`.

**Aucun compte ni client en production** — 0 `AppCustomer`, 0 `Event` Stripe traité. C'est ce qui
a permis de trancher plusieurs arbitrages de cette revue sans précaution particulière.

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

- [ ] **P2 — Il manque la page « Événements Stripe » du §10.** Liste des `djstripe.Event` avec
  statut de traitement et rejeu : ni page ni viewset, c'est un lot backend + frontend. Tout le
  reste du §10 est livré — dashboard, apps, plans, clients, droits, livraisons, factures.
- [x] **P3 — Middleware d'alias `/api/` → `/api/v1/` retiré (2026-07-29).** Une seule réécriture
  journalisée depuis sa mise en place (le 2026-07-28, à la vérification post-déploiement), et les
  deux consommateurs signent déjà `/api/v1/` (`billing/client.py:91` des deux côtés) : le critère
  de suppression de §3.18 était rempli. Un test pin désormais le 404 sur l'ancien préfixe.
- [x] **P3 — Alias retiré du reste de la flotte aussi (2026-07-29).** `Poker_server` PR #25 (201
  tests) et `Foxugly/QuizOnline` PR #119 (1194 tests, CI complète). Vérifié en production :
  l'ancien préfixe répond 404, le canonique répond normalement, `/health/` 200 des deux côtés.
  Les appelants restants étaient identifiés et bénins — sur quizonline, les 595 réécritures en
  3 jours (~68/h) venaient d'**un seul onglet de navigateur** resté ouvert sur `/lesson/1`, qui
  appelait `GET /api/unread-counts/` toutes les 60 s et s'est tu à sa fermeture, le 2026-07-29 à
  08:20:40 UTC. Décision de ne pas attendre de fenêtre calme : aucun compte, aucun client en
  production, donc aucun bundle en cache à ménager.
  *Au passage : le trafic du SPA quizonline atterrit dans le `access.log` par défaut de nginx et
  non dans `quizonline-access.log` — c'est ce qui a rendu cette recherche pénible.*
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
- [ ] **P3 — Tests de la console encore minces.** 4 fichiers `.spec.ts` (`auth.service`,
  `entitlements-list`, `invoices-list`, `plans-list`) pour 9 features. Ce qui compte est couvert —
  la conversion euros→centimes, le vidage des quotas d'un plan à l'unité — mais les pages de
  lecture ne le sont pas.
- [x] **P2 — Les libellés communs manquaient dans les catalogues (2026-07-29).** `common.status`,
  `common.actions`, `common.active`, `common.inactive`, `common.save`, `common.close` et
  `common.search` n'existaient nulle part : Transloco rendant la clé quand la traduction manque,
  la console affichait littéralement « common.status » en en-tête de colonne, **sur toutes les
  pages, depuis le début**. Les sept ajoutés dans les cinq langues (`billing_frontend` PR #4).

---

## Lots à venir (roadmap, pas dette)

Découpage complet : `docs/superpowers/specs/2026-07-28-billing-central-design.md` §12.

- **L4 — Poker consommateur** : fait, et au-delà du plan (PushIT l'est aussi ; les deux apps sont
  seedées, 4 plans actifs, une livraison poussée et acquittée).
- **L6 — mise en service réelle** : **runbook écrit**
  (`docs/superpowers/plans/2026-07-29-billing-l6-mise-en-service.md`), exécution en attente. Ce lot
  n'est presque pas du code : plusieurs étapes sont des gestes irréversibles dans le dashboard
  Stripe d'un compte live, et elles engagent fiscalement la société — elles reviennent à Renaud,
  pas à l'agent. **Ce qui bloque :** l'étape 0 (numérotation au niveau du compte, non rétroactive,
  à faire avant la première facture) et les clés du mode test pour la répétition.
- **L7 — facturation directe (consulting)** : **fait côté code** (2026-07-29), en attente de la
  répétition en mode test. `core/invoicing.py` + API console + page « Factures » : client direct,
  lignes, brouillon → finalisation → envoi, « marquer payée » hors Stripe, export CSV, codes
  fiscaux lus du catalogue Stripe. Périmètre volontairement réduit pour quelques factures par an :
  **pas** de suivi d'impayés (Stripe relance nativement), **pas** de CRUD de catégories de
  prestation.
