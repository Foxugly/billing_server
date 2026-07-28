import os


# Sélecteur d'environnement canonique de la flotte : STATE (OPERATIONS.md §3.14).
# Volontairement PAS de DJANGO_ENV — c'est une dérive de quizonline/ical.
state = os.environ.get("STATE", "DEV").strip().upper()

if state == "PROD":
    from .prod import *  # noqa: F401,F403
elif state == "TEST":
    from .test import *  # noqa: F401,F403
else:
    from .dev import *  # noqa: F401,F403
