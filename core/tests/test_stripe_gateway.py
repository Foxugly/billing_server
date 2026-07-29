import pytest

from core.stripe_gateway import stripe_client, stripe_configured, url_is_allowed_for


@pytest.mark.django_db
def test_stripe_is_not_configured_without_keys(settings):
    settings.STRIPE_LIVE_SECRET_KEY = ""
    settings.STRIPE_TEST_SECRET_KEY = ""

    assert stripe_configured() is False
    assert stripe_client() is None


@pytest.mark.django_db
def test_the_app_own_api_host_is_allowed(app):
    assert url_is_allowed_for(app, "https://poker-api.foxugly.invalid/retour") is True


@pytest.mark.django_db
def test_a_sibling_subdomain_is_allowed(app):
    """Le SPA vit sur poker.foxugly.invalid quand l'API est sur poker-api.foxugly.invalid."""
    assert url_is_allowed_for(app, "https://poker.foxugly.invalid/teams?billing=success") is True


@pytest.mark.django_db
def test_an_arbitrary_domain_is_refused(app):
    """Sans ce contrôle, le service serait un redirecteur ouvert."""
    assert url_is_allowed_for(app, "https://attaquant.example/collecte") is False


@pytest.mark.django_db
def test_a_lookalike_domain_is_refused(app):
    assert url_is_allowed_for(app, "https://foxugly.invalid.attaquant.example/") is False


@pytest.mark.django_db
def test_plain_http_is_refused(app):
    assert url_is_allowed_for(app, "http://poker.foxugly.invalid/") is False


@pytest.mark.django_db
def test_an_empty_url_is_refused(app):
    assert url_is_allowed_for(app, "") is False
    assert url_is_allowed_for(app, None) is False
