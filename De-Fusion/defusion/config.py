"""Config file loading (design § 6.1). Fail closed: any error is recorded and no default fallback applies."""
import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import profiles, units

log = logging.getLogger("defusion")

DEFAULT_PORT = 8642
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
LENGTH_UNITS = ("mm", "cm", "m", "in", "ft", "um")
ANGLE_UNITS = ("deg", "rad")
DEFAULT_PROFILE = ["all", "-script"]

DEFAULT_TOML = """\
port = 8642                      # 127.0.0.1 only
# token = "..."                  # optional; see § 7
log_level = "INFO"
default_profile = ["all", "-script"]

[units]
length = "mm"                    # default for bare numbers in and for outputs
angle = "deg"

[profiles]                       # adds to / overrides the built-in profiles
# my-print = ["3dprinting", "-model.coil"]
"""


def _default_dir():
    env = os.environ.get("DEFUSION_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".defusion"


CONFIG_DIR = _default_dir()
CONFIG_PATH = CONFIG_DIR / "config.toml"


@dataclass
class Config:
    port: int = DEFAULT_PORT
    token: str | None = None
    log_level: str = "INFO"
    default_profile: list = field(default_factory=lambda: list(DEFAULT_PROFILE))
    units: dict = field(default_factory=lambda: {"length": "mm", "angle": "deg"})
    profiles: dict = field(default_factory=dict)


_state = {"config": Config(), "error": None}


def settings():
    """The effective config loaded at start() (defaults before load)."""
    return _state["config"]


def load_error():
    """The config error recorded by load(), or None."""
    return _state["error"]


def record_error(message):
    """Record a config error found after load() (``start()`` checks profile selectors against the registry)."""
    log.error("config error: %s", message)
    _state["error"] = message


def _items(value, where, errors):
    ok = isinstance(value, list) and value and all(
        isinstance(i, str) and i.lstrip("-").strip() and i == i.strip() and " " not in i for i in value
    )
    if not ok:
        errors.append("%s must be a non-empty list of profile items like \"3dprinting\" or \"-script\"" % where)
        return None
    return list(value)


def _parse(data):
    """Validate a parsed TOML table; returns (Config, [errors])."""
    errors = []
    cfg = Config()
    known = {"port", "token", "log_level", "default_profile", "units", "profiles"}
    for key in data:
        if key not in known:
            errors.append("unknown key '%s'" % key)
    if "port" in data:
        v = data["port"]
        if isinstance(v, int) and not isinstance(v, bool) and 1024 <= v <= 65535:
            cfg.port = v
        else:
            errors.append("port must be an integer 1024-65535")
    if "token" in data:
        v = data["token"]
        if isinstance(v, str) and v.strip():
            cfg.token = v
        else:
            errors.append("token must not be blank")
    if "log_level" in data:
        v = data["log_level"]
        if v in LOG_LEVELS:
            cfg.log_level = v
        else:
            errors.append("log_level must be one of %s" % " ".join(LOG_LEVELS))
    if "default_profile" in data:
        v = _items(data["default_profile"], "default_profile", errors)
        if v is not None:
            cfg.default_profile = v
    if "units" in data:
        u = data["units"]
        if not isinstance(u, dict):
            errors.append("units must be a table")
        else:
            for key in u:
                if key not in ("length", "angle"):
                    errors.append("unknown key 'units.%s'" % key)
            for key, allowed in (("length", LENGTH_UNITS), ("angle", ANGLE_UNITS)):
                if key in u:
                    if u[key] in allowed:
                        cfg.units[key] = u[key]
                    else:
                        errors.append("units.%s must be one of %s" % (key, " ".join(allowed)))
    if "profiles" in data:
        p = data["profiles"]
        if not isinstance(p, dict):
            errors.append("profiles must be a table")
        else:
            for name, items in p.items():
                v = _items(items, "profiles.%s" % name, errors)
                if v is not None:
                    cfg.profiles[name] = v
    return cfg, errors


def load():
    """Read CONFIG_PATH. Returns ``(Config, error | None)`` and records both for settings()/load_error().

    On error the Config holds port 8642, INFO, no token and the default units; callers must then
    allow only server.status and server.job_status."""
    error = None
    cfg = Config()
    try:
        raw = CONFIG_PATH.read_bytes() if CONFIG_PATH.exists() else None
    except OSError as exc:
        raw, error = None, "cannot read %s: %s" % (CONFIG_PATH, exc)
    if raw is not None:
        try:
            data = tomllib.loads(raw.decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            error = "cannot parse %s: %s" % (CONFIG_PATH, exc)
        else:
            cfg, errors = _parse(data)
            if errors:
                error = "invalid config %s: %s" % (CONFIG_PATH, "; ".join(errors))
            else:
                try:
                    merged = profiles.merge(profiles.BUILTIN, cfg.profiles)
                    profiles.expand(cfg.default_profile, merged)
                except profiles.InvalidProfile as exc:
                    error = "invalid config %s: %s" % (CONFIG_PATH, exc)
    if error:
        cfg = Config()
        log.error("config error: %s", error)
    _state["config"], _state["error"] = cfg, error
    units.set_input_defaults(cfg.units["length"], cfg.units["angle"])
    return cfg, error
