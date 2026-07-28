import pytest
from django.contrib.auth import get_user_model


User = get_user_model()


@pytest.mark.django_db
def test_create_user_keys_on_email():
    user = User.objects.create_user(email="ops@foxugly.com", password="s3cret!")

    assert user.email == "ops@foxugly.com"
    assert user.check_password("s3cret!")
    assert user.is_staff is False
    assert user.is_superuser is False


@pytest.mark.django_db
def test_create_superuser_is_staff_and_superuser():
    user = User.objects.create_superuser(email="boss@foxugly.com", password="s3cret!")

    assert user.is_staff is True
    assert user.is_superuser is True


@pytest.mark.django_db
def test_email_is_the_username_field_and_has_no_username_column():
    assert User.USERNAME_FIELD == "email"
    assert User.REQUIRED_FIELDS == []
    assert "username" not in [f.name for f in User._meta.get_fields()]


@pytest.mark.django_db
def test_email_is_unique():
    User.objects.create_user(email="dup@foxugly.com", password="x")

    with pytest.raises(Exception):
        User.objects.create_user(email="dup@foxugly.com", password="x")


@pytest.mark.django_db
def test_email_is_required():
    with pytest.raises(ValueError):
        User.objects.create_user(email="", password="x")
