"""File logging (design § 3.6)."""
import logging
import logging.handlers
from pathlib import Path

LOG_NAME = "defusion"
_MARK = "_defusion_file_handler"


def setup(config_dir, level="INFO"):
    """Log to ``<config_dir>/defusion.log`` (1 MiB x 3). Replaces any handler this function added earlier."""
    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOG_NAME)
    for h in list(logger.handlers):
        if getattr(h, _MARK, False):
            logger.removeHandler(h)
            h.close()
    handler = logging.handlers.RotatingFileHandler(
        config_dir / "defusion.log", maxBytes=1048576, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    setattr(handler, _MARK, True)
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return handler
