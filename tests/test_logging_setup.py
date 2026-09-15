import logging
from datetime import date

from mqs.logging_setup import (
    LOGGER_NAME,
    MASK,
    SecretRegistry,
    mask_secrets,
    setup_logging,
)


def test_mask_secrets():
    assert mask_secrets("login with Tajne123 ok", ["Tajne123"]) == f"login with {MASK} ok"
    assert mask_secrets('/user set admin password="a b" x=1', []) == \
        f"/user set admin password={MASK} x=1"
    assert mask_secrets("password=plain next", []) == f"password={MASK} next"


def test_registry():
    registry = SecretRegistry()
    registry.add("")
    registry.add("abc")
    registry.add("abcdef")
    assert list(registry) == ["abcdef", "abc"]  # longest first
    registry.clear()
    assert list(registry) == []


def test_setup_logging_writes_masked_file(tmp_path):
    registry = SecretRegistry()
    registry.add("S3cret!")
    path = setup_logging(registry, tmp_path / "logs", today=date(2026, 9, 15))
    assert path.name == "mqs-20260915.log"
    logger = logging.getLogger(LOGGER_NAME + ".test")
    logger.info("connecting with %s", "S3cret!")
    logger.info("/interface pppoe-client add password=abc user=u")
    # Calling setup again replaces the file handler instead of duplicating it.
    setup_logging(registry, tmp_path / "logs", today=date(2026, 9, 15))
    logger.warning("second")
    for handler in logging.getLogger(LOGGER_NAME).handlers:
        handler.flush()
    text = path.read_text(encoding="utf-8")
    assert "S3cret!" not in text and "password=abc" not in text
    assert text.count("second") == 1
    assert "mqs.test: connecting with ********" in text
    for handler in list(logging.getLogger(LOGGER_NAME).handlers):
        logging.getLogger(LOGGER_NAME).removeHandler(handler)
        handler.close()
