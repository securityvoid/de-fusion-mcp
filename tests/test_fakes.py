import threading
import time

import adsk
import pytest
from adsk import core, fusion


def test_thread_recording(fake_app, fake_design):
    core.reset_calls()
    fake_app.activeDocument.products.itemByProductType("DesignProductType")
    assert core.calling_threads() == {threading.get_ident()}
    seen = []
    t = threading.Thread(target=lambda: (fake_design.rootComponent, seen.append(threading.get_ident())))
    t.start()
    t.join()
    assert seen[0] in core.calling_threads()


def test_units_manager(fake_design):
    um = fake_design.unitsManager
    assert um.evaluateExpression("1 in", "cm") == pytest.approx(2.54)
    assert um.evaluateExpression("5", "cm") == pytest.approx(0.5)  # default mm
    assert um.evaluateExpression("90 deg", "rad") == pytest.approx(3.14159265 / 2)
    assert um.isValidExpression("2 ft", "cm")
    assert not um.isValidExpression("3 furlong", "cm")
    assert not um.isValidExpression("5 deg", "cm")
    fake_design.add_parameter("d1", 2.0)
    assert um.evaluateExpression("d1*2", "cm") == pytest.approx(4.0)
    assert not um.isValidExpression("nope*2", "cm")
    assert um.evaluateExpression("3*2", "") == 6.0


def test_text_command_recorded_and_raising(fake_app):
    fake_app.executeTextCommand("A")
    assert fake_app.commands == ["A"]
    fake_app.text_command_errors["BAD"] = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        fake_app.executeTextCommand("a BAD b")
    assert fake_app.commands[-1] == "a BAD b"


def test_custom_event_delivered_on_main_thread(fake_app):
    got = []

    class H(core.CustomEventHandler):
        def notify(self, args):
            got.append((args.eventId, threading.get_ident()))

    fake_app.registerCustomEvent("e").add(H())
    fake_app.fireCustomEvent("e")
    deadline = time.time() + 2
    while not got and time.time() < deadline:
        time.sleep(0.01)
    assert got == [("e", fake_app.main_thread_id)]
    assert fake_app.main_thread_id != threading.get_ident()


def test_drop_next_fires(fake_app):
    got = []

    class H(core.CustomEventHandler):
        def notify(self, args):
            got.append(1)

    fake_app.registerCustomEvent("e").add(H())
    fake_app.drop_next_fires(1)
    assert fake_app.fireCustomEvent("e") is False
    time.sleep(0.1)
    assert got == []
    fake_app.fireCustomEvent("e")
    time.sleep(0.1)
    assert got == [1]


def test_doevents_delivers_inline(fake_app):
    fake_app.stop_main_loop()
    got = []

    class H(core.CustomEventHandler):
        def notify(self, args):
            got.append(threading.get_ident())

    fake_app.registerCustomEvent("e").add(H())
    fake_app.fireCustomEvent("e")
    adsk.doEvents()
    assert got == [threading.get_ident()]


