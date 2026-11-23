"""Le filtre Sentry : etiqueter la commande, et taire le shell.

Sur les huit issues de la revue du 2026-07-29, quatre etaient des fautes de
frappe dans des `manage.py shell` d'audit. Une boite qui melange ca avec de
vraies erreurs de production cesse d'etre un signal.
"""
import warnings

import sentry_sdk

from config.sentry_filter import (
    DATA_COLLECTION,
    before_send,
    django_command,
    scrub_event,
)


def test_a_web_process_carries_no_command_tag():
    """gunicorn n'est pas une commande de gestion : rien a etiqueter."""
    assert django_command(["gunicorn", "config.wsgi"]) == ""


def test_a_management_command_is_identified():
    assert django_command(["manage.py", "migrate"]) == "migrate"
    assert django_command(["./manage.py", "sync_entitlements", "--app", "poker"]) == "sync_entitlements"


def test_manage_py_without_a_command_is_not_one():
    assert django_command(["manage.py"]) == ""


def test_an_event_from_the_shell_is_dropped():
    """C'est du bruit d'atelier, pas une erreur applicative."""
    assert before_send({"message": "boom"}, {}, argv=["manage.py", "shell", "-c", "..."]) is None


def test_an_event_from_a_scheduled_command_is_kept_and_tagged():
    """Une reconciliation quotidienne qui echoue DOIT alerter : on etiquette,
    on ne jette pas. C'est la difference entre du bruit et une panne."""
    evenement = before_send({"message": "boom"}, {}, argv=["manage.py", "sync_entitlements"])

    assert evenement is not None
    assert evenement["tags"]["django_command"] == "sync_entitlements"


def test_a_web_event_passes_through_untouched():
    evenement = before_send({"message": "boom"}, {}, argv=["gunicorn"])

    assert evenement is not None
    assert "django_command" not in evenement.get("tags", {})


def test_existing_tags_are_preserved():
    evenement = before_send(
        {"message": "boom", "tags": {"app": "billing"}}, {}, argv=["manage.py", "migrate"]
    )

    assert evenement["tags"] == {"app": "billing", "django_command": "migrate"}


def _filtre(valeur):
    # config.sentry_scrub substitue "[Filtered]" (le scrubber de 2.x, un AnnotatedValue).
    return getattr(valeur, "value", valeur) == "[Filtered]"


def test_data_collection_ne_collecte_aucune_pii_et_sans_avertissement():
    """`data_collection` (3.0 a supprimé `send_default_pii`), sans rien collecter de plus."""
    with warnings.catch_warnings(record=True) as avertissements:
        warnings.simplefilter("always")
        client = sentry_sdk.Client(
            dsn=None, default_integrations=False, data_collection=DATA_COLLECTION
        )
    assert not any("send_default_pii" in str(a.message) for a in avertissements)
    regles = client.options["data_collection"]
    assert regles["user_info"] is False
    assert regles["cookies"] == {"mode": "off"}
    assert regles["http_bodies"] == []
    assert regles["queues"] is False
    assert regles["database_query_data"] is False
    assert regles["mcp"] == {"inputs": False, "outputs": False}


def test_le_scrubber_du_sdk_reste_applique():
    """sentry-sdk 3.0 a supprimé l'EventScrubber : `scrub_event` le remplace."""
    evenement = scrub_event(
        {
            "extra": {"password": "hunter2", "page": 3},
            "breadcrumbs": {"values": [{"data": {"token": "abc", "n": 1}}]},
            "request": {"headers": {"Authorization": "Bearer x", "Accept": "*/*"}},
            "user": {"id": "7", "ip_address": "203.0.113.9"},
        },
        {},
    )
    assert _filtre(evenement["extra"]["password"]) and evenement["extra"]["page"] == 3
    miette = evenement["breadcrumbs"]["values"][0]["data"]
    assert _filtre(miette["token"]) and miette["n"] == 1
    entetes = evenement["request"]["headers"]
    assert _filtre(entetes["Authorization"]) and entetes["Accept"] == "*/*"
    assert "ip_address" not in evenement["user"]


def test_before_send_nettoie_avant_de_filtrer():
    evenement = before_send({"extra": {"secret": "s"}}, {}, argv=["gunicorn"])
    assert _filtre(evenement["extra"]["secret"])
    assert before_send({"extra": {}}, {}, argv=["manage.py", "shell"]) is None
