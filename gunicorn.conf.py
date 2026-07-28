# Le bind par défaut DOIT être le port assigné au site (§3.4) : ne jamais laisser
# un défaut qui entrerait en collision avec un autre site de la flotte.
bind = "127.0.0.1:8007"
workers = 3
timeout = 60
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
loglevel = "info"
