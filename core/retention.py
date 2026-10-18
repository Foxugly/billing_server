"""Durées de conservation (audit 2026-10-18, M5).

- `EntitlementDelivery` livrées ou en échec : `BILLING_DELIVERY_RETENTION_DAYS`
  (180 j par défaut). Le payload contient l'identifiant de l'utilisateur. On
  garde toujours la **dernière livraison réussie** de chaque droit : la
  réconciliation s'y compare, la purger lui ferait re-pousser chaque droit.
  Les livraisons `pending` ne sont jamais purgées : elles ont encore un travail.
- `djstripe.Event` et `djstripe.WebhookEventTrigger` (corps bruts des webhooks,
  e-mails compris) : `BILLING_STRIPE_EVENT_RETENTION_DAYS` (365 j). Stripe ne
  conserve lui-même ses événements que 30 jours ; au-delà ils ne servent plus
  qu'au rejeu console, qui n'a plus de sens après un an.

`AppCustomer.email` n'est pas purgé ici : il sert tant que la relation client
existe, et son effacement passe par l'API `erase/` (audit B1).
"""
from datetime import timedelta

from django.conf import settings
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from .models import Entitlement, EntitlementDelivery


def purge_expired_data(now=None) -> dict:
    from djstripe.models import Event, WebhookEventTrigger

    now = now or timezone.now()
    delivery_cutoff = now - timedelta(days=settings.BILLING_DELIVERY_RETENTION_DAYS)
    event_cutoff = now - timedelta(days=settings.BILLING_STRIPE_EVENT_RETENTION_DAYS)

    last_delivered = Entitlement.objects.annotate(
        last=Subquery(
            EntitlementDelivery.objects.filter(
                entitlement=OuterRef("pk"), status=EntitlementDelivery.DELIVERED
            )
            .order_by("-delivered_at", "-created_at")
            .values("pk")[:1]
        )
    ).exclude(last=None).values_list("last", flat=True)

    deliveries, _ = (
        EntitlementDelivery.objects.filter(
            status__in=(EntitlementDelivery.DELIVERED, EntitlementDelivery.FAILED),
            created_at__lt=delivery_cutoff,
        )
        .exclude(pk__in=list(last_delivered))
        .delete()
    )
    triggers, _ = WebhookEventTrigger.objects.filter(created__lt=event_cutoff).delete()
    events, _ = Event.objects.filter(created__lt=event_cutoff).delete()

    return {"deliveries": deliveries, "stripe_events": events, "webhook_triggers": triggers}
