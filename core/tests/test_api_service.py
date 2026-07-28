from unittest.mock import MagicMock, patch

import pytest

from core.models import App, AppCustomer, Entitlement, Plan


@pytest.fixture
def plan(app):
    return Plan.objects.create(
        app=app, code="team1", name="1 équipe", quotas={"teams": 1}, sort_order=1
    )


@pytest.fixture
def stripe_keys(settings):
    settings.STRIPE_TEST_SECRET_KEY = "sk_test_factice"
    settings.STRIPE_LIVE_MODE = False
    return settings


# --- Catalogue ------------------------------------------------------------------


@pytest.mark.django_db
def test_a_plan_without_any_price_is_excluded_from_the_catalogue(app, plan, signed_get):
    """Mieux vaut ne rien afficher qu'un bouton d'achat qui échoue."""
    response = signed_get("/api/v1/plans/", app)

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.django_db
def test_the_catalogue_only_exposes_public_and_active_plans(app, plan, signed_get):
    Plan.objects.create(app=app, code="secret", name="Privé", public=False)
    Plan.objects.create(app=app, code="retire", name="Retiré", active=False)

    response = signed_get("/api/v1/plans/", app)

    assert [p["code"] for p in response.json()] == []


@pytest.mark.django_db
def test_an_app_never_sees_another_apps_catalogue(app, signed_get):
    other = App.objects.create(slug="tm", name="TM", base_url="https://tm-api.foxugly.com")
    Plan.objects.create(app=other, code="club", name="Club")

    response = signed_get("/api/v1/plans/", app)

    assert response.json() == []


# --- Droits ---------------------------------------------------------------------


@pytest.mark.django_db
def test_the_entitlement_pull_returns_the_current_state(app, signed_get):
    Entitlement.objects.create(app=app, external_user_id="42", is_paid=True, status="active")

    response = signed_get("/api/v1/entitlements/poker/42/", app)

    assert response.status_code == 200
    assert response.json()["is_paid"] is True


@pytest.mark.django_db
def test_the_pull_creates_a_missing_entitlement_rather_than_404(app, signed_get):
    """Le SPA revient de Stripe et interroge : il doit obtenir un état, pas une erreur."""
    response = signed_get("/api/v1/entitlements/poker/inconnu/", app)

    assert response.status_code == 200
    assert response.json()["is_paid"] is False


@pytest.mark.django_db
def test_an_app_cannot_read_another_apps_entitlement(app, signed_get):
    App.objects.create(slug="tm", name="TM", base_url="https://tm-api.foxugly.com")

    response = signed_get("/api/v1/entitlements/tm/42/", app)

    assert response.status_code == 403


# --- Checkout -------------------------------------------------------------------


@pytest.mark.django_db
def test_checkout_returns_503_while_stripe_is_unconfigured(app, plan, signed_post, settings):
    settings.STRIPE_TEST_SECRET_KEY = ""
    settings.STRIPE_LIVE_SECRET_KEY = ""

    response = signed_post(
        "/api/v1/checkout/",
        {"external_user_id": "42", "plan": "team1", "interval": "monthly"},
        app,
    )

    assert response.status_code == 503


@pytest.mark.django_db
def test_checkout_refuses_an_unknown_plan(app, signed_post, stripe_keys):
    response = signed_post(
        "/api/v1/checkout/",
        {"external_user_id": "42", "plan": "inexistant", "interval": "monthly"},
        app,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "unknown_plan"


@pytest.mark.django_db
def test_checkout_refuses_an_interval_without_a_configured_price(app, plan, signed_post, stripe_keys):
    response = signed_post(
        "/api/v1/checkout/",
        {"external_user_id": "42", "plan": "team1", "interval": "yearly"},
        app,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "unknown_interval"


@pytest.mark.django_db
def test_checkout_refuses_a_return_url_on_a_foreign_domain(app, plan, signed_post, stripe_keys):
    """Sans ce contrôle, le service serait un redirecteur ouvert derrière Stripe."""
    price = MagicMock(id="price_123")
    with patch.object(Plan, "price_for", return_value=price):
        response = signed_post(
            "/api/v1/checkout/",
            {
                "external_user_id": "42",
                "plan": "team1",
                "interval": "monthly",
                "success_url": "https://attaquant.example/ok",
                "cancel_url": "https://poker.foxugly.com/ko",
            },
            app,
        )

    assert response.status_code == 400
    assert response.json()["code"] == "bad_return_url"


@pytest.mark.django_db
def test_checkout_creates_the_session_with_tax_and_identifying_metadata(
    app, plan, signed_post, stripe_keys
):
    price = MagicMock(id="price_123")
    fake_stripe = MagicMock()
    fake_stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/c/x")

    with patch.object(Plan, "price_for", return_value=price), patch(
        "core.api_views.stripe_client", return_value=fake_stripe
    ):
        response = signed_post(
            "/api/v1/checkout/",
            {
                "external_user_id": "42",
                "email": "alice@x.be",
                "plan": "team1",
                "interval": "monthly",
                "success_url": "https://poker.foxugly.com/teams?billing=success",
                "cancel_url": "https://poker.foxugly.com/teams?billing=cancel",
            },
            app,
        )

    assert response.status_code == 200
    assert response.json()["url"].startswith("https://checkout.stripe.com/")

    params = fake_stripe.checkout.Session.create.call_args.kwargs
    # La quantite est reportee dans les metadata : la reconciliation doit pouvoir
    # la retrouver sans reinterroger Stripe.
    assert params["metadata"] == {
        "app": "poker", "external_user_id": "42", "plan": "team1", "quantity": "1",
    }
    assert params["line_items"][0]["quantity"] == 1
    assert params["client_reference_id"] == "poker:42"
    assert params["automatic_tax"] == {"enabled": True}
    assert params["tax_id_collection"] == {"enabled": True}
    assert AppCustomer.objects.filter(app=app, external_user_id="42").exists()


@pytest.mark.django_db
def test_checkout_reuses_an_existing_app_customer(app, plan, signed_post, stripe_keys):
    AppCustomer.objects.create(app=app, external_user_id="42", email="ancienne@x.be")
    price = MagicMock(id="price_123")
    fake_stripe = MagicMock()
    fake_stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/c/x")

    with patch.object(Plan, "price_for", return_value=price), patch(
        "core.api_views.stripe_client", return_value=fake_stripe
    ):
        signed_post(
            "/api/v1/checkout/",
            {
                "external_user_id": "42",
                "email": "nouvelle@x.be",
                "plan": "team1",
                "interval": "monthly",
                "success_url": "https://poker.foxugly.com/ok",
                "cancel_url": "https://poker.foxugly.com/ko",
            },
            app,
        )

    assert AppCustomer.objects.filter(app=app, external_user_id="42").count() == 1
    assert AppCustomer.objects.get(app=app, external_user_id="42").email == "nouvelle@x.be"


# --- Portail --------------------------------------------------------------------


@pytest.mark.django_db
def test_the_portal_refuses_when_there_is_no_stripe_customer(app, signed_post, stripe_keys):
    response = signed_post(
        "/api/v1/portal/",
        {"external_user_id": "42", "return_url": "https://poker.foxugly.com/teams"},
        app,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "no_customer"


@pytest.mark.django_db
def test_the_portal_returns_a_session_url(app, signed_post, stripe_keys):
    Entitlement.objects.create(app=app, external_user_id="42", stripe_customer_id="cus_123")
    fake_stripe = MagicMock()
    fake_stripe.billing_portal.Session.create.return_value = MagicMock(
        url="https://billing.stripe.com/p/session/x"
    )

    with patch("core.api_views.stripe_client", return_value=fake_stripe):
        response = signed_post(
            "/api/v1/portal/",
            {"external_user_id": "42", "return_url": "https://poker.foxugly.com/teams"},
            app,
        )

    assert response.status_code == 200
    assert fake_stripe.billing_portal.Session.create.call_args.kwargs["customer"] == "cus_123"


# --- Historique -----------------------------------------------------------------


@pytest.mark.django_db
def test_history_is_empty_for_a_user_who_never_paid(app, signed_get):
    response = signed_get("/api/v1/history/?external_user_id=42", app)

    assert response.status_code == 200
    assert response.json() == {"subscriptions": [], "invoices": []}


@pytest.mark.django_db
def test_checkout_forwards_the_requested_quantity(app, plan, signed_post, stripe_keys):
    """Sans elle, un client qui paie pour cinq applications n'en obtiendrait qu'une."""
    price = MagicMock(id="price_123")
    fake_stripe = MagicMock()
    fake_stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/c/x")

    with patch.object(Plan, "price_for", return_value=price), patch(
        "core.api_views.stripe_client", return_value=fake_stripe
    ):
        signed_post(
            "/api/v1/checkout/",
            {
                "external_user_id": "42", "plan": "team1", "interval": "monthly", "quantity": 5,
                "success_url": "https://poker.foxugly.com/ok",
                "cancel_url": "https://poker.foxugly.com/ko",
            },
            app,
        )

    params = fake_stripe.checkout.Session.create.call_args.kwargs
    assert params["line_items"][0]["quantity"] == 5
    assert params["metadata"]["quantity"] == "5"


@pytest.mark.django_db
def test_the_catalogue_announces_the_trial_so_an_app_can_display_it(app, signed_get):
    """Sans ce champ, une app consommatrice ne peut pas annoncer « 1er mois
    offert » avant l'achat — elle le devinerait ou le tairait."""
    from djstripe.models import Price, Product

    produit = Product.objects.create(id="prod_app", name="Par application")
    # En dj-stripe 2.11 `unit_amount` et `currency` sont lus depuis stripe_data,
    # pas depuis des colonnes : les passer en kwargs leverait un AttributeError.
    prix = Price.objects.create(
        id="price_app_m", active=True, currency="eur", product=produit,
        stripe_data={"id": "price_app_m", "unit_amount": 200, "currency": "eur"},
    )
    Plan.objects.create(
        app=app, code="app", name="Par application", trial_days=30, price_monthly=prix
    )

    payload = signed_get("/api/v1/plans/", app).json()

    assert [p["code"] for p in payload] == ["app"]
    assert payload[0]["trial_days"] == 30
    assert payload[0]["prices"]["monthly"] == {"id": "price_app_m", "amount": 200, "currency": "EUR"}


@pytest.mark.django_db
def test_the_history_says_what_was_subscribed_not_just_that_something_was(app, signed_get):
    """Une ligne d'historique sans plan ni date ni quantité n'apprend rien : la
    page consommatrice n'afficherait qu'une colonne de tirets."""
    from djstripe.models import Customer, Price, Product, Subscription

    produit = Product.objects.create(id="prod_h", name="Par application")
    prix = Price.objects.create(
        id="price_h_m", active=True, currency="eur", product=produit,
        stripe_data={"id": "price_h_m", "unit_amount": 200, "currency": "eur"},
    )
    plan = Plan.objects.create(app=app, code="app", name="Par application", price_monthly=prix)
    client_stripe = Customer.objects.create(id="cus_h", livemode=False)
    AppCustomer.objects.create(app=app, external_user_id="42", customer=client_stripe)
    # C'est l'entitlement qui porte le `cus_…`, ecrit par le webhook : la FK
    # AppCustomer.customer ne contient qu'un identifiant interne au miroir.
    Entitlement.objects.create(app=app, external_user_id="42", stripe_customer_id="cus_h")
    # `status` est une propriete lue de stripe_data en dj-stripe 2.11, pas une
    # colonne : la passer en kwarg leverait un AttributeError.
    Subscription.objects.create(
        id="sub_h", customer=client_stripe, livemode=False,
        stripe_data={
            "id": "sub_h",
            "status": "active",
            "start_date": 1_700_000_000,
            "items": {"data": [{
                "quantity": 3,
                "price": {"id": "price_h_m"},
                "current_period_end": 1_800_000_000,
            }]},
        },
    )

    ligne = signed_get(f"/api/v1/history/?external_user_id=42", app).json()["subscriptions"][0]

    assert ligne["plan"] == plan.code
    assert ligne["plan_name"] == "Par application"
    assert ligne["interval"] == "monthly"
    assert ligne["quantity"] == 3
    assert ligne["started_at"] is not None
    assert ligne["current_period_end"] is not None


@pytest.mark.django_db
def test_checkout_refuses_a_second_subscription_for_the_same_customer(
    app, plan, signed_post, stripe_keys
):
    """Sans ce refus, un client déjà abonné qui repasse par Checkout obtiendrait
    un SECOND abonnement Stripe : deux prélèvements, et un droit qui oscille
    entre les deux au gré des webhooks."""
    Entitlement.objects.create(app=app, external_user_id="42", stripe_customer_id="cus_deja")
    price = MagicMock(id="price_123")
    stripe = MagicMock()
    stripe.Subscription.list.return_value = {"data": [{"id": "sub_1", "status": "active"}]}

    with patch.object(Plan, "price_for", return_value=price), patch(
        "core.api_views.stripe_client", return_value=stripe
    ):
        response = signed_post(
            "/api/v1/checkout/",
            {
                "external_user_id": "42",
                "plan": "team1",
                "interval": "monthly",
                "success_url": "https://poker.foxugly.com/ok",
                "cancel_url": "https://poker.foxugly.com/ko",
            },
            app,
        )

    assert response.status_code == 409
    assert response.json()["code"] == "already_subscribed"
    stripe.checkout.Session.create.assert_not_called()


@pytest.mark.django_db
def test_checkout_proceeds_when_the_previous_subscription_is_over(
    app, plan, signed_post, stripe_keys
):
    """Un client qui a résilié doit pouvoir se réabonner : le refus porte sur un
    abonnement vivant, pas sur le fait d'avoir déjà été client."""
    Entitlement.objects.create(app=app, external_user_id="42", stripe_customer_id="cus_ancien")
    price = MagicMock(id="price_123")
    stripe = MagicMock()
    stripe.Subscription.list.return_value = {"data": [{"id": "sub_1", "status": "canceled"}]}
    stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/c/x")

    with patch.object(Plan, "price_for", return_value=price), patch(
        "core.api_views.stripe_client", return_value=stripe
    ):
        response = signed_post(
            "/api/v1/checkout/",
            {
                "external_user_id": "42",
                "plan": "team1",
                "interval": "monthly",
                "success_url": "https://poker.foxugly.com/ok",
                "cancel_url": "https://poker.foxugly.com/ko",
            },
            app,
        )

    assert response.status_code == 200
    stripe.checkout.Session.create.assert_called_once()


@pytest.mark.django_db
def test_checkout_reuses_the_known_stripe_customer(app, plan, signed_post, stripe_keys):
    """Sans ça Stripe crée une fiche client à chaque paiement : les factures se
    dispersent entre plusieurs fiches et le portail n'en montre qu'une."""
    Entitlement.objects.create(app=app, external_user_id="42", stripe_customer_id="cus_connu")
    price = MagicMock(id="price_123")
    stripe = MagicMock()
    stripe.Subscription.list.return_value = {"data": []}
    stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/c/x")

    with patch.object(Plan, "price_for", return_value=price), patch(
        "core.api_views.stripe_client", return_value=stripe
    ):
        signed_post(
            "/api/v1/checkout/",
            {
                "external_user_id": "42",
                "plan": "team1",
                "interval": "monthly",
                "success_url": "https://poker.foxugly.com/ok",
                "cancel_url": "https://poker.foxugly.com/ko",
            },
            app,
        )

    params = stripe.checkout.Session.create.call_args.kwargs
    assert params["customer"] == "cus_connu"
    assert "customer_email" not in params


@pytest.mark.django_db
def test_a_first_checkout_identifies_the_customer_by_email(app, plan, signed_post, stripe_keys):
    """Personne n'est encore connu de Stripe : c'est le seul cas où l'on peut
    laisser Stripe créer la fiche."""
    price = MagicMock(id="price_123")
    stripe = MagicMock()
    stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/c/x")

    with patch.object(Plan, "price_for", return_value=price), patch(
        "core.api_views.stripe_client", return_value=stripe
    ):
        signed_post(
            "/api/v1/checkout/",
            {
                "external_user_id": "42",
                "email": "nouveau@example.com",
                "plan": "team1",
                "interval": "monthly",
                "success_url": "https://poker.foxugly.com/ok",
                "cancel_url": "https://poker.foxugly.com/ko",
            },
            app,
        )

    params = stripe.checkout.Session.create.call_args.kwargs
    assert params["customer_email"] == "nouveau@example.com"
    assert "customer" not in params
    stripe.Subscription.list.assert_not_called()
