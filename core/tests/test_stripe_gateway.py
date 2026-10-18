import pytest

from core.stripe_gateway import stripe_client, stripe_configured, url_is_allowed_for


@pytest.mark.django_db
def test_stripe_is_not_configured_without_keys(settings):
    settings.STRIPE_LIVE_SECRET_KEY = ""
    settings.STRIPE_TEST_SECRET_KEY = ""

    assert stripe_configured() is False
    assert stripe_client() is None


@pytest.mark.django_db
def test_a_configured_return_host_is_allowed(app):
    """Le SPA de l'app, déclaré dans `App.return_hosts`."""
    assert url_is_allowed_for(app, "https://poker.foxugly.invalid/teams?billing=success") is True


@pytest.mark.django_db
def test_the_app_api_host_is_not_implicitly_allowed(app):
    """Seuls les hôtes déclarés comptent : plus de dérivation depuis `base_url`."""
    assert url_is_allowed_for(app, "https://poker-api.foxugly.invalid/retour") is False


@pytest.mark.django_db
def test_another_fleet_subdomain_is_refused(app):
    """Audit M2 : tout `*.foxugly…` passait. Une app ne renvoie plus vers une autre,
    ni vers un sous-domaine pendouillant."""
    assert url_is_allowed_for(app, "https://pushit.foxugly.invalid/billing") is False
    assert url_is_allowed_for(app, "https://billing.foxugly.invalid/") is False
    assert url_is_allowed_for(app, "https://anything.foxugly.invalid/") is False


@pytest.mark.django_db
def test_host_matching_is_case_insensitive(app):
    assert url_is_allowed_for(app, "https://POKER.foxugly.invalid/teams") is True


@pytest.mark.django_db
def test_nothing_is_allowed_without_configured_hosts(app):
    app.return_hosts = []
    app.save()
    assert url_is_allowed_for(app, "https://poker.foxugly.invalid/teams") is False


@pytest.mark.django_db
def test_an_arbitrary_domain_is_refused(app):
    """Sans ce contrôle, le service serait un redirecteur ouvert."""
    assert url_is_allowed_for(app, "https://attaquant.example/collecte") is False


@pytest.mark.django_db
def test_a_lookalike_domain_is_refused(app):
    assert url_is_allowed_for(app, "https://poker.foxugly.invalid.attaquant.example/") is False


@pytest.mark.django_db
def test_userinfo_does_not_smuggle_a_host(app):
    assert url_is_allowed_for(app, "https://poker.foxugly.invalid@attaquant.example/") is False


@pytest.mark.django_db
def test_plain_http_is_refused(app):
    assert url_is_allowed_for(app, "http://poker.foxugly.invalid/") is False


@pytest.mark.django_db
def test_an_empty_url_is_refused(app):
    assert url_is_allowed_for(app, "") is False
    assert url_is_allowed_for(app, None) is False
