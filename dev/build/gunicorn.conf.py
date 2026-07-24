# Copyright The IETF Trust 2026, All Rights Reserved
"""gunicorn configuration.

Command-line arguments override any settings here.
"""

control_socket_disable = True

# Log as JSON on stdout (Django logs separately on stderr).
_json = "pythonjsonlogger.jsonlogger.JsonFormatter"
logconfig_dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"level": "INFO", "handlers": ["console"]},
    "loggers": {
        "gunicorn.error": {
            "level": "INFO",
            "handlers": ["console"],
            "propagate": False,
            "qualname": "gunicorn.error",
        },
        "gunicorn.access": {
            "level": "INFO",
            "handlers": ["access_console"],
            "propagate": False,
            "qualname": "gunicorn.access",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": "ext://sys.stdout",
        },
        "access_console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": "ext://sys.stdout",
        },
    },
    "formatters": {
        "json": {
            "()": _json,
            "format": "%(asctime)s %(levelname)s %(message)s %(name)s %(process)s",
        },
    },
}
