"""Réconciliation : le filet sous la livraison (§6.4).

Un push peut se perdre définitivement — application hors ligne trop longtemps,
livraison passée en échec, base restaurée. Ce module recalcule les droits et ne
repousse que ceux qui ont réellement changé.

Il traite aussi le cas qu'aucun webhook ne couvre : une **période de grâce qui
expire**. Stripe n'émet aucun événement à cet instant précis, donc sans ce
balayage un accès en grâce resterait ouvert indéfiniment.
"""
import logging

from django.utils import timezone

from .models import Entitlement, EntitlementDelivery
from .services import recompute_entitlement

logger = logging.getLogger("billing")


def _last_delivered_payload(entitlement):
    delivery = (
        entitlement.deliveries.filter(status=EntitlementDelivery.DELIVERED)
        .order_by("-delivered_at")
        .first()
    )
    return delivery.payload if delivery else None


def _differs(payload, last):
    """Compare ce qui engage l'application, en ignorant l'horodatage d'émission."""
    if last is None:
        return True
    keys = ("is_paid", "status", "plan", "interval", "quotas", "current_period_end", "grace_until")
    return any(payload.get(k) != last.get(k) for k in keys)


def reconcile(app_slug=None, push_diff=False):
    """Recalcule les droits ; renvoie (examinés, modifiés, poussés)."""
    queryset = Entitlement.objects.select_related("app")
    if app_slug:
        queryset = queryset.filter(app__slug=app_slug)

    examined = changed = pushed = 0
    now = timezone.now()

    for entitlement in queryset:
        examined += 1

        # Une grâce arrivée à échéance ferme l'accès. Aucun webhook Stripe ne
        # survient à ce moment-là : c'est ici, et nulle part ailleurs, que ça se joue.
        if (
            entitlement.grace_until
            and entitlement.grace_until <= now
            and entitlement.is_paid
            and entitlement.source == Entitlement.STRIPE
        ):
            entitlement = recompute_entitlement(
                entitlement.app,
                entitlement.external_user_id,
                stripe_status=entitlement.status,
                period_end=entitlement.current_period_end,
            )

        # La référence est le dernier payload RÉELLEMENT livré, pas l'état d'avant
        # le recalcul : c'est la seule façon de rattraper un push perdu, où l'état
        # local est correct mais où l'application ne l'a jamais reçu.
        after = entitlement.payload()
        if not _differs(after, _last_delivered_payload(entitlement)):
            continue

        changed += 1
        if push_diff:
            delivery = EntitlementDelivery.objects.create(entitlement=entitlement, payload=after)
            from .tasks import deliver_entitlement

            deliver_entitlement.delay(str(delivery.pk))
            pushed += 1

    logger.info(
        "reconcile_done", extra={"examined": examined, "changed": changed, "pushed": pushed}
    )
    return examined, changed, pushed
