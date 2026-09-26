import logging

import logging_setup


def test_setup_logging_creates_file_handler_once_per_clean_logger(tmp_path, monkeypatch):
    root = logging.getLogger()
    old_handlers = root.handlers[:]
    root.handlers.clear()
    try:
        monkeypatch.setattr(logging_setup, "LOG_FILE", tmp_path / "app.log")
        monkeypatch.setattr(logging_setup, "ensure_app_dirs", lambda: None)
        logging_setup.setup_logging(debug=True)
        assert (tmp_path / "app.log").exists()
        assert any(isinstance(handler, logging.StreamHandler) for handler in root.handlers)
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers[:] = old_handlers
