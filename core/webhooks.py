"""Réception des événements Stripe.

dj-stripe vérifie la signature Stripe, persiste l'événement et met à jour son
miroir avant de nous appeler : l'idempotence sur `event.id` est donc déjà acquise,
et un renvoi de Stripe ne retraite rien.

Ce module ne fait qu'une chose : identifier de qui parle l'événement, puis demander
un recalcul du droit. La livraison à l'application arrive au lot L3 — ici on se
contente de créer une EntitlementDelivery en attente.
"""
import logging

from djstripe.event_handlers import djstripe_receiver

from .models import App, EntitlementDelivery, Plan
from .services import period_end_of, recompute_entitlement

logger = logging.getLogger("billing")

# Événements qui peuvent changer un droit. `customer.updated` et `customer.tax_id.*`
# ne sont PAS ici : ils n'affectent que les coordonnées de facturation, que dj-stripe
# mire déjà tout seul — les écouter ne produirait que des livraisons inutiles.
SUBSCRIPTION_EVENTS = [
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
]


def resolve_target(obj):
    """(App, external_user_id) depuis un objet d'événement Stripe, ou (None, None).

    Deux chemins : les `metadata` posées au checkout, et `client_reference_id` au
    format "<app>:<user>" en secours — Stripe ne propage pas les metadata sur tous
    les objets issus d'une même session.
    """
    obj = obj or {}
    metadata = obj.get("metadata") or {}
    slug, user_id = metadata.get("app"), metadata.get("external_user_id")

    if not (slug and user_id):
        reference = obj.get("client_reference_id") or ""
        if ":" in reference:
            slug, user_id = reference.split(":", 1)

    if not (slug and user_id):
        return None, None

    app = App.objects.filter(slug=slug).first()
    return (app, user_id) if app else (None, None)


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


def handle_subscription_event(obj):
    """Recalcule le droit décrit par un objet `subscription` Stripe.

    Renvoie l'EntitlementDelivery créée, ou None si l'événement ne concerne aucune
    app connue — un compte Stripe partagé reçoit aussi des événements qui ne nous
    regardent pas, et les ignorer silencieusement est le comportement correct.
    """
    app, user_id = resolve_target(obj)
    if app is None:
        logger.info("stripe_event_ignored", extra={"reason": "unresolved_target"})
        return None

    plan, interval = plan_for_price(app, price_id_of(obj))
    entitlement = recompute_entitlement(
        app,
        user_id,
        stripe_status=obj.get("status", "") or "",
        plan=plan,
        interval=interval,
        period_end=period_end_of(obj),
        stripe_customer_id=obj.get("customer") or "",
        quantity=quantity_of(obj),
    )
    delivery = EntitlementDelivery.objects.create(
        entitlement=entitlement, payload=entitlement.payload()
    )
    # Asynchrone : un webhook Stripe ne doit jamais échouer parce qu'un site de la
    # flotte est injoignable. dj-stripe a déjà répondu 200 à Stripe à ce stade.
    from .tasks import deliver_entitlement

    deliver_entitlement.delay(str(delivery.pk))
    return delivery


@djstripe_receiver(SUBSCRIPTION_EVENTS)
def on_subscription_event(sender, event, **kwargs):
    """Point d'entrée réel des webhooks : dj-stripe nous appelle après avoir vérifié
    la signature Stripe et mis son miroir à jour."""
    handle_subscription_event(event.data.get("object") if event.data else None)
