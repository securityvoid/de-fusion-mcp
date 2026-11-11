"""Input evaluation and output conversion (design § 8). Internal units: cm, rad, kg."""
import math
import re

from .errors import INVALID_PARAMS, ActionError

CM_PER = {"mm": 0.1, "cm": 1.0, "m": 100.0, "in": 2.54, "ft": 30.48, "um": 1e-4}
RAD_PER = {"deg": math.pi / 180.0, "rad": 1.0}

_input_defaults = {"length": "mm", "angle": "deg"}


def set_input_defaults(length, angle):
    """Set the unit applied to bare input numbers (config ``[units]``, written by config.load)."""
    _input_defaults["length"] = length
    _input_defaults["angle"] = angle


def input_defaults():
    return dict(_input_defaults)


def to_expr(value, default_unit):
    """A number becomes ``"<n> <default unit>"``; a string is used as written."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActionError(INVALID_PARAMS, "expected a number or an expression string")
    n = int(value) if float(value).is_integer() and abs(value) < 1e15 else value
    return "%s %s" % (n, default_unit) if default_unit else str(n)


_SIMPLE = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z]*)\s*$")


def _table_eval(expr, table, default_unit, param):
    m = _SIMPLE.match(expr)
    if not m:
        raise ActionError(
            INVALID_PARAMS,
            "%s: cannot evaluate %r without an active design (use a number and a unit, "
            "e.g. '2 ft')" % (param, expr),
        )
    unit = m.group(2) or default_unit
    if unit not in table:
        raise ActionError(
            INVALID_PARAMS,
            "%s: unit %r is not supported here; use one of %s" % (param, unit, " ".join(table)),
        )
    return float(m.group(1)) * table[unit]


def _design_eval(expr, design, units, param):
    um = design.unitsManager
    if not um.isValidExpression(expr, units):
        raise ActionError(INVALID_PARAMS, "%s: invalid expression %r" % (param, expr))
    return um.evaluateExpression(expr, units)


def evaluate_length(expr, design, param):
    """Length expression -> cm."""
    if design is None:
        return _table_eval(expr, CM_PER, _input_defaults["length"], param)
    return _design_eval(expr, design, "cm", param)


def evaluate_angle(expr, design, param):
    """Angle expression -> radians."""
    if design is None:
        return _table_eval(expr, RAD_PER, _input_defaults["angle"], param)
    return _design_eval(expr, design, "rad", param)


def evaluate_expr(expr, design, param):
    """Unitless expression (counts, ratios) -> float."""
    if design is None:
        m = _SIMPLE.match(expr)
        if not m or m.group(2):
            raise ActionError(INVALID_PARAMS, "%s: %r is not a plain number" % (param, expr))
        return float(m.group(1))
    return _design_eval(expr, design, "", param)


class OutputUnits:
    """The call's output units with converters from internal cm / rad / kg."""

    def __init__(self, length="mm", angle="deg"):
        if length not in CM_PER:
            raise ActionError(
                INVALID_PARAMS,
                "units.length %r is not supported; use one of %s" % (length, " ".join(CM_PER)),
            )
        if angle not in RAD_PER:
            raise ActionError(
                INVALID_PARAMS,
                "units.angle %r is not supported; use one of %s" % (angle, " ".join(RAD_PER)),
            )
        self.length_unit = length
        self.angle_unit = angle

    @classmethod
    def for_call(cls, units=None, defaults=None):
        """Defaults (config ``[units]``) overridden per key by a call's ``units`` object."""
        d = dict(defaults if defaults is not None else _input_defaults)
        if units is not None:
            if not isinstance(units, dict):
                raise ActionError(INVALID_PARAMS, "units must be an object")
            extra = set(units) - {"length", "angle"}
            if extra:
                raise ActionError(
                    INVALID_PARAMS, "units: unknown key(s) %s" % ", ".join(sorted(extra))
                )
            d.update(units)
        return cls(d["length"], d["angle"])

    def as_dict(self):
        return {"length": self.length_unit, "angle": self.angle_unit}

    def length(self, cm):
        return cm / CM_PER[self.length_unit]

    def area(self, cm2):
        return cm2 / CM_PER[self.length_unit] ** 2

    def volume(self, cm3):
        return cm3 / CM_PER[self.length_unit] ** 3

    def angle(self, rad):
        return rad / RAD_PER[self.angle_unit]

    def mass(self, kg):
        return kg
