"""Le seul module qui parle à Stripe.

Tout est concentré ici pour qu'un test n'ait qu'un point à simuler, et pour qu'on
puisse répondre 503 proprement tant que les clés ne sont pas configurées — ce qui
est le cas jusqu'au lot L6.
"""
import logging
from urllib.parse import urlparse

from django.conf import settings

logger = logging.getLogger("billing")


def stripe_configured() -> bool:
    return bool(settings.STRIPE_LIVE_SECRET_KEY or settings.STRIPE_TEST_SECRET_KEY)


def stripe_client():
    """Le SDK Stripe prêt à l'emploi, ou None si aucune clé n'est configurée."""
    if not stripe_configured():
        return None
    import stripe

    stripe.api_key = (
        settings.STRIPE_LIVE_SECRET_KEY if settings.STRIPE_LIVE_MODE else settings.STRIPE_TEST_SECRET_KEY
    )
    return stripe


def url_is_allowed_for(app, url: str) -> bool:
    """Une URL de retour doit rester sur un domaine de l'application appelante.

    Sans ce contrôle, une app compromise — ou un simple bug de concaténation —
    transformerait le service en redirecteur ouvert : Stripe renverrait le client
    payant vers un domaine arbitraire, avec toute la confiance visuelle du tunnel.
    """
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False

    allowed_hosts = {urlparse(app.base_url).hostname}
    frontend = getattr(settings, "FRONTEND_BASE_URL", "")
    if frontend:
        allowed_hosts.add(urlparse(frontend).hostname)

    host = parsed.hostname or ""
    # Le SPA d'une app vit sur un sous-domaine frère de son API (poker.foxugly.com
    # face à poker-api.foxugly.com) : on accepte le même domaine parent.
    api_host = urlparse(app.base_url).hostname or ""
    parent = api_host.split(".", 1)[1] if "." in api_host else api_host

    return host in allowed_hosts or (parent and host.endswith("." + parent))
