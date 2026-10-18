"""Résiliation et effacement d'un utilisateur d'une app (audit 2026-10-18, B1).

Appelé par le backend d'une app quand un utilisateur supprime son compte. Sans
cela, un payeur qui supprime son compte continue d'être prélevé, et n'a plus de
compte pour ouvrir le portail Stripe.

Politique de résiliation (choisie le 2026-10-18) : **immédiate, sans
remboursement ni prorata** (`prorate=False`, `invoice_now=False` — le comportement
par défaut de Stripe pour `cancel`). La période déjà payée n'est pas remboursée ;
aucune facture finale n'est émise. `at_period_end=True` reste disponible pour une
résiliation demandée par l'utilisateur sans suppression de compte.

Cloisonnement : tout part de l'app signataire. On ne lit que SES clients
(`Entitlement` / `AppCustomer` de cette app) et on ne touche qu'aux abonnements
qui lui appartiennent (metadata `app`, ou à défaut un prix de son catalogue).
"""
import logging
import uuid

from django.db import transaction
from django.db.models import Q
from stripe import InvalidRequestError

from . import webhooks
from .models import AppCustomer, Entitlement, ErasedUser

logger = logging.getLogger("billing")

# Tout ce qui peut encore prélever, ou le faire plus tard.
LIVE_STATUSES = {"active", "trialing", "past_due", "unpaid", "incomplete", "paused"}


class BillingUnconfigured(Exception):
    """Un client Stripe est connu, mais Stripe n'est pas configuré ici."""


def customer_ids_for(app, external_user_id):
    """Les `cus_…` connus pour ce couple (app, utilisateur), sans doublon."""
    ids = []
    ent = Entitlement.objects.filter(app=app, external_user_id=external_user_id).first()
    if ent and ent.stripe_customer_id:
        ids.append(ent.stripe_customer_id)
    customer = (
        AppCustomer.objects.filter(app=app, external_user_id=external_user_id)
        .select_related("customer")
        .first()
    )
    if customer and customer.customer and customer.customer.id not in ids:
        ids.append(customer.customer.id)
    return ids


def subscription_belongs_to(app, subscription) -> bool:
    slug = (subscription.get("metadata") or {}).get("app")
    if slug:
        return slug == app.slug
    plan, _ = webhooks.plan_for_price(app, webhooks.price_id_of(subscription))
    return plan is not None


def cancel_subscriptions(stripe, app, external_user_id, *, at_period_end=False, customer_ids=None):
    """Résilie les abonnements vivants de cet utilisateur pour CETTE app.

    Renvoie les identifiants des abonnements résiliés (ou programmés pour l'être).
    Une `StripeError` remonte telle quelle : l'appelant la traduit en 502.
    """
    canceled = []
    ids = customer_ids if customer_ids is not None else customer_ids_for(app, external_user_id)
    for customer_id in ids:
        subs = stripe.Subscription.list(customer=customer_id, status="all", limit=100).get("data", [])
        for sub in subs:
            if sub.get("status") not in LIVE_STATUSES or not subscription_belongs_to(app, sub):
                continue
            if at_period_end:
                if sub.get("cancel_at_period_end"):
                    continue
                stripe.Subscription.modify(sub["id"], cancel_at_period_end=True)
            else:
                stripe.Subscription.cancel(sub["id"], prorate=False, invoice_now=False)
            canceled.append(sub["id"])
    if canceled:
        logger.info(
            "subscriptions_canceled",
            extra={"app": app.slug, "count": len(canceled), "at_period_end": at_period_end},
        )
    return canceled


def _shared_elsewhere(customer_id, app, external_user_id) -> bool:
    """Ce `cus_…` est-il référencé par un autre utilisateur ou une autre app ?"""
    mine = Q(app=app, external_user_id=external_user_id)
    return (
        Entitlement.objects.filter(stripe_customer_id=customer_id).exclude(mine).exists()
        or AppCustomer.objects.filter(customer__id=customer_id).exclude(mine).exists()
    )


def erase_user(stripe, app, external_user_id):
    """Résilie, supprime le client Stripe, puis efface les données locales.

    Renvoie None s'il n'y avait rien à effacer (idempotent), sinon un résumé.
    Stripe passe AVANT la base : si Stripe échoue, rien n'est effacé localement et
    un nouvel appel retrouve le `cus_…` pour terminer le travail.
    """
    customer_ids = customer_ids_for(app, external_user_id)
    app_customer = AppCustomer.objects.filter(app=app, external_user_id=external_user_id).first()
    entitlement = Entitlement.objects.filter(app=app, external_user_id=external_user_id).first()

    if not (customer_ids or app_customer or entitlement):
        ErasedUser.mark(app, external_user_id)
        return None
    if customer_ids and stripe is None:
        raise BillingUnconfigured()

    canceled = []
    deleted = 0
    if customer_ids:
        canceled = cancel_subscriptions(stripe, app, external_user_id, customer_ids=customer_ids)
        for customer_id in customer_ids:
            if _shared_elsewhere(customer_id, app, external_user_id):
                logger.warning("erase_customer_shared", extra={"app": app.slug})
                continue
            try:
                stripe.Customer.delete(customer_id)
            except InvalidRequestError as exc:
                if "No such customer" not in str(exc):
                    raise
            deleted += 1

    with transaction.atomic():
        ErasedUser.mark(app, external_user_id)
        if app_customer is not None:
            pseudonym = f"erased-{uuid.uuid4().hex}"
            app_customer.external_user_id = pseudonym
            app_customer.email = f"{pseudonym}@erased.invalid"
            app_customer.customer = None
            app_customer.save(update_fields=["external_user_id", "email", "customer"])
        if entitlement is not None:
            # Les livraisons (et leur payload, qui porte l'identifiant) partent avec.
            entitlement.delete()

    logger.info("user_erased", extra={"app": app.slug, "canceled": len(canceled), "deleted": deleted})
    return {"erased": True, "canceled": canceled, "stripe_customers_deleted": deleted}
