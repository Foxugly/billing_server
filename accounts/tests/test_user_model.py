import pytest
from django.contrib.auth import get_user_model


User = get_user_model()

# Domaine de documentation (RFC 2606) et littéral explicite : un email au domaine
# de l'entreprise accolé à un "password=" déclenche les scanners de secrets, et une
# alerte qu'on apprend à ignorer ne sert plus le jour où elle est vraie.
TEST_PASSWORD = "pytest-only-not-a-real-credential"


@pytest.mark.django_db
def test_create_user_keys_on_email():
    user = User.objects.create_user(email="ops@example.com", password=TEST_PASSWORD)

    assert user.email == "ops@example.com"
    assert user.check_password(TEST_PASSWORD)
    assert user.is_staff is False
    assert user.is_superuser is False


@pytest.mark.django_db
def test_create_superuser_is_staff_and_superuser():
    user = User.objects.create_superuser(email="boss@example.com", password=TEST_PASSWORD)

    assert user.is_staff is True
    assert user.is_superuser is True


@pytest.mark.django_db
def test_email_is_the_username_field_and_has_no_username_column():
    assert User.USERNAME_FIELD == "email"
    assert User.REQUIRED_FIELDS == []
    assert "username" not in [f.name for f in User._meta.get_fields()]


@pytest.mark.django_db
def test_email_is_unique():
    User.objects.create_user(email="dup@example.com", password=TEST_PASSWORD)

    with pytest.raises(Exception):
        User.objects.create_user(email="dup@example.com", password=TEST_PASSWORD)


@pytest.mark.django_db
def test_email_is_required():
    with pytest.raises(ValueError):
        User.objects.create_user(email="", password=TEST_PASSWORD)
