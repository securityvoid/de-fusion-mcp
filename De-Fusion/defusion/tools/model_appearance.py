"""Materials and appearances: list, assign, set and color (design § 11)."""
import re
from typing import Literal

import adsk.core

from ..actions import action
from ..errors import INVALID_PARAMS, NOT_FOUND, UNSUPPORTED
from ..types import Body, Entity
from .model_common import expect, kind

APPEARANCE_LIBRARY = "Fusion Appearance Library"
ALBEDO = "opaque_albedo"
COLOR = re.compile(r"^#([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})?$")


def _libraries(ctx, library):
    libs = ctx.app.materialLibraries
    if library is None:
        return list(libs)
    found = libs.itemByName(library)
    if found is None:
        ctx.fail(NOT_FOUND, "library %r not found" % library, hints=[lib.name for lib in libs])
    return [found]


def _lookup(ctx, attr, name, library):
    """The material/appearance ``name`` and whether it is already in the design: design first, then libraries."""
    found = getattr(ctx.design, attr).itemByName(name)
    if found is not None:
        return found, True
    for lib in _libraries(ctx, library):
        try:
            found = getattr(lib, attr).itemByName(name)
        except RuntimeError:  # Fusion raises "invalid name" for a library of the other kind (no appearances)
            continue
        if found is not None:
            return found, False
    ctx.fail(NOT_FOUND, "%s %r not found" % (attr[:-1], name))


@action("model", access="read")
def material_list(ctx, source: Literal["design", "library"] = "library", library: str | None = None,
                  query: str | None = None, offset: int = 0, limit: int = 100) -> dict:
    """List materials from the design or from the material libraries."""
    design = ctx.require_design()
    if source == "design":
        pairs = [(m, "design") for m in design.materials]
    else:
        pairs = [(m, lib.name) for lib in _libraries(ctx, library) for m in lib.materials]
    if query:
        pairs = [(m, lib) for m, lib in pairs if query.lower() in m.name.lower()]
    return ctx.page(pairs, offset, limit, build=lambda p: {"name": p[0].name, "id": p[0].id, "library": p[1]})


@action("model", access="write")
def material_assign(ctx, bodies: list[Body], material: str, library: str | None = None) -> dict:
    """Assign a physical material to bodies."""
    ctx.require_design()
    mat, _ = _lookup(ctx, "materials", material, library)
    for b in bodies:
        b.material = mat
    return {"bodies": ctx.refs(bodies), "material": mat.name}


def _assign(ctx, targets, appearance):
    for t in targets:
        for obj in (t.bRepBodies if kind(t) == "Component" else [t]):
            obj.appearance = appearance
    return ctx.refs(targets)


@action("model", access="write")
def appearance_set(ctx, targets: list[Entity], appearance: str, library: str | None = None) -> dict:
    """Set the appearance of bodies, faces, occurrences or components."""
    design = ctx.require_design()
    expect(ctx, targets, ("BRepBody", "BRepFace", "Occurrence", "Component"), "targets")
    app, in_design = _lookup(ctx, "appearances", appearance, library)
    if not in_design:
        app = design.appearances.addByCopy(app, app.name)
    return {"targets": _assign(ctx, targets, app), "appearance": app.name}


@action("model", access="write")
def color_set(ctx, targets: list[Entity], color: str) -> dict:
    """Set a solid color (#RRGGBB or #RRGGBBAA) on bodies, faces, occurrences or components."""
    design = ctx.require_design()
    m = COLOR.match(color)
    if not m:
        ctx.fail(INVALID_PARAMS, "color must be #RRGGBB or #RRGGBBAA")
    expect(ctx, targets, ("BRepBody", "BRepFace", "Occurrence", "Component"), "targets")
    hexa = (m.group(1) + (m.group(2) or "FF")).upper()
    name = "De-Fusion color " + hexa
    app = design.appearances.itemByName(name)
    if app is None:
        base = _color_base(ctx)
        app = design.appearances.addByCopy(base, name)
        r, g, b, a = (int(hexa[i:i + 2], 16) for i in range(0, 8, 2))
        app.appearanceProperties.itemById(ALBEDO).value = adsk.core.Color.create(r, g, b, a)
    return {"targets": _assign(ctx, targets, app), "appearance": name, "color": "#" + hexa}


def _color_base(ctx):
    lib = ctx.app.materialLibraries.itemByName(APPEARANCE_LIBRARY)
    for a in (lib.appearances if lib is not None else ()):
        if isinstance(a.appearanceProperties.itemById(ALBEDO), adsk.core.ColorProperty):
            return a
    ctx.fail(UNSUPPORTED, "no colorable base appearance found in %r" % APPEARANCE_LIBRARY)
