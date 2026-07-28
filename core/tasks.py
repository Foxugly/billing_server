"""Livraison des droits aux applications de la flotte.

Le central pousse, l'application encaisse. Si l'application est injoignable, on
réessaie selon un barème dégressif ; le droit finit toujours par arriver, et à
défaut la réconciliation quotidienne (`sync_entitlements`) rattrape l'écart.

Ce chemin est **asynchrone par construction** : un webhook Stripe ne doit jamais
échouer parce qu'un site de la flotte est en maintenance.
"""
import json
import logging
import time
from urllib.parse import urlparse

import requests
from celery import shared_task
from django.utils import timezone

from .models import EntitlementDelivery
from .signing import sign_payload

logger = logging.getLogger("billing")

# Barème de reprise, en secondes : 1 min, 5 min, 15 min, 1 h, 6 h, 24 h.
# Au-delà du dernier rang, la livraison passe en échec et attend un rejeu manuel
# ou la réconciliation quotidienne.
RETRY_SCHEDULE = [60, 300, 900, 3600, 21600, 86400]

DELIVERY_TIMEOUT_SECONDS = 10


def _post_signed(app, body: bytes):
    timestamp = int(time.time())
    path = urlparse(app.entitlement_url).path
    return requests.post(
        app.entitlement_url,
        data=body,
        timeout=DELIVERY_TIMEOUT_SECONDS,
        headers={
            "Content-Type": "application/json",
            "X-Foxugly-App": app.slug,
            "X-Foxugly-Timestamp": str(timestamp),
            "X-Foxugly-Signature": sign_payload(app.shared_secret, "POST", path, body, timestamp),
        },
    )


@shared_task(name="core.deliver_entitlement")
def deliver_entitlement(delivery_id):
    """Livre un droit à son application. Idempotente : rejouable sans risque.

    Renvoie le statut atteint, pour que l'appelant (ou un test) puisse l'observer.
    """
    delivery = EntitlementDelivery.objects.filter(pk=delivery_id).select_related(
        "entitlement__app"
    ).first()
    if delivery is None:
        logger.warning("delivery_missing", extra={"delivery_id": str(delivery_id)})
        return "missing"

    if delivery.status == EntitlementDelivery.DELIVERED:
        # Rejouée par erreur : ne pas renvoyer, ne pas compter de tentative.
        return EntitlementDelivery.DELIVERED

    app = delivery.entitlement.app
    if not app.active:
        logger.info("delivery_skipped_inactive_app", extra={"app": app.slug})
        return "skipped"

    body = json.dumps({**delivery.payload, "delivery_id": str(delivery.pk)}).encode()

    try:
        response = _post_signed(app, body)
        status_code = response.status_code
    except Exception as exc:  # réseau, DNS, TLS, timeout…
        return _schedule_retry(delivery, f"{type(exc).__name__}: {exc}")

    # 409 = l'application a déjà vu ce delivery_id. C'est un succès de notre point
    # de vue : l'état est bien arrivé, une seule fois.
    if 200 <= status_code < 300 or status_code == 409:
        delivery.status = EntitlementDelivery.DELIVERED
        delivery.delivered_at = timezone.now()
        delivery.attempts += 1
        delivery.last_error = ""
        delivery.next_retry_at = None
        delivery.save()
        logger.info("delivery_ok", extra={"app": app.slug, "code": status_code})
        return EntitlementDelivery.DELIVERED

    return _schedule_retry(delivery, f"HTTP {status_code}")


def _schedule_retry(delivery, error: str):
    """Compte la tentative et pose la prochaine échéance, ou déclare l'échec."""
    delivery.attempts += 1
    delivery.last_error = error[:500]

    if delivery.attempts > len(RETRY_SCHEDULE):
        delivery.status = EntitlementDelivery.FAILED
        delivery.next_retry_at = None
        logger.error("delivery_failed", extra={"delivery_id": str(delivery.pk), "error": error})
    else:
        delay = RETRY_SCHEDULE[delivery.attempts - 1]
        delivery.status = EntitlementDelivery.PENDING
        delivery.next_retry_at = timezone.now() + timezone.timedelta(seconds=delay)
        logger.info("delivery_retry", extra={"delivery_id": str(delivery.pk), "in_seconds": delay})

    delivery.save()
    return delivery.status


@shared_task(name="core.flush_pending_deliveries")
def flush_pending_deliveries():
    """Reprend les livraisons dont l'échéance est passée. Cadencée par Celery beat.

    C'est ce qui rend les reprises fiables sans dépendre d'un `countdown` Celery
    qui serait perdu au redémarrage du worker.
    """
    due = EntitlementDelivery.objects.filter(
        status=EntitlementDelivery.PENDING, next_retry_at__lte=timezone.now()
    ).values_list("pk", flat=True)

    for delivery_id in list(due):
        deliver_entitlement(delivery_id)

    return len(due)


@shared_task(name="core.reconcile_entitlements")
def reconcile_entitlements():
    """Réconciliation quotidienne (§6.4). Pousse les écarts constatés."""
    from .reconcile import reconcile

    examined, changed, pushed = reconcile(push_diff=True)
    return {"examined": examined, "changed": changed, "pushed": pushed}


def ping_app(app):
    """Teste la connectivité et le secret vers une app. Renvoie (ok, détail).

    Appelé depuis la console : le test part du serveur, qui détient le secret —
    il ne doit jamais atteindre un bundle SPA.
    """
    body = json.dumps({"ping": True}).encode()
    try:
        response = _post_signed(app, body)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    ok = 200 <= response.status_code < 300
    return ok, f"HTTP {response.status_code}"
