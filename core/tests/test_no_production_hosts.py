"""Aucun hote de production dans les fixtures de test.

Ce garde-fou existe parce que la suite a reellement poste sur
`poker-api.<domaine de prod>` pendant des semaines : les tests de webhook
laissaient partir la livraison, et le `base_url` des fixtures pointait sur la
production. Seule la signature HMAC a empeche l'ecriture de droits fictifs.

Le `conftest.py` de la racine bloque desormais tout appel reseau reel, ce qui
suffit. Ce test-ci s'attaque a la cause plutot qu'a l'effet : avec des hotes non
routables (`.invalid`, reserve par la RFC 2606), meme une requete qui echapperait
au garde-fou n'atteindrait rien.
"""
from pathlib import Path

import pytest

# Construit en deux morceaux : ecrit d'une piece, ce fichier se signalerait
# lui-meme a chaque execution.
DOMAINE_DE_PROD = "foxugly" + ".com"

RACINE = Path(__file__).resolve().parents[2]
DOSSIERS_DE_TEST = ("core/tests", "config/tests", "accounts/tests")


def _fichiers_de_test():
    for dossier in DOSSIERS_DE_TEST:
        yield from sorted((RACINE / dossier).glob("*.py"))


@pytest.mark.parametrize("fichier", list(_fichiers_de_test()), ids=lambda f: f.name)
def test_no_test_file_points_at_a_production_host(fichier):
    contenu = fichier.read_text(encoding="utf-8")

    assert DOMAINE_DE_PROD not in contenu, (
        f"{fichier.relative_to(RACINE)} contient un hote de production. "
        "Utiliser un domaine non routable (.invalid) : une fixture qui porte "
        "l'adresse de la prod finit un jour par etre appelee pour de vrai."
    )
