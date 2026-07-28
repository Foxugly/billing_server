"""Alias transitoire de l'ancien préfixe d'API.

Toute l'API vit désormais sous `/api/v1/`. Backends et frontends se déploient
séparément : sans cet alias, il existe une fenêtre pendant laquelle un bundle
encore en cache appelle `/api/…` et prend un 404 en pleine figure.

La réécriture se fait ici plutôt qu'en déclarant deux fois les URLs : dupliquer
la table dupliquerait aussi le schéma OpenAPI et les noms de routes, alors que
`reverse()` doit continuer à produire l'URL canonique.

**Ce module est fait pour être supprimé.** Chaque réécriture est journalisée :
quand `legacy_api_prefix` n'apparaît plus dans les logs, plus personne n'appelle
l'ancien chemin et on peut retirer le middleware et cette ligne de MIDDLEWARE.
"""
import logging

logger = logging.getLogger("billing")

LEGACY_PREFIX = "/api/"
CANONICAL_PREFIX = "/api/v1/"


class LegacyApiPrefixMiddleware:
    """Réécrit `/api/<x>` en `/api/v1/<x>`, sauf ce qui y est déjà."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if path.startswith(LEGACY_PREFIX) and not path.startswith(CANONICAL_PREFIX):
            nouveau = CANONICAL_PREFIX + path[len(LEGACY_PREFIX) :]
            # PATH_INFO est la source de vérité de la résolution d'URL ; `path`
            # en découle (préfixe de montage inclus). Les deux doivent bouger,
            # sinon une vue qui relit `request.path` verrait l'ancien chemin --
            # c'est précisément ce que fait une vérification de signature.
            request.path_info = nouveau
            request.META["PATH_INFO"] = nouveau
            request.path = request.META.get("SCRIPT_NAME", "") + nouveau
            logger.info("legacy_api_prefix", extra={"from": path, "to": nouveau})
        return self.get_response(request)
