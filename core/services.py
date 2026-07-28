"""Dérivation des droits depuis l'état Stripe.

C'est ici que « un abonnement Stripe » devient « cet utilisateur a le droit de… ».
Aucun appel réseau : la fonction lit ce qu'on lui passe et nos propres modèles.
"""
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from .models import Entitlement

# Les seuls statuts Stripe qui ouvrent l'accès sans condition.
PAID_STATUSES = {"active", "trialing"}
# Statuts qui ferment l'accès immédiatement, même si une grâce est en cours.
TERMINAL_STATUSES = {"canceled", "unpaid", "incomplete_expired"}


def period_end_of(subscription):
    """Fin de période d'un abonnement Stripe, en datetime aware, ou None.

    Les versions récentes de l'API Stripe portent `current_period_end` sur les
    **items** de l'abonnement et non plus à sa racine. Lire la racine seule renvoie
    None en silence, et un abonnement parfaitement payé paraît alors expiré — d'où
    la lecture des items d'abord, avec repli explicite sur la racine.
    """
    if not isinstance(subscription, dict):
        return None

    epoch = None
    try:
        epoch = subscription["items"]["data"][0].get("current_period_end")
    except (KeyError, IndexError, TypeError):
        epoch = None
    if not epoch:
        epoch = subscription.get("current_period_end")
    if not epoch:
        return None

    return datetime.fromtimestamp(epoch, tz=timezone.get_current_timezone())


def recompute_entitlement(
    app,
    external_user_id,
    *,
    stripe_status="",
    plan=None,
    interval="",
    period_end=None,
    stripe_customer_id="",
    quantity=1,
):
    """Recalcule et persiste le droit d'un utilisateur. Renvoie l'Entitlement.

    `source=manual` (accès offert depuis la console) est **préservé** : un webhook
    Stripe ne doit pas révoquer une décision humaine.
    """
    ent, _ = Entitlement.objects.get_or_create(app=app, external_user_id=external_user_id)

    if ent.source == Entitlement.MANUAL:
        return ent

    now = timezone.now()
    grace_days = getattr(settings, "BILLING_GRACE_DAYS", 7)

    if stripe_status in PAID_STATUSES:
        is_paid, grace_until = True, None
    elif stripe_status in TERMINAL_STATUSES or not stripe_status:
        is_paid, grace_until = False, None
    else:
        # past_due, incomplete… : Stripe relance la carte pendant plusieurs jours.
        # On réutilise l'échéance existante si elle est déjà posée — sinon un échec
        # de facture quotidien repousserait la grâce indéfiniment.
        grace_until = ent.grace_until or (now + timedelta(days=grace_days))
        is_paid = grace_days > 0 and grace_until > now

    ent.status = stripe_status
    ent.is_paid = is_paid
    ent.grace_until = grace_until
    ent.plan_code = plan.code if plan else ""
    ent.interval = interval if plan else ""
    # quotas_for() fait suivre la quantite pour les plans factures a l'unite.
    ent.quotas = plan.quotas_for(quantity) if (plan and is_paid) else {}
    ent.current_period_end = period_end
    # Jamais écrasé par du vide : un événement ultérieur peut ne pas le porter,
    # et on perdrait le lien vers le portail client.
    if stripe_customer_id:
        ent.stripe_customer_id = stripe_customer_id
    ent.source = Entitlement.STRIPE
    ent.save()
    return ent
