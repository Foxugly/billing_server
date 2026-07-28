"""Signature HMAC des échanges service-à-service (§8).

Le même mécanisme dans les deux sens : billing signe ce qu'il pousse aux
applications, et vérifie ce qu'elles lui envoient. Il n'y a pas de jeton porteur
à faire expirer, pas de rotation de certificat — juste un secret partagé par app.
"""
import hashlib
import hmac
import time

from django.core.cache import cache
from django.utils import timezone

# Tolérance d'horloge entre les deux serveurs. 5 minutes : assez large pour une
# dérive NTP normale, assez court pour qu'une requête interceptée soit périmée.
SIGNATURE_WINDOW_SECONDS = 300

# Durée pendant laquelle l'ancien secret reste accepté après une rotation. Sans
# ce sursis, une rotation couperait l'app le temps qu'elle redéploie.
SECRET_ROTATION_GRACE_SECONDS = 24 * 3600

_REPLAY_PREFIX = "billing:sig:"


def signed_string(body: bytes, timestamp: int) -> bytes:
    """Ce qui est réellement signé : l'horodatage lie la signature à un instant."""
    return f"{timestamp}.".encode() + (body or b"")


def sign_payload(secret: str, body: bytes, timestamp: int) -> str:
    """La valeur de l'en-tête X-Foxugly-Signature, préfixe compris."""
    digest = hmac.new(secret.encode(), signed_string(body, timestamp), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _secrets_valid_for(app) -> list:
    """Les secrets acceptables : l'actuel, plus l'ancien pendant la fenêtre de rotation."""
    secrets_list = [app.shared_secret]
    if app.previous_shared_secret and app.secret_rotated_at:
        age = (timezone.now() - app.secret_rotated_at).total_seconds()
        if age < SECRET_ROTATION_GRACE_SECONDS:
            secrets_list.append(app.previous_shared_secret)
    return [s for s in secrets_list if s]


def verify_signature(app, body: bytes, timestamp, signature: str) -> bool:
    """Vrai si la signature est valide, dans la fenêtre, et jamais vue auparavant.

    L'anti-rejeu s'appuie sur le cache Django : il DOIT être partagé entre les
    workers gunicorn (Redis), sinon un rejeu passerait une fois par worker.
    """
    if not signature or not signature.startswith("sha256="):
        return False

    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False

    # Fenêtre bornée des deux côtés : un horodatage dans le futur est tout aussi
    # suspect qu'un horodatage périmé.
    if abs(int(time.time()) - ts) > SIGNATURE_WINDOW_SECONDS:
        return False

    for secret in _secrets_valid_for(app):
        if hmac.compare_digest(sign_payload(secret, body, ts), signature):
            break
    else:
        return False

    # Rejeu : une signature n'est utilisable qu'une fois, mémorisée le temps de
    # la fenêtre — au-delà, l'horodatage la rejette de toute façon.
    key = _REPLAY_PREFIX + hashlib.sha256(signature.encode()).hexdigest()
    if not cache.add(key, 1, timeout=SIGNATURE_WINDOW_SECONDS):
        return False

    return True
