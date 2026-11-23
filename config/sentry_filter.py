"""Ce qui mérite d'arriver dans Sentry, et ce qui n'est que du bruit d'atelier.

Sur les huit issues relevées à la revue du 2026-07-29, **quatre** étaient des
fautes de frappe dans des `manage.py shell` d'audit — `App.is_active` au lieu de
`App.active`, `Plan.slug` au lieu de `Plan.code`. Elles arrivaient dans Sentry
exactement comme une erreur de production. Une boîte qui mélange les deux cesse
d'être un signal : on prend l'habitude de la voir rouge.

Le filtre fait donc deux choses distinctes :

- il **étiquette** chaque événement avec la commande de gestion qui l'a produit,
  quand il y en a une. Un `sync_entitlements` qui échoue reste une panne — la
  réconciliation quotidienne est un rouage de production — mais on veut le
  distinguer d'un plantage de requête web au premier coup d'œil ;
- il **jette** ce qui vient du `shell`. Là, c'est une session interactive : la
  trace est déjà sous les yeux de celui qui l'a provoquée.

On ne jette rien d'autre. `migrate` et `collectstatic` tournent au déploiement :
s'ils échouent, le déploiement est cassé et Sentry a son mot à dire.

Ce que le SDK peut collecter est fixé par `DATA_COLLECTION` (sentry-sdk 3.0 a
supprimé `send_default_pii`) : jamais plus qu'avant. sentry-sdk 3.0 a aussi
supprimé l'`EventScrubber` intégré : `config.sentry_scrub` le remplace, en tête
de `before_send` (`scrub_event`) et en `before_send_span` (`scrub_span`).
"""
import sys

from config.sentry_scrub import scrub_event, scrub_span

# Seule commande dont les erreurs sont, par nature, sous les yeux de leur auteur.
COMMANDES_MUETTES = {"shell", "dbshell"}


def django_command(argv=None) -> str:
    """La commande de gestion en cours, ou une chaîne vide hors `manage.py`.

    `argv` est injectable pour les tests : lire `sys.argv` en dur rendrait le
    comportement invérifiable autrement qu'en lançant un vrai processus.
    """
    argv = sys.argv if argv is None else argv
    if not argv or not argv[0].endswith("manage.py"):
        return ""
    return argv[1] if len(argv) > 1 else ""


# Termes proxy/IP que le guide de migration 3.0 donne pour retrouver
# `send_default_pii=False` ; s'ajoutent à la liste noire de clés sensibles du SDK.
_TERMES_PII = ["forwarded", "-ip", "remote-", "via", "-user"]

#: Ni utilisateur (id, e-mail, IP), ni cookies, ni corps de requête (celui d'un
#: checkout porte l'e-mail du client, M4), ni arguments Celery, ni paramètres SQL.
DATA_COLLECTION = {
    "user_info": False,
    "cookies": {"mode": "off"},
    "http_headers": {"request": {"mode": "denylist", "terms": _TERMES_PII}},
    "http_bodies": [],
    "url_query_params": {"mode": "denylist", "terms": _TERMES_PII},
    "graphql": {"document": False, "variables": False},
    "gen_ai": {"inputs": False, "outputs": False},
    "mcp": {"inputs": False, "outputs": False},
    "database_query_data": False,
    "queues": False,
    "stack_frame_variables": {"mode": "denylist"},
    "frame_context_lines": 5,
}

def before_send(event, hint, argv=None):
    """Hook `before_send` de Sentry. Renvoie l'événement, ou None pour le taire."""
    scrub_event(event, hint)
    commande = django_command(argv)
    if not commande:
        return event

    if commande in COMMANDES_MUETTES:
        return None

    event.setdefault("tags", {})["django_command"] = commande
    return event


def sentry_init_options():
    """Les options de `sentry_sdk.init` hors DSN/environnement/échantillonnage.

    Isolées ici pour être testables : en TEST/DEV, `settings` n'initialise pas Sentry.
    """
    import logging

    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    return {
        # Ni PII, ni cookies, ni corps de requête : voir DATA_COLLECTION.
        "data_collection": DATA_COLLECTION,
        # Le corps d'un checkout porte l'e-mail du client : jamais dans Sentry (M4).
        "max_request_body_size": "never",
        # 3.0 passe le défaut à True : un simple message emporterait la pile et
        # ses variables locales. On garde le comportement de 2.x.
        "attach_stacktrace": False,
        "integrations": [
            # 3.0 trace le cache par défaut : on garde le volume de spans de 2.x.
            DjangoIntegration(cache_spans=False),
            # 3.0 n'active plus l'intégration logging, et `event_level` n'y vaut
            # plus ERROR : sans ceci, `logger.error()` ne créerait plus d'issue.
            # `level=None` : pas de « Sentry logs », qu'on n'envoyait pas en 2.x.
            LoggingIntegration(
                level=None,
                event_level=logging.ERROR,
                breadcrumb_level=logging.INFO,
            ),
        ],
        # Étiquette la commande de gestion, et tait le `shell` : une faute de
        # frappe dans une session d'audit n'est pas une erreur de production.
        "before_send": before_send,
        "before_send_span": scrub_span,
    }
