import pytest

from adsk import core, fusion
from defusion import units


@pytest.fixture(autouse=True)
def _defaults():
    units.set_input_defaults("mm", "deg")


@pytest.fixture
def fake_app():
    """A fresh fake Fusion application with its main-thread loop running."""
    core.reset_calls()
    app = core.Application.reset()
    app.start_main_loop()
    yield app
    app.stop_main_loop()
    core.Application.reset()


@pytest.fixture
def fake_design(fake_app):
    """A parametric design in the active document of ``fake_app``."""
    design = fusion.Design(parametric=True)
    fake_app.activeDocument = core.Document("Doc", {"DesignProductType": design})
    return design


@pytest.fixture
def registry():
    """An isolated action registry, so core tests never import ``defusion.tools``."""
    from defusion import actions

    with actions.isolated_registry() as reg:
        yield reg


@pytest.fixture
def dispatcher(fake_app):
    """The dispatcher registered against ``fake_app`` (whose main loop is running)."""
    from defusion import dispatch

    dispatch.register()
    yield dispatch
    dispatch.fail_queued()
    dispatch.unregister()
    dispatch.clear_jobs()
