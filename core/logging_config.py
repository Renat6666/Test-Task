"""Logging setup: stdout plus a rotating logs/app.log file."""

import logging
from logging.config import dictConfig
from pathlib import Path

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "app.log"

PERFORMANCE_LOGGER = "app.performance"


def setup_logging(debug: bool = False) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = "DEBUG" if debug else "INFO"

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "default",
                    "filename": str(LOG_FILE),
                    "maxBytes": 5 * 1024 * 1024,
                    "backupCount": 3,
                    "encoding": "utf-8",
                },
            },
            "loggers": {
                "app": {"handlers": ["console", "file"], "level": level, "propagate": False},
                "uvicorn.error": {"handlers": ["console", "file"], "level": level, "propagate": False},
                "uvicorn.access": {"handlers": ["console"], "level": level, "propagate": False},
            },
            "root": {"handlers": ["console"], "level": "WARNING"},
        }
    )


def get_logger(name: str) -> logging.Logger:
    """Every application logger lives under the 'app.' prefix."""
    return logging.getLogger(name if name.startswith("app") else f"app.{name}")
