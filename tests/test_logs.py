import logging

from tamra.logs import setup_logging, shutdown_logging


def flush():
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_logs_go_to_a_file(tmp_path):
    try:
        path = setup_logging(tmp_path / "logs")
        logging.getLogger("tamra.test").info("hello log")
        flush()
        assert "hello log" in path.read_text(encoding="utf-8")
        assert logging.getLogger("uvicorn.access").level == logging.WARNING
    finally:
        shutdown_logging()


def test_setting_up_again_does_not_duplicate_lines(tmp_path):
    try:
        setup_logging(tmp_path / "a")
        path = setup_logging(tmp_path / "b")
        logging.getLogger("tamra.test").info("only once")
        flush()
        assert path.read_text(encoding="utf-8").count("only once") == 1
    finally:
        shutdown_logging()
