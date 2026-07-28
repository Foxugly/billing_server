"""Ces gardes existent à cause d'un vrai incident : REDIS_URL absent en prod
faisait retomber Celery sur un broker mémoire et le cache sur LocMem, sans le
moindre message d'erreur. Le worker tournait dans le vide, tout paraissait vert.
"""
from core.checks import redis_is_configured_in_production


def test_nothing_is_reported_outside_production(settings):
    settings.STATE = "DEV"
    settings.REDIS_URL = ""

    assert redis_is_configured_in_production(None) == []


def test_a_missing_redis_url_fails_the_check_in_production(settings):
    settings.STATE = "PROD"
    settings.REDIS_URL = ""

    ids = [e.id for e in redis_is_configured_in_production(None)]

    assert "billing.E001" in ids


def test_eager_tasks_fail_the_check_in_production(settings):
    settings.STATE = "PROD"
    settings.REDIS_URL = "redis://127.0.0.1:6379/4"
    settings.CELERY_TASK_ALWAYS_EAGER = True

    ids = [e.id for e in redis_is_configured_in_production(None)]

    assert "billing.E002" in ids


def test_locmem_cache_fails_the_check_in_production(settings):
    settings.STATE = "PROD"
    settings.REDIS_URL = "redis://127.0.0.1:6379/4"
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

    ids = [e.id for e in redis_is_configured_in_production(None)]

    assert "billing.E003" in ids


def test_a_correctly_configured_production_reports_nothing(settings):
    settings.STATE = "PROD"
    settings.REDIS_URL = "redis://127.0.0.1:6379/4"
    settings.CELERY_TASK_ALWAYS_EAGER = False
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": "redis://127.0.0.1:6379/5",
        }
    }

    assert redis_is_configured_in_production(None) == []
