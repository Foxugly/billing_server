"""Authentification service-à-service par signature HMAC.

Implémenté en `permission_class` et non en `authentication_class` pour une raison
précise : la signature porte sur le **corps brut**, et DRF lève
`RawPostDataException` si on lit `request.body` après que la requête a été parsée.
Les permissions s'exécutent dans `initial()`, avant tout accès à `request.data` —
c'est le seul endroit où le corps brut est encore lisible sans risque.
"""
import logging

from rest_framework.permissions import BasePermission

from .models import App
from .signing import verify_signature

logger = logging.getLogger("billing")

HEADER_APP = "HTTP_X_FOXUGLY_APP"
HEADER_TIMESTAMP = "HTTP_X_FOXUGLY_TIMESTAMP"
HEADER_SIGNATURE = "HTTP_X_FOXUGLY_SIGNATURE"


class HasValidAppSignature(BasePermission):
    """Autorise si la requête est signée par une app connue et active.

    Pose `request.billing_app` pour la vue. Une app désactivée est refusée même si
    sa signature est parfaite : c'est le bouton d'arrêt d'urgence.
    """

    message = "Signature de service invalide ou absente."

    def has_permission(self, request, view):
        meta = request.META
        slug = meta.get(HEADER_APP, "")
        timestamp = meta.get(HEADER_TIMESTAMP, "")
        signature = meta.get(HEADER_SIGNATURE, "")

        if not slug:
            return False

        app = App.objects.filter(slug=slug).first()
        if app is None:
            # Même réponse que pour une signature invalide : ne pas révéler quels
            # slugs existent.
            logger.info("s2s_unknown_app", extra={"slug": slug})
            return False

        if not verify_signature(app, request.body, timestamp, signature):
            logger.info("s2s_bad_signature", extra={"slug": slug})
            return False

        if not app.active:
            logger.info("s2s_inactive_app", extra={"slug": slug})
            self.message = "Application désactivée."
            return False

        request.billing_app = app
        return True
