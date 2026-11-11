"""Minimal fake of Fusion's ``adsk`` package for unit tests (no Fusion needed)."""
from . import core, fusion, cam  # noqa: F401


def doEvents():
    """Deliver any pending custom events on the calling thread (like adsk.doEvents)."""
    core.Application.get()._deliver_pending()
