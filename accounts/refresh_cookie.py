"""Refresh de la console en cookie HttpOnly (opt-in, design du pilote PushIT_server #69).

La console Angular s'y abonne en envoyant ``X-Auth-Mode: cookie`` sur ses appels
d'authentification. Pour ces requêtes, le refresh voyage dans un cookie
``HttpOnly; Secure; SameSite=Lax`` limité à ``/api/v1/auth/`` sur l'hôte de l'API,
et **disparaît du corps JSON** : le JavaScript ne le voit jamais, une XSS ne peut
plus l'exfiltrer. Sans l'en-tête (anciens bundles, scripts d'exploitation), le
contrat historique « jetons dans le corps » est inchangé.

L'API HMAC des apps de la flotte (``/api/v1/`` hors ``auth/`` et ``admin/``) n'est
pas concernée.

CSRF : dès que le cookie authentifie ``token/refresh/`` et ``token/blacklist/``,
une page tierce pourrait tenter de les piloter. Deux gardes indépendantes :

* l'en-tête ``X-Auth-Mode`` rend chaque appel non « simple » au sens CORS : le
  navigateur envoie un preflight, et seules les origines explicites
  (``CORS_ALLOWED_ORIGINS``, credentials actifs) passent ;
* toute requête en mode cookie doit en plus porter un ``Origin`` présent dans
  ``CORS_ALLOWED_ORIGINS`` (vérifié côté serveur, 403 sinon — y compris au login).

``SameSite=Lax`` suffit : billing.foxugly.com et billing-api.foxugly.com partagent
le domaine enregistrable foxugly.com, ils sont *same-site* bien que cross-origin.
"""
from __future__ import annotations

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

AUTH_MODE_HEADER = "X-Auth-Mode"
AUTH_MODE_COOKIE = "cookie"
# Claim du refresh (conservé par la rotation) : le cookie survit-il à la fermeture
# du navigateur (« se souvenir de moi ») ?
PERSIST_CLAIM = "persist"


def cookie_name() -> str:
    return settings.AUTH_REFRESH_COOKIE_NAME


def wants_cookie(request) -> bool:
    """Vrai quand le client a choisi le transport du refresh par cookie."""
    return request.headers.get(AUTH_MODE_HEADER, "").strip().lower() == AUTH_MODE_COOKIE


def origin_allowed(request) -> bool:
    origin = request.headers.get("Origin")
    return bool(origin) and origin in settings.CORS_ALLOWED_ORIGINS


def origin_guard(request):
    """403 pour une requête en mode cookie venue d'une origine non autorisée (CSRF)."""
    if wants_cookie(request) and not origin_allowed(request):
        return Response(
            {
                "code": "origin_not_allowed",
                "detail": "L'authentification par cookie n'est admise que depuis une origine autorisée.",
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def read_refresh_cookie(request) -> str:
    return request.COOKIES.get(cookie_name(), "")


def token_persists(refresh: str) -> bool:
    """Lit le claim ``persist`` sans vérifier la signature.

    Appelé sur un jeton que ce processus vient d'émettre ou de roter (signature
    déjà vérifiée en amont), ou pour choisir la durée d'un cookie : on lit un
    claim, on ne lui accorde aucune confiance.
    """
    try:
        token = RefreshToken(refresh, verify=False)
    except TokenError:
        return True
    return token.payload.get(PERSIST_CLAIM, True) is not False


def set_refresh_cookie(response, refresh: str) -> None:
    max_age = None
    if token_persists(refresh):
        max_age = int(api_settings.REFRESH_TOKEN_LIFETIME.total_seconds())
    response.set_cookie(
        cookie_name(),
        refresh,
        max_age=max_age,
        path=settings.AUTH_REFRESH_COOKIE_PATH,
        domain=None,  # host-only : billing-api, jamais tout foxugly.com
        secure=settings.AUTH_REFRESH_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
    )


def clear_refresh_cookie(response) -> None:
    response.delete_cookie(cookie_name(), path=settings.AUTH_REFRESH_COOKIE_PATH, samesite="Lax")


def deliver_tokens(request, response):
    """En mode cookie, déplace ``refresh`` du corps JSON vers le cookie. Sinon, rien."""
    if not wants_cookie(request):
        return response
    data = getattr(response, "data", None)
    if isinstance(data, dict) and data.get("refresh"):
        set_refresh_cookie(response, data.pop("refresh"))
    return response
