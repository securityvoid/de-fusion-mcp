import logging
import logging.handlers

import pytest

from defusion import log


@pytest.fixture(autouse=True)
def _clean():
    yield
    lg = logging.getLogger("defusion")
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()
    lg.setLevel(logging.NOTSET)
    lg.propagate = True


def test_handler_is_rotating_1mib_x3(tmp_path):
    h = log.setup(tmp_path / "cfg")
    assert isinstance(h, logging.handlers.RotatingFileHandler)
    assert (h.maxBytes, h.backupCount) == (1048576, 3)
    assert h.baseFilename == str(tmp_path / "cfg" / "defusion.log")


def test_writes_lines_at_level(tmp_path):
    log.setup(tmp_path, "INFO")
    lg = logging.getLogger("defusion")
    lg.debug("hidden")
    lg.info("call tool=model action=x duration_ms=3 outcome=ok")
    text = (tmp_path / "defusion.log").read_text()
    assert "hidden" not in text and "call tool=model action=x duration_ms=3 outcome=ok" in text


def test_second_setup_replaces_handler_and_applies_level(tmp_path):
    log.setup(tmp_path, "INFO")
    log.setup(tmp_path, "DEBUG")
    lg = logging.getLogger("defusion")
    assert len([h for h in lg.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]) == 1
    lg.debug("now visible")
    lg.info("once")
    text = (tmp_path / "defusion.log").read_text()
    assert "now visible" in text and text.count("once") == 1


def test_setup_moves_to_new_dir(tmp_path):
    log.setup(tmp_path / "a")
    log.setup(tmp_path / "b")
    logging.getLogger("defusion").warning("here")
    assert "here" in (tmp_path / "b" / "defusion.log").read_text()
    assert "here" not in (tmp_path / "a" / "defusion.log").read_text()
