# Billing — Lot L6 : mise en service réelle

**Date :** 2026-07-29
**Nature :** ce lot n'est presque pas du code. C'est une séquence de gestes, dont plusieurs sont
**irréversibles** et se font dans le dashboard Stripe d'un compte **live**.

Chaque étape porte qui l'exécute :
**[R]** = Renaud, dans le dashboard Stripe (personne d'autre ne peut, et personne d'autre ne doit :
ces réglages engagent fiscalement Foxugly SRL) · **[C]** = code ou ops, exécutable ici.

---

> ⚠️ **Ce document a d'abord été écrit sur une hypothèse fausse.** Sa première version présentait
> le fiscal, l'audit des prix et le cutover de Poker comme restant à faire : ils étaient **déjà
> exécutés**. Corrigé le 2026-07-29 après interrogation du compte live. La leçon vaut d'être
> gardée : un runbook rédigé depuis la spec plutôt que depuis l'état réel fait refaire des gestes
> déjà faits — et sur un compte live, refaire est parfois destructeur.

## Ce qui est déjà en place (interrogé sur le compte live le 2026-07-29)

**L'essentiel du lot est fait.** Ne rien refaire de ce tableau :

| Élément | État constaté |
|---|---|
| Service en prod | `billing-gunicorn` / `-celery` / `-celery-beat` actifs, `/health/` 200 |
| Mode Stripe | **live** — `STRIPE_LIVE_MODE=true`, clé live en SSM `/billing/prod` |
| **Stripe Tax** | ✅ **`status: active`**, défauts `inferred_by_currency` / `txcd_10000000` |
| **Enregistrement TVA** | ✅ une inscription **BE**, `active`, `country_options.be.type = oss_union` |
| **TVA calculée** | ✅ vérifié par trois calculs à blanc : BE → **21 %**, FR → **20 %** (OSS), US → **0 %** `not_collecting`. L'inscription OSS couvre donc le domestique **et** le cross-border UE |
| **`tax_behavior` des prix** | ✅ les **8** prix actifs sont `exclusive` — **rien à recréer** |
| **Cutover Poker** | ✅ fait — `/poker/prod` porte `BILLING_BASE_URL` + `BILLING_APP_SECRET`, et **plus aucun** `STRIPE_*` |
| **Webhook** | ✅ un **seul** endpoint, `enabled`, vers `billing-api` — l'ancien vers Poker a disparu |
| Objets Stripe mirés | 6 `Product`, 8 `Price` |
| Apps / plans seedés | `poker` (`team1`/`team5`) et `pushit` (`app`/`unlimited`), mensuel **et** annuel câblés |
| Live réel | **0 client, 0 abonnement, 0 facture, 0 `Event` traité** |

Autrement dit : la plomberie et le fiscal fonctionnent. Il reste **deux réglages de dashboard**, et
la chaîne complète n'a encore jamais tourné avec un vrai paiement.

---

## Ce qui reste **[R]**

### 1. La numérotation au niveau du compte — non rétroactive

Détaillée à l'étape 0 ci-dessous. **Non vérifiable par l'API** : ni `Account.retrieve()` ni aucun
autre point d'entrée n'expose le mode de numérotation. Il faut le lire dans le dashboard,
https://dashboard.stripe.com/settings/billing/invoice — l'écran montre le préfixe (3 à 12
caractères) et le champ *prochain numéro de séquence*.

**Déclarée faite par Renaud le 2026-07-29.** C'est le seul point de ce lot qui repose sur une
parole et non sur une vérification, et c'est assumé : le seul test décisif serait d'émettre une
vraie facture, ce qui consommerait le premier numéro de la séquence.

### 2. Le numéro de TVA de l'émetteur sur les factures — ✅ FAIT

Réglé le 2026-07-29 : `Account.settings.invoices.default_account_tax_ids` vaut désormais
`["txi_1TygghLJ7094uO171NQYscUX"]` → **`BE1004770045`**. Toute facture émise le portera.

⚠️ **Ce réglage n'est pas atteignable par l'API.** `Account.modify()` sur son propre compte répond :

```
PermissionError: You cannot use this method on your own account:
you may only use it on connected accounts.
```

Il se fait donc au dashboard : https://dashboard.stripe.com/settings/billing/invoices/general,
section *informations fiscales de facturation*, en cochant le numéro comme valeur par défaut.
Un numéro de TVA, une fois ajouté, n'est pas modifiable — il faut le supprimer et en recréer un.

**Piège de vérification :** `Invoice.create_preview` renvoie `account_tax_ids: null` même une fois
le réglage posé — l'aperçu n'applique pas les valeurs par défaut du compte. Vérifier sur
`Account.retrieve()`, pas sur un aperçu.

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

## Étape 1 — TVA **[R]** — ✅ FAITE

Constaté le 2026-07-29 sur le compte live : Stripe Tax `active`, une inscription **BE** `active` de
type **`oss_union`**. Vérifié par le calcul plutôt que par la lecture du réglage — trois calculs à
blanc (`stripe.tax.Calculation.create`) sur 20,00 € HT :

| Adresse de l'acheteur | TVA calculée | Motif |
|---|---|---|
| BE, Bruxelles | **4,20 €** (21 %) | `standard_rated` |
| FR, Paris | **4,00 €** (20 %) | `standard_rated` |
| US, Austin | **0,00 €** | `not_collecting` |

**Enseignement à garder :** l'inscription OSS déposée dans le pays d'établissement couvre à la fois
le domestique et le cross-border UE — il n'y a pas besoin d'une seconde inscription « standard »
belge. Ça ne se déduit pas de la documentation, ça se constate par un calcul.

Le calcul à blanc est le bon outil de vérification : il ne crée ni client, ni facture, ni paiement.

---

## Étape 2 — audit des prix existants **[C]** — ✅ FAITE

Constaté le 2026-07-29 : les **8** prix actifs sont tous en `tax_behavior = exclusive`. **Rien à
recréer.** La procédure ci-dessous n'a plus d'objet ; elle est conservée parce qu'elle resservira
au prochain prix créé à la main.

`tax_behavior` vaut `inclusive` (TVA comprise) ou `exclusive` (TVA en sus). S'il vaut
`unspecified`, Stripe Tax ne peut pas calculer — et **le champ est immuable** sur un prix existant.

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

## Étape 4 — cutover de Poker — ✅ FAITE

Constaté le 2026-07-29 : `/poker/prod` porte `BILLING_BASE_URL` et `BILLING_APP_SECRET`, et **plus
aucun** `STRIPE_*`. Un **seul** endpoint webhook existe côté Stripe, `enabled`, vers `billing-api` :
l'ancien, qui pointait sur Poker, a été retiré. Poker est donc consommateur du central, pour de bon.

**Rollback, s'il fallait revenir** : remettre les `STRIPE_*` dans `/poker/prod`, retirer
`BILLING_BASE_URL`, redémarrer `poker-asgi` — et rétablir l'endpoint webhook vers Poker, qui n'existe
plus. Le retour est donc plus coûteux qu'à l'origine ; il n'y a de toute façon aucun abonné à
protéger.

---

## Le vrai risque, et où il n'est pas

Il n'y a **aucun abonné à casser** : 0 client, 0 abonnement, 0 facture. Le risque n'est pas
commercial, il est fiscal et silencieux. Sur les trois pièges initialement listés, **deux sont
désormais écartés** et c'est vérifié :

- ~~une TVA à 0 % parce que les enregistrements manquent~~ → écarté, calculs à blanc à l'appui ;
- ~~un `tax_behavior` à `unspecified`~~ → écarté, les 8 prix sont `exclusive` ;
- **une numérotation par client qu'on ne peut plus corriger après la première facture** → **reste
  ouvert**, et non vérifiable par l'API ;
- **une facture sans le numéro de TVA de l'émetteur** → **s'ajoute à la liste** :
  `default_account_tax_ids` est `null` et le compte n'a aucun `TaxId` enregistré.

Les deux points ouverts ont le même profil : invisibles jusqu'à la clôture, et impossibles à
rattraper sur les factures déjà émises.

Aucun de ces trois ne déclenche d'alerte. Ils se constatent à la clôture, un an plus tard.

---

## Ce que ce lot ne fait PAS

La facturation directe de prestations (L7), traitée séparément, et l'intégration des autres sites
de la flotte (tm, quizonline, foxugly), qui est un petit lot par site.
