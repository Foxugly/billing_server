import pytest


@pytest.mark.django_db
def test_health_returns_ok_with_database(client):
    response = client.get("/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_is_not_behind_authentication(client, db):
    """UptimeRobot appelle l'endpoint sans credentials (§3.9)."""
    assert client.get("/health/").status_code == 200
