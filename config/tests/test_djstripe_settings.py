import pytest
from django.apps import apps
from django.conf import settings


def test_djstripe_is_installed():
    assert apps.is_installed("djstripe")


def test_foreign_key_to_field_is_id_for_a_fresh_install():
    """Choix irréversible : "id" est la valeur recommandée hors migration historique."""
    assert settings.DJSTRIPE_FOREIGN_KEY_TO_FIELD == "id"


def test_live_mode_is_a_boolean_not_a_string():
    """Une chaîne "False" serait vraie : dj-stripe basculerait en live sans prévenir."""
    assert isinstance(settings.STRIPE_LIVE_MODE, bool)
    assert settings.STRIPE_LIVE_MODE is False


@pytest.mark.django_db
def test_djstripe_models_are_migrated():
    from djstripe.models import Customer

    assert Customer.objects.count() == 0
