# Billing — Lot L6 : mise en service réelle

**Date :** 2026-07-29
**Nature :** ce lot n'est presque pas du code. C'est une séquence de gestes, dont plusieurs sont
**irréversibles** et se font dans le dashboard Stripe d'un compte **live**.

Chaque étape porte qui l'exécute :
**[R]** = Renaud, dans le dashboard Stripe (personne d'autre ne peut, et personne d'autre ne doit :
ces réglages engagent fiscalement Foxugly SRL) · **[C]** = code ou ops, exécutable ici.

---

## Ce qui est déjà en place (vérifié le 2026-07-29)

Une partie du lot est faite. Inutile de la refaire :

| Élément | État |
|---|---|
| Service en prod | `billing-gunicorn` / `-celery` / `-celery-beat` actifs, `/health/` 200 |
| Mode Stripe | **live** — `STRIPE_LIVE_MODE=true`, clé live en SSM `/billing/prod` |
| Endpoint webhook | déclaré, `livemode`, `enabled` — mais **0 `Event` traité à ce jour** |
| Objets Stripe mirés | 6 `Product`, 8 `Price` |
| Apps seedées | `poker` et `pushit`, actives, chemin de livraison canonique |
| Plans seedés | poker `team1`/`team5`, pushit `app`/`unlimited` — mensuel **et** annuel câblés |
| Consommateurs | Poker et PushIT déployés, signature HMAC opérationnelle (une livraison acquittée en 200) |

Autrement dit : la plomberie fonctionne. Ce qui reste, c'est le **fiscal**, la **numérotation**,
et le **basculement de Poker** de son Stripe historique vers le central.

---

## Étape 0 — la seule qui ne se rattrape pas **[R]**

**Basculer la numérotation des factures au niveau du compte, avant la première facture émise.**

L'administration belge exige une séquence continue par émetteur. Stripe numérote **par client**
par défaut (`INV-0001` repart à 1 chez chaque nouveau client), ce qui ne constitue pas une
séquence continue. Le réglage n'est **pas rétroactif** : les factures déjà émises gardent leur
numérotation.

Aujourd'hui **aucune facture n'a été émise** — la fenêtre est ouverte. Elle se referme à la
première, qu'elle vienne d'un abonnement ou d'une prestation.

> Dashboard Stripe → *Settings* → *Billing* → *Invoices* → *Invoice numbering* → **Account level**
> (par opposition à *Customer level*).

Faire cette étape **avant** tout le reste, y compris avant la répétition en mode test : le réglage
est propre à chaque mode, et on veut le même comportement des deux côtés.

---

## Étape 1 — TVA **[R]**

1. Activer **Stripe Tax**.
2. Déclarer les **enregistrements TVA** : Belgique, et **OSS** pour les ventes B2C dans le reste
   de l'UE.
3. Vérifier que l'adresse de l'entreprise et son numéro de TVA sont renseignés — Stripe Tax s'en
   sert pour déterminer le régime applicable.

Sans ces enregistrements, Stripe Tax calcule 0 % partout et le service émettra des factures sans
TVA, ce qui est bien pire qu'une erreur visible.

---

## Étape 2 — audit des prix existants **[C]**, décision **[R]**

Les 4 prix live du catalogue Poker ont été créés avant Stripe Tax. Il faut vérifier leur
`tax_behavior` : `inclusive` (TVA comprise) ou `exclusive` (TVA en sus). S'il vaut `unspecified`,
Stripe Tax ne peut pas calculer — et **le champ est immuable** sur un prix existant.

⚠️ **L'audit ne peut pas se faire depuis dj-stripe** : son modèle `Price` n'expose ni `recurring`
ni `tax_behavior` (constaté le 2026-07-28 — une `FieldError` remontée dans Sentry). Passer par le
SDK Stripe :

```python
import stripe
for p in stripe.Price.list(limit=100, active=True).auto_paging_iter():
    print(p.id, p.unit_amount, p.currency, p.tax_behavior, p.recurring and p.recurring.interval)
```

Si un prix est à `unspecified` :

1. Créer un **nouveau** prix sur le même produit, avec le `tax_behavior` voulu.
2. **Changer d'abord le prix par défaut du produit** (`stripe.Product.modify(prod, default_price=...)`).
3. **Puis seulement** archiver l'ancien. Dans l'autre ordre, Stripe refuse :
   *« This price cannot be archived because it is the default price of its product »* — erreur
   déjà rencontrée le 2026-07-28.
4. Recâbler `Plan.price_monthly` / `price_yearly` sur les nouveaux prix, dans la console ou l'admin.

Aucun abonné à migrer : **0 abonnement existant**. C'est le bon moment.

---

## Étape 3 — la répétition en fausse monnaie **[C]**, clés **[R]**

Ne rien basculer en réel sans avoir vu le cycle complet fonctionner. La production étant branchée
sur le compte live, la répétition se fait **en local**, sur une base locale, avec les clés du mode
test.

Ce qu'il faut :

- `STRIPE_TEST_SECRET_KEY` (dashboard, interrupteur sur *Test mode*) — **[R]**, une fois.
- Le **Stripe CLI**, qui ouvre un tunnel pour que Stripe puisse rappeler une machine locale :
  `stripe listen --forward-to localhost:8000/stripe/webhook/<uuid>/`.

Ce qu'on vérifie, dans cet ordre :

1. `POST /api/v1/checkout/` signé → une session Checkout s'ouvre, avec la TVA calculée.
2. Paiement avec une carte de test → Stripe émet la facture et déclenche le webhook.
3. Le webhook recalcule l'entitlement et met une livraison en file.
4. La livraison part vers l'app et revient en 200 → `EntitlementDelivery.status == delivered`.
5. Le numéro de facture suit la séquence du **compte**, pas du client (contrôle de l'étape 0).
6. Le portail client s'ouvre, l'historique liste la facture.

Tant que ces six points ne sont pas verts, ne pas passer à l'étape 4.

---

## Étape 4 — cutover de Poker **[C]** + SSM **[R]**

L'ordre compte : un webhook pointe déjà sur Poker, et Poker détient encore ses propres clés Stripe.

1. **[C]** Vérifier que Poker tourne bien en consommateur inerte : le code est déployé, mais sans
   `BILLING_BASE_URL` ni `BILLING_APP_SECRET`, il reste sur son chemin historique.
2. **[R]** Enregistrer le **nouvel** endpoint webhook vers `billing-api` — déjà fait — et **garder
   l'ancien actif** le temps de la bascule.
3. **[R]** Dans SSM `/poker/prod` : poser `BILLING_BASE_URL` et `BILLING_APP_SECRET` (le secret se
   lit **une seule fois**, au moment de la rotation depuis la console), puis **retirer** les
   `STRIPE_*`.
4. **[C]** Redémarrer `poker-asgi`, vérifier `/health/` et un achat de bout en bout.
5. **[C]** `sync_entitlements --app poker --push-diff` pour aligner l'état.
6. **[R]** Désactiver l'ancien endpoint webhook.

**Rollback** : remettre les `STRIPE_*` dans `/poker/prod`, retirer `BILLING_BASE_URL`, redémarrer.
Poker reprend son chemin historique. C'est pour garder ce retour possible que l'ancien endpoint
reste actif jusqu'au bout.

---

## Le vrai risque, et où il n'est pas

Il n'y a **aucun abonné à casser** : 0 abonnement existant, sur Poker comme ailleurs. Le risque
n'est pas commercial, il est fiscal et silencieux :

- une TVA à 0 % parce que les enregistrements manquent ;
- une numérotation par client qu'on ne peut plus corriger après la première facture ;
- un `tax_behavior` à `unspecified` qui fait échouer le calcul sans que personne ne regarde.

Aucun de ces trois ne déclenche d'alerte. Ils se constatent à la clôture, un an plus tard.

---

## Ce que ce lot ne fait PAS

La facturation directe de prestations (L7), traitée séparément, et l'intégration des autres sites
de la flotte (tm, quizonline, foxugly), qui est un petit lot par site.
