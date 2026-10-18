"""Réception des événements Stripe.

dj-stripe vérifie la signature Stripe, persiste l'événement et met à jour son
miroir avant de nous appeler : l'idempotence sur `event.id` est donc déjà acquise,
et un renvoi de Stripe ne retraite rien.

Ce module ne fait qu'une chose : identifier de qui parle l'événement, puis demander
un recalcul du droit. La livraison à l'application est confiée à Celery.

L'ORDRE, en revanche, n'est pas garanti par Stripe : un événement ancien peut
arriver après un plus récent. `_stale_reason` écarte ceux qui rouvriraient un
accès résilié (audit 2026-10-18, I4).
"""
import logging

from django.db.models import Q
from djstripe.event_handlers import djstripe_receiver

from .models import App, EntitlementDelivery, Plan
from .services import (
    ENDED_STATUSES,
    TERMINAL_STATUSES,
    period_end_of,
    recompute_entitlement,
)

logger = logging.getLogger("billing")

# Événements qui peuvent changer un droit. `customer.updated` et `customer.tax_id.*`
# ne sont PAS ici : ils n'affectent que les coordonnées de facturation, que dj-stripe
# mire déjà tout seul — les écouter ne produirait que des livraisons inutiles.
SUBSCRIPTION_EVENTS = [
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
]
# Secours de rattachement (B2) : la session porte toujours l'identité de
# l'utilisateur, même quand l'abonnement ne la porte pas.
CHECKOUT_EVENTS = ["checkout.session.completed"]


def resolve_target(obj):
    """(App, external_user_id) depuis un objet d'événement Stripe, ou (None, None).

    Dans l'ordre :
    1. les `metadata` — posées sur l'abonnement par `subscription_data.metadata`
       au checkout, et sur la session elle-même ;
    2. `client_reference_id` au format "<app>:<user>" (une session Checkout ; un
       objet Subscription n'en a pas) ;
    3. le client Stripe (`customer`) déjà rattaché localement ;
    4. pour les abonnements antérieurs au correctif B2 (sans metadata ni lien) :
       l'app désignée par le prix, et l'e-mail du client Stripe — s'il désigne un
       seul utilisateur de cette app. Dans le doute, on ne rattache pas.
    """
    obj = obj or {}
    metadata = obj.get("metadata") or {}
    slug, user_id = metadata.get("app"), metadata.get("external_user_id")

    if not (slug and user_id):
        reference = obj.get("client_reference_id") or ""
        if ":" in reference:
            slug, user_id = reference.split(":", 1)

    if slug and user_id:
        app = App.objects.filter(slug=slug).first()
        return (app, user_id) if app else (None, None)

    return _resolve_by_customer(obj)


def _customer_id_of(obj):
    customer = obj.get("customer") or ""
    if isinstance(customer, dict):
        customer = customer.get("id") or ""
    return customer


def _single(queryset):
    """L'unique élément d'une requête, ou None s'il y en a zéro ou plusieurs."""
    rows = list(queryset[:2])
    return rows[0] if len(rows) == 1 else None


def _resolve_by_customer(obj):
    from djstripe.models import Customer as DjstripeCustomer

    from .models import AppCustomer, Entitlement

    customer_id = _customer_id_of(obj)
    if not customer_id:
        return None, None

    entitlement = _single(
        Entitlement.objects.filter(stripe_customer_id=customer_id).select_related("app")
    )
    if entitlement is not None:
        return entitlement.app, entitlement.external_user_id

    linked = _single(
        AppCustomer.objects.filter(customer__id=customer_id, app__isnull=False)
        .exclude(external_user_id="")
        .select_related("app")
    )
    if linked is not None:
        return linked.app, linked.external_user_id

    # Repli « historique » : le prix désigne l'app, l'e-mail l'utilisateur.
    apps = apps_for_price(price_id_of(obj))
    if len(apps) != 1:
        return None, None
    miroir = DjstripeCustomer.objects.filter(id=customer_id).first()
    email = ((miroir.stripe_data or {}).get("email") if miroir else "") or ""
    if not email:
        return None, None
    match = _single(
        AppCustomer.objects.filter(app=apps[0], email__iexact=email).exclude(external_user_id="")
    )
    if match is None:
        return None, None
    return apps[0], match.external_user_id


def apps_for_price(price_id):
    """Les apps dont un plan utilise ce prix Stripe."""
    if not price_id:
        return []
    return list(
        App.objects.filter(
            Q(plans__price_monthly__id=price_id) | Q(plans__price_yearly__id=price_id)
        ).distinct()
    )


def plan_for_price(app, price_id):
    """(Plan, interval) depuis un identifiant de prix Stripe, ou (None, "")."""
    if not price_id:
        return None, ""
    plan = Plan.objects.filter(app=app, price_monthly__id=price_id).first()
    if plan:
        return plan, Plan.MONTHLY
    plan = Plan.objects.filter(app=app, price_yearly__id=price_id).first()
    if plan:
        return plan, Plan.YEARLY
    return None, ""


def price_id_of(subscription):
    """L'identifiant du prix porté par le premier item d'un abonnement."""
    try:
        return subscription["items"]["data"][0]["price"]["id"]
    except (KeyError, IndexError, TypeError):
        return ""


def quantity_of(subscription):
    """La quantité souscrite. 1 par défaut — un abonnement forfaitaire n'en porte pas."""
    try:
        return int(subscription["items"]["data"][0].get("quantity") or 1)
    except (KeyError, IndexError, TypeError, ValueError):
        return 1


def _stale_reason(app, user_id, obj, event_created):
    """Pourquoi cet événement ne doit PAS être appliqué, ou "" s'il doit l'être.

    Stripe ne garantit pas l'ordre de livraison, et un renvoi tardif d'un
    `updated(active)` rouvrirait un accès résilié (audit I4).
    """
    from .models import Entitlement

    ent = Entitlement.objects.filter(app=app, external_user_id=user_id).first()
    if ent is None or ent.source != Entitlement.STRIPE:
        return ""

    sub_id = obj.get("id") or ""
    status = obj.get("status") or ""
    same_subscription = bool(sub_id) and sub_id == ent.stripe_subscription_id

    if same_subscription and ent.status in ENDED_STATUSES and status not in ENDED_STATUSES:
        # `canceled` est définitif chez Stripe : ce qui dit le contraire est périmé.
        return "subscription_already_ended"
    if event_created and ent.last_event_at and event_created < ent.last_event_at:
        return "older_than_last_applied"
    if (
        sub_id
        and ent.stripe_subscription_id
        and not same_subscription
        and status in TERMINAL_STATUSES
        and ent.is_paid
    ):
        # La fin d'un ANCIEN abonnement ne ferme pas l'abonnement en cours.
        return "superseded_subscription"
    return ""


def handle_subscription_event(obj, event_created=None):
    """Recalcule le droit décrit par un objet `subscription` Stripe.

    Renvoie l'EntitlementDelivery créée, ou None quand l'événement :
    - ne concerne aucune app connue (un compte Stripe partagé reçoit aussi des
      événements qui ne nous regardent pas) ;
    - concerne un utilisateur effacé (la résiliation provoquée par l'effacement
      arrive après lui) ;
    - est périmé (cf. `_stale_reason`). Sans `event_created` (rejeu console),
      seuls les gardes indépendants de l'horodatage s'appliquent.
    """
    from .models import ErasedUser

    obj = obj or {}
    app, user_id = resolve_target(obj)
    if app is None:
        logger.info("stripe_event_ignored", extra={"reason": "unresolved_target"})
        return None
    if ErasedUser.is_erased(app, user_id):
        logger.info("stripe_event_ignored", extra={"reason": "erased_user", "app": app.slug})
        return None
    reason = _stale_reason(app, user_id, obj, event_created)
    if reason:
        logger.info(
            "stripe_event_ignored",
            extra={"reason": reason, "app": app.slug, "subscription": obj.get("id")},
        )
        return None

    customer_id = _customer_id_of(obj)
    plan, interval = plan_for_price(app, price_id_of(obj))
    entitlement = recompute_entitlement(
        app,
        user_id,
        stripe_status=obj.get("status", "") or "",
        plan=plan,
        interval=interval,
        period_end=period_end_of(obj),
        stripe_customer_id=customer_id,
        quantity=quantity_of(obj),
        stripe_subscription_id=obj.get("id") or "",
        event_created=event_created,
    )
    _link_customer_mirror(app, user_id, customer_id)
    delivery = EntitlementDelivery.objects.create(
        entitlement=entitlement, payload=entitlement.payload()
    )
    # Asynchrone : un webhook Stripe ne doit jamais échouer parce qu'un site de la
    # flotte est injoignable. dj-stripe a déjà répondu 200 à Stripe à ce stade.
    from .tasks import deliver_entitlement

    deliver_entitlement.delay(str(delivery.pk))
    return delivery


def handle_checkout_completed(obj):
    """`checkout.session.completed` : rattache le client Stripe à l'utilisateur.

    La session porte toujours les metadata et `client_reference_id`, même quand
    l'abonnement n'en a pas (abonnements antérieurs au correctif B2). Une fois le
    `cus_…` connu, les événements d'abonnement suivants se rattachent par lui.
    Si l'abonnement est déjà dans le miroir sans avoir été appliqué (son
    `created` est arrivé avant, sans pouvoir être attribué), on l'applique ici.
    """
    from djstripe.models import Subscription as DjstripeSubscription

    from .models import Entitlement, ErasedUser

    obj = obj or {}
    app, user_id = resolve_target(obj)
    customer_id = _customer_id_of(obj)
    if app is None or not customer_id or ErasedUser.is_erased(app, user_id):
        return None

    ent, _ = Entitlement.objects.get_or_create(app=app, external_user_id=user_id)
    if ent.stripe_customer_id != customer_id:
        ent.stripe_customer_id = customer_id
        ent.save(update_fields=["stripe_customer_id", "computed_at"])
    _link_customer_mirror(app, user_id, customer_id)

    sub_id = obj.get("subscription") or ""
    if isinstance(sub_id, dict):
        sub_id = sub_id.get("id") or ""
    if not sub_id or ent.stripe_subscription_id == sub_id:
        return None
    miroir = DjstripeSubscription.objects.filter(id=sub_id).first()
    if miroir is None or not miroir.stripe_data:
        return None
    return handle_subscription_event(dict(miroir.stripe_data))


def _link_customer_mirror(app, external_user_id, stripe_customer_id):
    """Rattache l'AppCustomer a la fiche client du miroir dj-stripe.

    Sans ca la FK reste vide et la console d'exploitation ne peut pas relier un
    utilisateur d'une app a son client Stripe. Best-effort : si le miroir n'a pas
    encore la fiche, on n'echoue pas -- un webhook ne doit pas retomber en erreur
    pour un lien de confort, et le prochain evenement rattrapera.
    """
    if not stripe_customer_id:
        return
    from djstripe.models import Customer as DjstripeCustomer

    from .models import AppCustomer

    miroir = DjstripeCustomer.objects.filter(id=stripe_customer_id).first()
    if miroir is None:
        return
    AppCustomer.objects.filter(
        app=app, external_user_id=external_user_id, customer__isnull=True
    ).update(customer=miroir)


@djstripe_receiver(SUBSCRIPTION_EVENTS)
def on_subscription_event(sender, event, **kwargs):
    """Point d'entrée réel des webhooks : dj-stripe nous appelle après avoir vérifié
    la signature Stripe et mis son miroir à jour."""
    handle_subscription_event(
        event.data.get("object") if event.data else None,
        event_created=getattr(event, "created", None),
    )


@djstripe_receiver(CHECKOUT_EVENTS)
def on_checkout_completed(sender, event, **kwargs):
    """Secours de rattachement (B2). N'agit que si l'endpoint Stripe est abonné à
    `checkout.session.completed` ; sans cet abonnement, rien ne casse."""
    handle_checkout_completed(event.data.get("object") if event.data else None)
