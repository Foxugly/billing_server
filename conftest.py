"""Garde-fou global de la suite : aucun test ne sort sur le réseau.

Ce fichier existe à cause d'un vrai incident. Les tests de webhook laissaient la
livraison partir : `CELERY_TASK_ALWAYS_EAGER` est actif en test, donc le
`deliver_entitlement.delay()` déclenché par le webhook s'exécutait dans le
processus de test et **postait pour de bon** sur `https://poker-api.foxugly.com`,
en production. Quatre requêtes par exécution de la suite, à chaque `pytest` local
et à chaque job CI. Rien ne le signalait : la production répondait 401 (la
signature ne collait pas, le secret local n'étant pas celui de la prod), le code
de livraison traite un échec comme une reprise à programmer, et les tests
restaient verts.

Deux conséquences valent la peine d'être retenues :

1. Seule la signature HMAC a empêché que des droits fictifs soient écrits en
   production. Ce n'était pas le garde-fou prévu pour ça, juste celui qui a tenu.
2. Un test vert ne prouve pas l'absence d'effet de bord. D'où ce fichier.

`RealNetworkCall` hérite de `BaseException` **volontairement** : le chemin de
livraison attrape `Exception` pour programmer une reprise, donc une exception
ordinaire serait avalée et le test resterait vert — précisément ce qui a masqué
l'incident.

Un test qui a besoin de simuler une réponse HTTP continue de patcher
`core.tasks.requests.post` : le patch remplace l'appel bien avant l'adaptateur,
ce garde-fou ne le gêne pas.
"""
import pytest


class RealNetworkCall(BaseException):
    """Un test a tenté une requête HTTP réelle."""


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    import requests.adapters

    def refuser(self, request, *args, **kwargs):
        raise RealNetworkCall(
            f"Appel réseau réel depuis un test : {request.method} {request.url}. "
            "Patcher `core.tasks.requests.post` (ou le point d'appel concerné) "
            "plutôt que de laisser la requête partir."
        )

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", refuser)
