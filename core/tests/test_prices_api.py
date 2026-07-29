"""Le catalogue des prix Stripe mires, pour la page Plans de la console.

Sans lui, mapper un plan sur un `Price` demanderait de recopier un identifiant
`price_…` a la main depuis le dashboard Stripe -- et une faute de frappe y donne
un plan qui ne se vend pas, sans que rien ne le signale avant le premier achat.
"""
import pytest


@pytest.fixture
def staff_client(db, django_user_model):
    from rest_framework.test import APIClient

    operateur = django_user_model.objects.create_superuser(email="ops@example.com", password="x")
    client = APIClient()
    client.force_authenticate(operateur)
    return client


@pytest.fixture
def prix(db):
    from djstripe.models import Price, Product

    produit = Product.objects.create(id="prod_1", name="Une equipe")
    mensuel = Price.objects.create(
        id="price_m", active=True, currency="eur", product=produit,
        stripe_data={
            "id": "price_m", "unit_amount": 500, "currency": "eur",
            "recurring": {"interval": "month"}, "nickname": "Mensuel",
        },
    )
    annuel = Price.objects.create(
        id="price_y", active=True, currency="eur", product=produit,
        stripe_data={
            "id": "price_y", "unit_amount": 5000, "currency": "eur",
            "recurring": {"interval": "year"},
        },
    )
    archive = Price.objects.create(
        id="price_old", active=False, currency="eur", product=produit,
        stripe_data={"id": "price_old", "unit_amount": 400, "currency": "eur"},
    )
    return mensuel, annuel, archive


@pytest.mark.django_db
def test_the_prices_are_closed_to_anonymous(client):
    assert client.get("/api/v1/admin/prices/").status_code in (401, 403)


@pytest.mark.django_db
def test_the_catalogue_lists_what_a_plan_can_be_mapped_to(staff_client, prix):
    payload = staff_client.get("/api/v1/admin/prices/").json()
    lignes = payload if isinstance(payload, list) else payload["results"]

    par_id = {p["id"]: p for p in lignes}
    assert par_id["price_m"]["unit_amount"] == 500
    assert par_id["price_m"]["currency"] == "EUR"
    assert par_id["price_m"]["interval"] == "month"
    assert par_id["price_m"]["product_name"] == "Une equipe"
    assert par_id["price_y"]["interval"] == "year"


@pytest.mark.django_db
def test_an_archived_price_is_out_of_the_picker(staff_client, prix):
    """Le proposer produirait un plan invendable : Stripe refuse un prix archive."""
    lignes = staff_client.get("/api/v1/admin/prices/").json()
    lignes = lignes if isinstance(lignes, list) else lignes["results"]

    assert "price_old" not in [p["id"] for p in lignes]


@pytest.mark.django_db
def test_a_price_without_recurrence_says_so_rather_than_lying(staff_client, prix):
    """Un prix ponctuel n'a pas d'intervalle : mieux vaut vide qu'un « month »
    invente, qui ferait croire a un abonnement mensuel."""
    from djstripe.models import Price, Product

    produit = Product.objects.get(id="prod_1")
    Price.objects.create(
        id="price_one", active=True, currency="eur", product=produit,
        stripe_data={"id": "price_one", "unit_amount": 9900, "currency": "eur"},
    )

    lignes = staff_client.get("/api/v1/admin/prices/").json()
    lignes = lignes if isinstance(lignes, list) else lignes["results"]
    ponctuel = next(p for p in lignes if p["id"] == "price_one")

    assert ponctuel["interval"] == ""
