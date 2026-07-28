from django.conf import settings


def test_sentry_is_not_initialised_outside_prod():
    """En TEST/DEV, aucun DSN ne doit être requis ni aucun événement envoyé."""
    assert settings.STATE != "PROD"
    assert settings.SENTRY_DSN == ""
