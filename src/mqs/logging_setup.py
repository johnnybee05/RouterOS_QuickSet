"""Application logging: rolling file in %LOCALAPPDATA%\\MikrotikQuickSetPlus\\logs with
secret masking. The UI attaches its own handler for the rolling log window."""

from __future__ import annotations

import logging
import logging.handlers
import re
import threading
from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path

from mqs.paths import logs_dir

LOGGER_NAME = "mqs"
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
MASK = "********"
_PASSWORD_ARG = re.compile(r'(password=)("(?:[^"\\]|\\.)*"|\S+)', re.IGNORECASE)


class SecretRegistry:
    """Secrets known at runtime (device, PPPoE, admin passwords) to be masked in logs."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def add(self, secret: str) -> None:
        if secret:
            with self._lock:
                self._secrets.add(secret)

    def clear(self) -> None:
        with self._lock:
            self._secrets.clear()

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(sorted(self._secrets, key=len, reverse=True))


def mask_secrets(text: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, MASK)
    return _PASSWORD_ARG.sub(lambda m: m.group(1) + MASK, text)


class SecretFilter(logging.Filter):
    def __init__(self, registry: SecretRegistry) -> None:
        super().__init__()
        self.registry = registry

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        masked = mask_secrets(message, self.registry)
        if masked != message:
            record.msg, record.args = masked, None
        return True


def setup_logging(
    registry: SecretRegistry,
    directory: Path | None = None,
    level: int = logging.INFO,
    today: date | None = None,
) -> Path:
    directory = directory or logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"mqs-{(today or date.today()):%Y%m%d}.log"

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    for handler in list(logger.handlers):
        if getattr(handler, "_mqs_file", False):
            logger.removeHandler(handler)
            handler.close()
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=5 * 1024 * 1024,
                                                   backupCount=5, encoding="utf-8")
    handler._mqs_file = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(SecretFilter(registry))
    logger.addHandler(handler)
    return path
