"""Tests for enovapower logging."""

from __future__ import annotations

import logging

import pytest

from enovapower.logger import LOGGER_NAME, configure_logging, get_logger


class TestLogger:
    def test_get_logger_returns_enovapower_logger(self):
        logger = get_logger()
        assert logger.name == LOGGER_NAME

    def test_logger_is_singleton(self):
        logger1 = get_logger()
        logger2 = get_logger()
        assert logger1 is logger2

    def test_logger_has_no_default_handlers(self):
        logger = get_logger()
        assert len(logger.handlers) == 0

    def test_logger_default_level_is_not_set(self):
        logger = get_logger()
        assert logger.level == logging.NOTSET


class TestConfigureLogging:
    @pytest.fixture(autouse=True)
    def reset_logger(self):
        import enovapower.logger

        enovapower.logger._logger = None
        yield

    def test_configure_logging_adds_handler(self):
        logger = configure_logging()
        assert len(logger.handlers) > 0

    def test_configure_logging_sets_level(self):
        logger = configure_logging(level=logging.INFO)
        assert logger.level == logging.INFO

    def test_configure_logging_custom_format(self):
        import io

        format_string = "%(levelname)s: %(message)s"
        logger = configure_logging(format_string=format_string)

        stream = io.StringIO()
        test_handler = logging.StreamHandler(stream)
        test_handler.setFormatter(logging.Formatter(format_string))
        logger.addHandler(test_handler)

        logger.info("test")
        assert "INFO: test" in stream.getvalue()


class TestLoggerWithClients:
    def test_async_client_uses_default_logger(self):
        from enovapower.async_client import AsyncEnovaClient

        client = AsyncEnovaClient()
        assert client._log is not None
        assert client._log.name == LOGGER_NAME

    def test_async_client_accepts_custom_logger(self, caplog):
        custom_logger = logging.getLogger("custom")
        custom_logger.setLevel(logging.DEBUG)

        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        custom_logger.addHandler(handler)

        from enovapower.async_client import AsyncEnovaClient

        client = AsyncEnovaClient(logger=custom_logger)
        assert client._log is custom_logger

    def test_storage_uses_default_logger(self):
        import tempfile

        from enovapower.storage import UsageStore

        with tempfile.NamedTemporaryFile(suffix=".db", delete=True) as f:
            store = UsageStore(f.name)
            assert store._log is not None
            assert store._log.name == LOGGER_NAME

    def test_storage_accepts_custom_logger(self):
        custom_logger = logging.getLogger("custom_storage")

        import tempfile

        from enovapower.storage import UsageStore

        with tempfile.NamedTemporaryFile(suffix=".db", delete=True) as f:
            store = UsageStore(f.name, logger=custom_logger)
            assert store._log is custom_logger


class TestLogMessages:
    def test_logging_works_after_configure(self, caplog):
        configure_logging(level=logging.DEBUG)

        with caplog.at_level(logging.DEBUG, LOGGER_NAME):
            logger = get_logger()
            logger.info("Test message")

        assert "Test message" in caplog.text

    def test_user_can_configure_before_import(self, caplog):
        user_logger = logging.getLogger(LOGGER_NAME)
        user_logger.setLevel(logging.DEBUG)

        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("CUSTOM: %(message)s"))
        user_logger.addHandler(handler)

        from enovapower.logger import get_logger

        logger = get_logger()

        with caplog.at_level(logging.DEBUG, LOGGER_NAME):
            logger.info("User configured message")

        assert "User configured message" in caplog.text
