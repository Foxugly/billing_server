from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Importe les modules pour leurs effets de bord : enregistrement des
        # décorateurs @djstripe_receiver, et des system checks de configuration.
        from . import checks, webhooks  # noqa: F401
