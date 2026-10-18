"""Refresh en cookie HttpOnly pour la console (opt-in via ``X-Auth-Mode: cookie``).

Même contrat que le pilote PushIT (PushIT_server #69) : cookie posé ou non selon
l'en-tête, refresh + rotation par le cookie, logout qui l'efface, contrôle de
l'Origin, et le flux « jetons dans le corps » inchangé sans l'en-tête.
"""
import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

TOKEN = "/api/v1/auth/token/"
REFRESH = "/api/v1/auth/token/refresh/"
BLACKLIST = "/api/v1/auth/token/blacklist/"
CHANGE = "/api/v1/auth/password/change/"

ORIGIN = "https://billing.foxugly.invalid"
EMAIL = "ops@example.com"
CURRENT = "ancien-Mdp-de-test-42"
NEW = "nouveau-Mdp-de-test-43"
COOKIE = "billing_refresh"
DAY = 24 * 3600  # REFRESH_TOKEN_LIFETIME par défaut de simplejwt

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_allowed_origin")]


@pytest.fixture(autouse=True)
def _clear_throttle():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def _allowed_origin():
    with override_settings(CORS_ALLOWED_ORIGINS=[ORIGIN]):
        yield


@pytest.fixture
def operateur(django_user_model):
    return django_user_model.objects.create_superuser(email=EMAIL, password=CURRENT)


def spa_client(origin=ORIGIN):
    """Client façon navigateur : en-tête d'opt-in + Origin sur chaque requête."""
    headers = {"HTTP_X_AUTH_MODE": "cookie"}
    if origin is not None:
        headers["HTTP_ORIGIN"] = origin
    client = APIClient()
    client.credentials(**headers)
    return client


def login(client, **extra):
    return client.post(TOKEN, {"email": EMAIL, "password": CURRENT, **extra}, format="json")


def is_blacklisted(token: str) -> bool:
    try:
        RefreshToken(token)
    except Exception:
        return True
    return False


# --- cookie posé / absent ---------------------------------------------------


def test_login_in_cookie_mode_sets_an_httponly_cookie_and_omits_refresh(operateur):
    resp = login(spa_client())

    assert resp.status_code == 200
    assert "refresh" not in resp.json()
    assert resp.json()["access"]
    cookie = resp.cookies[COOKIE]
    assert cookie.value
    assert cookie["httponly"]
    assert cookie["secure"]
    assert cookie["samesite"] == "Lax"
    assert cookie["path"] == "/api/v1/auth/"
    assert cookie["domain"] == ""  # host-only : l'hôte de l'API, jamais tout le domaine
    assert int(cookie["max-age"]) == DAY


def test_login_without_remember_me_sets_a_session_cookie(operateur):
    resp = login(spa_client(), remember_me=False)

    assert resp.status_code == 200
    assert resp.cookies[COOKIE]["max-age"] == ""


def test_a_session_cookie_stays_a_session_cookie_after_rotation(operateur):
    client = spa_client()
    login(client, remember_me=False)

    resp = client.post(REFRESH, {}, format="json")

    assert resp.status_code == 200
    assert resp.cookies[COOKIE]["max-age"] == ""


def test_login_without_the_header_keeps_body_tokens_and_sets_no_cookie(operateur):
    resp = login(APIClient())

    assert resp.status_code == 200
    assert resp.json()["refresh"]
    assert COOKIE not in resp.cookies


def test_a_non_staff_account_is_still_refused_in_cookie_mode(django_user_model):
    django_user_model.objects.create_user(email=EMAIL, password=CURRENT)

    resp = login(spa_client())

    assert resp.status_code == 400
    assert COOKIE not in resp.cookies


# --- refresh par le cookie --------------------------------------------------


def test_refresh_reads_the_cookie_and_rotates_it(operateur):
    client = spa_client()
    original = login(client).cookies[COOKIE].value

    resp = client.post(REFRESH, {}, format="json")

    assert resp.status_code == 200
    assert resp.json()["access"]
    assert "refresh" not in resp.json()
    rotated = resp.cookies[COOKIE].value
    assert rotated and rotated != original
    assert int(resp.cookies[COOKIE]["max-age"]) == DAY
    assert is_blacklisted(original)


def test_replaying_a_rotated_cookie_is_rejected(operateur):
    client = spa_client()
    original = login(client).cookies[COOKIE].value
    client.post(REFRESH, {}, format="json")

    replay = spa_client()
    replay.cookies[COOKIE] = original

    assert replay.post(REFRESH, {}, format="json").status_code == 401


def test_refresh_without_a_cookie_in_cookie_mode_is_401(operateur):
    resp = spa_client().post(REFRESH, {}, format="json")

    assert resp.status_code == 401
    assert resp.json()["code"] == "refresh_cookie_missing"


def test_a_body_token_in_cookie_mode_migrates_to_the_cookie(operateur):
    """Migration unique : une console qui garde encore un refresh en localStorage."""
    legacy = login(APIClient()).json()["refresh"]

    resp = spa_client().post(REFRESH, {"refresh": legacy}, format="json")

    assert resp.status_code == 200
    assert "refresh" not in resp.json()
    assert resp.cookies[COOKIE].value
    assert is_blacklisted(legacy)


# --- contrôle de l'Origin (CSRF) --------------------------------------------


@pytest.mark.parametrize("origin", ["https://evil.example", None])
def test_cookie_refresh_from_a_foreign_or_missing_origin_is_403(operateur, origin):
    cookie = login(spa_client()).cookies[COOKIE].value

    attacker = spa_client(origin=origin)
    attacker.cookies[COOKIE] = cookie
    resp = attacker.post(REFRESH, {}, format="json")

    assert resp.status_code == 403
    assert resp.json()["code"] == "origin_not_allowed"
    assert not is_blacklisted(cookie)


def test_cookie_logout_from_a_foreign_origin_is_403(operateur):
    cookie = login(spa_client()).cookies[COOKIE].value

    attacker = spa_client(origin="https://evil.example")
    attacker.cookies[COOKIE] = cookie
    resp = attacker.post(BLACKLIST, {}, format="json")

    assert resp.status_code == 403
    assert not is_blacklisted(cookie)


def test_cookie_login_from_a_foreign_origin_is_403(operateur):
    resp = login(spa_client(origin="https://evil.example"))

    assert resp.status_code == 403
    assert COOKIE not in resp.cookies


def test_cookie_password_change_from_a_foreign_origin_is_403(operateur):
    client = spa_client()
    access = login(client).json()["access"]
    client.credentials(
        HTTP_X_AUTH_MODE="cookie",
        HTTP_ORIGIN="https://evil.example",
        HTTP_AUTHORIZATION=f"Bearer {access}",
    )

    resp = client.post(CHANGE, {"current_password": CURRENT, "new_password": NEW}, format="json")

    assert resp.status_code == 403
    operateur.refresh_from_db()
    assert operateur.check_password(CURRENT)


# --- logout -----------------------------------------------------------------


def test_logout_blacklists_and_clears_the_cookie_without_bearer(operateur):
    client = spa_client()
    cookie = login(client).cookies[COOKIE].value

    resp = client.post(BLACKLIST, {}, format="json")

    assert resp.status_code == 204
    cleared = resp.cookies[COOKIE]
    assert cleared.value == ""
    assert cleared["path"] == "/api/v1/auth/"
    assert int(cleared["max-age"]) == 0
    assert is_blacklisted(cookie)
    assert client.post(REFRESH, {}, format="json").status_code == 401


def test_logout_in_cookie_mode_is_idempotent_without_a_cookie(operateur):
    resp = spa_client().post(BLACKLIST, {}, format="json")

    assert resp.status_code == 204
    assert resp.cookies[COOKIE].value == ""


def test_logout_in_cookie_mode_tolerates_an_already_revoked_cookie(operateur):
    client = spa_client()
    cookie = login(client).cookies[COOKIE].value
    RefreshToken(cookie).blacklist()

    assert client.post(BLACKLIST, {}, format="json").status_code == 204


# --- changement de mot de passe -----------------------------------------------


def _change_password(client, access):
    client.credentials(
        HTTP_X_AUTH_MODE="cookie", HTTP_ORIGIN=ORIGIN, HTTP_AUTHORIZATION=f"Bearer {access}"
    )
    return client.post(CHANGE, {"current_password": CURRENT, "new_password": NEW}, format="json")


def test_password_change_in_cookie_mode_resets_the_cookie(operateur):
    client = spa_client()
    first = login(client)
    old_cookie = first.cookies[COOKIE].value

    resp = _change_password(client, first.json()["access"])

    assert resp.status_code == 200
    assert resp.json()["access"]
    assert "refresh" not in resp.json()
    new_cookie = resp.cookies[COOKIE].value
    assert new_cookie and new_cookie != old_cookie
    assert int(resp.cookies[COOKIE]["max-age"]) == DAY
    assert is_blacklisted(old_cookie)
    assert not is_blacklisted(new_cookie)


def test_password_change_keeps_a_session_cookie_a_session_cookie(operateur):
    client = spa_client()
    first = login(client, remember_me=False)

    resp = _change_password(client, first.json()["access"])

    assert resp.status_code == 200
    assert resp.cookies[COOKIE]["max-age"] == ""


def test_password_change_without_the_header_still_returns_the_pair(operateur):
    client = APIClient()
    access = login(client).json()["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    resp = client.post(CHANGE, {"current_password": CURRENT, "new_password": NEW}, format="json")

    assert resp.status_code == 200
    assert resp.json()["refresh"]
    assert COOKIE not in resp.cookies


# --- flux « corps » inchangé ------------------------------------------------


def test_the_body_flow_is_unchanged(operateur):
    client = APIClient()
    refresh = login(client).json()["refresh"]

    refreshed = client.post(REFRESH, {"refresh": refresh}, format="json")
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh"] != refresh
    assert COOKIE not in refreshed.cookies

    # Sans l'en-tête, le champ reste obligatoire (400, pas de lecture du cookie).
    assert client.post(REFRESH, {}, format="json").status_code == 400
    assert client.post(BLACKLIST, {}, format="json").status_code == 400

    resp = client.post(BLACKLIST, {"refresh": refreshed.json()["refresh"]}, format="json")
    assert resp.status_code == 200
    assert COOKIE not in resp.cookies


# --- CORS ---------------------------------------------------------------------


def test_cors_preflight_allows_the_opt_in_header_with_credentials():
    resp = APIClient().options(
        REFRESH,
        HTTP_ORIGIN=ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-auth-mode",
    )

    assert resp["Access-Control-Allow-Origin"] == ORIGIN
    assert resp["Access-Control-Allow-Credentials"] == "true"
    assert "x-auth-mode" in resp["Access-Control-Allow-Headers"]


def test_cors_preflight_from_a_foreign_origin_gets_no_allow_origin():
    resp = APIClient().options(
        REFRESH,
        HTTP_ORIGIN="https://evil.example",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="x-auth-mode",
    )

    assert "Access-Control-Allow-Origin" not in resp


def test_cors_never_allows_every_origin():
    from django.conf import settings

    assert settings.CORS_ALLOW_ALL_ORIGINS is False
    assert "*" not in settings.CORS_ALLOWED_ORIGINS
