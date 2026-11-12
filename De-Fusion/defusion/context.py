"""Ctx: what every action receives (design § 5.9)."""
import json
import logging

from . import types
from .errors import (
    INVALID_PARAMS,
    NO_DESIGN,
    TEXT_COMMAND_FAILED,
    UNSUPPORTED,
    WRONG_DESIGN_TYPE,
    ActionError,
    from_exception,
)

ATTR_GROUP = "defusion"
log = logging.getLogger("defusion")


def page(seq, offset, limit, build=None):
    """One page of ``seq``: slice first, then apply ``build`` to each sliced item (design § 5.5)."""
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ActionError(INVALID_PARAMS, "offset must be an integer >= 0")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ActionError(INVALID_PARAMS, "limit must be an integer from 1 to 500")
    seq = list(seq)
    items = seq[offset : offset + limit]
    if build is not None:
        items = [build(i) for i in items]
    nxt = offset + len(items)
    return {
        "items": items,
        "total": len(seq),
        "offset": offset,
        "next_offset": nxt if nxt < len(seq) else None,
    }


class BulkResult(dict):
    """The ``{"results", "ok", "failed"}`` dict returned by ``Ctx.bulk``; the size budget gives it
    its own RESULT_TOO_LARGE message (design § 5.12)."""


class Ctx:
    """Fields and helpers for one action call. ``app``, ``ui``, ``document`` and ``design`` are
    ``None`` in a ``main_thread=False`` action (``Ctx.offline``)."""

    def __init__(self, app, ui, document, design, allowed, profile_items, output_units, folder_limit=None):
        self.app = app
        self.ui = ui
        self.document = document
        self.design = design
        self.allowed = frozenset(allowed)
        self.profile_items = list(profile_items)
        self.output = output_units
        self.units = output_units.as_dict()
        self.folder_limit = folder_limit
        self.preserve_read_only = False
        self.made_editable = []

    @classmethod
    def for_app(cls, app, allowed, profile_items, output_units, folder_limit=None):
        """Main-thread Ctx; ``design`` comes from the document's products, not ``app.activeProduct``."""
        import adsk.fusion

        document = app.activeDocument
        product = document.products.itemByProductType("DesignProductType") if document else None
        design = adsk.fusion.Design.cast(product) if product is not None else None
        return cls(app, app.userInterface, document, design, allowed, profile_items, output_units, folder_limit)

    @classmethod
    def offline(cls, allowed, profile_items, units, folder_limit=None):
        """Fusion-free Ctx for ``main_thread=False`` actions."""
        return cls(None, None, None, None, allowed, profile_items, units, folder_limit)

    # --- design access ----------------------------------------------------------------------
    def require_design(self):
        if self.design is None:
            raise ActionError(NO_DESIGN, "no active design", hints=["open or create a design document"])
        return self.design

    def require_parametric(self):
        import adsk.fusion

        design = self.require_design()
        if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
            raise ActionError(
                WRONG_DESIGN_TYPE,
                "this action needs a parametric design (the timeline is off in direct mode)",
            )
        return design

    def component(self, ref=None):
        """The given component, else the design's active component."""
        return ref if ref is not None else self.require_design().activeComponent

    # --- refs and units -----------------------------------------------------------------------
    def ref(self, obj):
        return types.entity_ref(obj)

    def refs(self, objs):
        return [types.entity_ref(o) for o in objs]

    def length_out(self, cm):
        return self.output.length(cm)

    def angle_out(self, rad):
        return self.output.angle(rad)

    def area_out(self, cm2):
        return self.output.area(cm2)

    def volume_out(self, cm3):
        return self.output.volume(cm3)

    def mass_out(self, kg):
        return self.output.mass(kg)

    def page(self, seq, offset, limit, build=None):
        return page(seq, offset, limit, build)

    # --- Fusion helpers -------------------------------------------------------------------------
    def text_command(self, cmd):
        if self.app is None:
            raise ActionError(UNSUPPORTED, "text commands need Fusion; this action is main_thread=False")
        try:
            return self.app.executeTextCommand(cmd)
        except Exception as exc:
            raise ActionError(
                TEXT_COMMAND_FAILED, "text command %r failed: %s" % (cmd, exc), command=cmd
            ) from exc

    def attr_set(self, entity, key, value):
        attrs = entity.attributes
        text = json.dumps(value)
        existing = attrs.itemByName(ATTR_GROUP, key)
        if existing is not None:
            existing.value = text
        else:
            attrs.add(ATTR_GROUP, key, text)

    def attr_get(self, entity, key):
        a = entity.attributes.itemByName(ATTR_GROUP, key)
        return None if a is None else json.loads(a.value)

    def fail(self, code, message, hints=()):
        raise ActionError(code, message, hints)

    def bulk(self, entries, fn):
        """Call ``fn(entry)`` for each entry in order; a failed entry never stops later ones (§ 5.12)."""
        records, failed = [], 0
        for entry in entries:
            rec = {"input": entry.model_dump() if hasattr(entry, "model_dump") else entry}
            try:
                rec["ok"], rec["result"] = True, fn(entry)
            except Exception as exc:
                if not isinstance(exc, ActionError):
                    log.warning("bulk entry %r raised", rec["input"], exc_info=True)
                rec["ok"], rec["error"] = False, from_exception(exc).to_dict()
                failed += 1
            records.append(rec)
        return BulkResult(results=records, ok=len(records) - failed, failed=failed)
