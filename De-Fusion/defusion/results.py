"""Result envelopes, size budget, paging and change summary (design § 5.5, § 5.8)."""
import base64
import json
from dataclasses import dataclass, field

from mcp.types import CallToolResult, ImageContent, TextContent

from .context import BulkResult, page  # noqa: F401  (ctx.page and results.page are the same function)
from .errors import RESULT_TOO_LARGE, ActionError, from_exception

MAX_RESULT_BYTES = 65536
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_BBOX_BODIES = 500


@dataclass
class Image:
    """A PNG returned by an action, with the JSON metadata that goes in the envelope."""

    png: bytes
    meta: dict = field(default_factory=dict)


def dumps(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _images(raw):
    if isinstance(raw, Image):
        return [raw]
    if isinstance(raw, list) and raw and all(isinstance(i, Image) for i in raw):
        return list(raw)
    return []


def _json_result(raw):
    """The JSON-able ``result`` value: the metadata when the action returned images."""
    if isinstance(raw, Image):
        return raw.meta
    if _images(raw):
        return [i.meta for i in raw]
    return raw


def check_result(raw):
    """Raise RESULT_TOO_LARGE if the JSON result exceeds 64 KiB or the images total over 4 MiB."""
    size = len(dumps(_json_result(raw)).encode("utf-8"))
    if size > MAX_RESULT_BYTES and isinstance(raw, BulkResult):
        raise ActionError(
            RESULT_TOO_LARGE,
            "all entries ran; the result is %d bytes and the limit is %d" % (size, MAX_RESULT_BYTES),
            hints=["check state with document.list_open or datafile_get", "send fewer entries per call"],
        )
    if size > MAX_RESULT_BYTES:
        raise ActionError(
            RESULT_TOO_LARGE,
            "result is %d bytes; the limit is %d" % (size, MAX_RESULT_BYTES),
            hints=["use a smaller limit or a larger offset to page through the results"],
        )
    total = sum(len(i.png) for i in _images(raw))
    if total > MAX_IMAGE_BYTES:
        raise ActionError(
            RESULT_TOO_LARGE,
            "images total %d bytes; the limit is %d" % (total, MAX_IMAGE_BYTES),
            hints=["reduce the image size (width/height, or tile for view_sheet)"],
        )


def ok_envelope(tool, action, result, units, changes=None, undo_grouped=True, made_editable=None):
    env = {"ok": True, "tool": tool, "action": action, "result": result, "units": units}
    if changes is not None:
        env["changes"] = changes
    if undo_grouped is False:
        env["undo_grouped"] = False
    if made_editable:
        env["made_editable"] = list(made_editable)
    return env


def error_envelope(tool, action, exc, made_editable=None):
    err = from_exception(exc)
    env = {"ok": False, "tool": tool, "action": action, "error": err.to_dict()}
    if made_editable:
        env["made_editable"] = list(made_editable)
    return env


def to_call_result(envelope, images=()):
    """CallToolResult for an envelope; images (if any) come first as ImageContent."""
    content = [
        ImageContent(type="image", data=base64.b64encode(i.png).decode("ascii"), mime_type="image/png")
        for i in images
    ]
    content.append(TextContent(type="text", text=dumps(envelope)))
    return CallToolResult(
        content=content, structured_content=envelope, is_error=not envelope["ok"]
    )


def shape(tool, action, raw, units, changes=None, undo_grouped=True, made_editable=None):
    """§ 5.3 step 7: size-check a raw action result and build the CallToolResult."""
    try:
        check_result(raw)
    except ActionError as exc:
        return to_call_result(error_envelope(tool, action, exc, made_editable))
    env = ok_envelope(tool, action, _json_result(raw), units, changes, undo_grouped, made_editable)
    return to_call_result(env, _images(raw))


def error_result(tool, action, exc, made_editable=None):
    return to_call_result(error_envelope(tool, action, exc, made_editable))


# --- change summary -------------------------------------------------------------------------


def _components(design):
    seen, comps = set(), []
    for c in [design.rootComponent] + list(design.allComponents):
        if c.entityToken not in seen:
            seen.add(c.entityToken)
            comps.append(c)
    return comps


def _bbox(bodies, output_units):
    boxes = [b.boundingBox for b in bodies]
    if not boxes:
        return None
    lo = [min(getattr(b.minPoint, a) for b in boxes) for a in "xyz"]
    hi = [max(getattr(b.maxPoint, a) for b in boxes) for a in "xyz"]
    return [output_units.length(v) for v in lo + hi]


def snapshot(design, output_units):
    """{"bodies", "timeline", "bbox"} for a design; bbox is [minx, miny, minz, maxx, maxy, maxz]."""
    import adsk.fusion

    bodies = [b for c in _components(design) for b in c.bRepBodies]
    timeline = None
    if design.designType == adsk.fusion.DesignTypes.ParametricDesignType:
        try:
            timeline = design.timeline.count
        except Exception:
            timeline = None
    bbox = None
    try:
        visible = [b for b in bodies if b.isVisible]
        if len(visible) <= MAX_BBOX_BODIES:
            bbox = _bbox(visible, output_units)
    except Exception:
        bbox = None
    return {"bodies": len(bodies), "timeline": timeline, "bbox": bbox}


def diff(before, after):
    """``{key: [before, after]}`` for the snapshot keys whose values changed."""
    if before is None or after is None:
        return {}
    return {k: [before[k], after[k]] for k in after if before.get(k) != after[k]}
