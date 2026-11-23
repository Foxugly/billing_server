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

Ce que le SDK peut collecter est fixé par `DATA_COLLECTION` (sentry-sdk 2.76,
remplace `send_default_pii=False`, devenu obsolète) : jamais plus qu'avant.
Passer `data_collection` coupe l'`EventScrubber` intégré au SDK ; `scrub_event`
le rejoue donc en tête de `before_send` et en `before_send_transaction`.
"""
import sys

from sentry_sdk.scrubber import EventScrubber

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


# Termes proxy/IP que le SDK lui-même ajoute quand il traduit
# `send_default_pii=False` ; s'ajoutent à sa liste noire de clés sensibles.
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
    "database_query_data": False,
    "queues": False,
    "stack_frame_variables": {"mode": "denylist"},
    "frame_context_lines": 5,
}

_SCRUBBER = EventScrubber(send_default_pii=False)


def scrub_event(event, hint):
    """Hook `before_send_transaction` : le nettoyage par liste noire du SDK."""
    _SCRUBBER.scrub_event(event)
    return event


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
