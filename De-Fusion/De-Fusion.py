"""De-Fusion add-in entry point: Fusion calls run(context) on load and stop(context) on unload (design § 3.1)."""
import logging
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "lib")


def run(context):
    for path in (_HERE, _LIB):  # lib first, add-in folder ahead of it, both ahead of everything else
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)
    log = logging.getLogger("defusion")
    try:
        from defusion import config, log as dlog

        dlog.setup(config.CONFIG_DIR, "INFO")
        try:
            from defusion import server
        except ImportError:
            log.error("Dependencies missing in %s; run install.py", _LIB, exc_info=True)
            return
        server.start()
        try:
            from defusion import setup_ui

            setup_ui.add()
        except Exception:
            log.exception("De-Fusion Setup button failed")
    except Exception:
        log.exception("De-Fusion failed to start")


def stop(context):
    log = logging.getLogger("defusion")
    try:
        from defusion import setup_ui

        setup_ui.remove()
    except Exception:
        log.exception("De-Fusion Setup button removal failed")
    try:
        from defusion import server

        server.stop()
    except Exception:
        log.exception("De-Fusion failed to stop")
