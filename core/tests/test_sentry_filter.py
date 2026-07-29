"""Le filtre Sentry : etiqueter la commande, et taire le shell.

Sur les huit issues de la revue du 2026-07-29, quatre etaient des fautes de
frappe dans des `manage.py shell` d'audit. Une boite qui melange ca avec de
vraies erreurs de production cesse d'etre un signal.
"""
from config.sentry_filter import before_send, django_command


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
