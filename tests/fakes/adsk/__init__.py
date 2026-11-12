"""Minimal fake of Fusion's ``adsk`` package for unit tests (no Fusion needed)."""
from . import core, fusion, cam  # noqa: F401


def doEvents():
    """Deliver any pending custom events on the calling thread (like adsk.doEvents); each call is
    recorded in ``core.DO_EVENTS``."""
    core.DO_EVENTS.append(1)
    core.Application.get()._deliver_pending()
