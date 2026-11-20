"""gunicorn.conf.py : réglages vérifiés par le chargeur de gunicorn lui-même."""

import runpy
from pathlib import Path

import pytest

# gunicorn.config importe grp (Unix seulement) : le test tourne en CI, pas sous Windows.
Config = pytest.importorskip("gunicorn.config", reason="gunicorn needs grp (Unix only)").Config

CONF = Path(__file__).resolve().parents[2] / "gunicorn.conf.py"


def test_le_socket_de_controle_de_gunicorn_est_desactive():
    # gunicorn >= 25.1 ouvre par défaut un socket de contrôle (~/.gunicorn/gunicorn.ctl),
    # le même chemin pour tous les sites de l'utilisateur django.
    reglages = runpy.run_path(str(CONF))
    cfg = Config()
    cfg.set("control_socket_disable", reglages["control_socket_disable"])  # nom inconnu -> erreur
    assert cfg.control_socket_disable is True
