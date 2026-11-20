# Le bind par défaut DOIT être le port assigné au site (§3.4) : ne jamais laisser
# un défaut qui entrerait en collision avec un autre site de la flotte.
bind = "127.0.0.1:8007"
workers = 3
timeout = 60
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
loglevel = "info"
# gunicorn >= 25.1 ouvre par défaut un socket de contrôle (gunicornc) dans
# $XDG_RUNTIME_DIR ou ~/.gunicorn/gunicorn.ctl : tous les sites tournant sous
# l'utilisateur django se disputeraient le même chemin. On ne s'en sert pas.
control_socket_disable = True
