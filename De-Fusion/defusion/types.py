"""Parameter types (design § 5.4).

Each type is a pydantic-compatible ``Annotated`` alias. Validation (HTTP thread, no Fusion) turns the
client's JSON into a ``Spec``; ``Spec.resolve`` runs later on the main thread and produces the
Fusion object or ``Value``. ``resolve_params`` (below) walks a validated model and resolves every Spec.
"""
import importlib
import math
import typing
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    StringConstraints,
    WithJsonSchema,
)

from . import units
from .errors import (
    AMBIGUOUS,
    INVALID_PARAMS,
    NO_DESIGN,
    NOT_FOUND,
    UNSUPPORTED,
    ActionError,
)


class Resolver:
    """State for one resolution pass: the context, the dotted parameter path (for messages),
    and the parameters already resolved (so Point2/Plane/Axis can read ``sketch`` / ``component``)."""

    def __init__(self, ctx, params=None, path=""):
        self.ctx = ctx
        self.params = params if params is not None else {}
        self.path = path

    @property
    def design(self):
        return self.ctx.design

    def child(self, path):
        return Resolver(self.ctx, self.params, path)


class Spec:
    """A validated-but-unresolved parameter value."""

    def resolve(self, rs):  # pragma: no cover - interface
        raise NotImplementedError


# --- scalars ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Value:
    """A resolved Length / Angle / Expr: the expression text and its evaluated number."""

    expr: str
    number: float
    kind: str  # "length" | "angle" | "expr"

    @property
    def cm(self):
        if self.kind != "length":
            raise AttributeError("cm: not a length")
        return self.number

    @property
    def rad(self):
        if self.kind != "angle":
            raise AttributeError("rad: not an angle")
        return self.number

    def value_input(self):
        import adsk.core

        return adsk.core.ValueInput.createByString(self.expr)


_KIND_UNIT = {"length": "length", "angle": "angle", "expr": None}
_EVAL = {
    "length": units.evaluate_length,
    "angle": units.evaluate_angle,
    "expr": units.evaluate_expr,
}


@dataclass(frozen=True)
class ScalarSpec(Spec):
    kind: str
    raw: Any  # number or expression string

    def resolve(self, rs):
        key = _KIND_UNIT[self.kind]
        expr = units.to_expr(self.raw, units.input_defaults()[key] if key else "")
        return Value(expr, _EVAL[self.kind](expr, rs.design, rs.path or self.kind), self.kind)


def _scalar_input(kind):
    def check(v):
        if isinstance(v, ScalarSpec):
            return v
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ValueError("expected a number or an expression string")
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("number must be finite")
        if isinstance(v, str) and not v.strip():
            raise ValueError("expression must not be blank")
        return ScalarSpec(kind, v)

    return check


def _scalar_type(kind, desc):
    return Annotated[
        Any,
        BeforeValidator(_scalar_input(kind)),
        WithJsonSchema(
            {"oneOf": [{"type": "number"}, {"type": "string"}], "description": desc}
        ),
    ]


Length = _scalar_type("length", "Length: a number in the default length unit, or an expression such as '1.5 in' or 'd1*2'.")
Angle = _scalar_type("angle", "Angle: a number in the default angle unit, or an expression such as '45 deg'.")
Expr = _scalar_type("expr", "Unitless number or expression (counts, ratios).")


# --- geometric --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Point3Spec(Spec):
    items: tuple

    def resolve(self, rs):
        import adsk.core

        return adsk.core.Point3D.create(*[s.resolve(rs).cm for s in self.items])


def _point3(v):
    if isinstance(v, Point3Spec):
        return v
    if isinstance(v, dict):
        if set(v) != {"x", "y", "z"}:
            raise ValueError("expected keys x, y, z")
        v = [v["x"], v["y"], v["z"]]
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise ValueError("expected [x, y, z]")
    return Point3Spec(tuple(_scalar_input("length")(i) for i in v))


Point3 = Annotated[
    Any,
    BeforeValidator(_point3),
    WithJsonSchema(
        {
            "description": "[x, y, z] of Length items, or {x, y, z}.",
            "oneOf": [
                {"type": "array", "items": {"oneOf": [{"type": "number"}, {"type": "string"}]},
                 "minItems": 3, "maxItems": 3},
                {"type": "object", "required": ["x", "y", "z"]},
            ],
        }
    ),
]


@dataclass(frozen=True)
class Vector3Spec(Spec):
    xyz: tuple

    def resolve(self, rs):
        import adsk.core

        return adsk.core.Vector3D.create(*self.xyz)


def _vector3(v):
    if isinstance(v, Vector3Spec):
        return v
    if (
        not isinstance(v, (list, tuple))
        or len(v) != 3
        or any(isinstance(i, bool) or not isinstance(i, (int, float)) for i in v)
    ):
        raise ValueError("expected [x, y, z] numbers")
    return Vector3Spec(tuple(float(i) for i in v))


Vector3 = Annotated[
    Any,
    BeforeValidator(_vector3),
    WithJsonSchema(
        {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3,
         "description": "[x, y, z] numbers."}
    ),
]


# --- file names, enums ----------------------------------------------------------------------------


def _file_name(s):
    if "/" in s or "\\" in s or ".." in s or any(ord(c) < 32 or ord(c) == 127 for c in s):
        raise ValueError("file name must not contain '/', '\\', '..' or control characters")
    if s[0] in ".-":
        raise ValueError("file name must not start with '.' or '-'")
    return s


FileName = Annotated[
    str, StringConstraints(min_length=1, max_length=64), AfterValidator(_file_name)
]



def bulk(t):
    """A bulk-entry list parameter of ``t`` entries: 1 to 50 of them (design § 5.12)."""
    return Annotated[list[t], Field(min_length=1, max_length=50)]


Operation = Literal["new_body", "join", "cut", "intersect", "new_component"]

# literal value -> (adsk module, enum class, member). One table per literal type is defined here.
_ENUMS = {
    "new_body": ("fusion", "FeatureOperations", "NewBodyFeatureOperation"),
    "join": ("fusion", "FeatureOperations", "JoinFeatureOperation"),
    "cut": ("fusion", "FeatureOperations", "CutFeatureOperation"),
    "intersect": ("fusion", "FeatureOperations", "IntersectFeatureOperation"),
    "new_component": ("fusion", "FeatureOperations", "NewComponentFeatureOperation"),
}


def adsk_enum(literal_value):
    """Map an enum literal (e.g. an ``Operation`` value) to its ``adsk`` enum member."""
    import importlib

    try:
        module, cls, member = _ENUMS[literal_value]
    except KeyError:
        raise ActionError(INVALID_PARAMS, "unknown enum value %r" % (literal_value,)) from None
    return getattr(getattr(importlib.import_module("adsk." + module), cls), member)


# --- entity references --------------------------------------------------------------------------

# Ref type name -> (adsk module, class). "Entity" is any entity (no class check).
_KINDS = {
    "Body": ("fusion", "BRepBody"),
    "MeshBody": ("fusion", "MeshBody"),
    "Face": ("fusion", "BRepFace"),
    "Edge": ("fusion", "BRepEdge"),
    "Vertex": ("fusion", "BRepVertex"),
    "Sketch": ("fusion", "Sketch"),
    "SketchCurve": ("fusion", "SketchCurve"),
    "SketchLine": ("fusion", "SketchLine"),
    "SketchPoint": ("fusion", "SketchPoint"),
    "Feature": ("fusion", "Feature"),
    "Component": ("fusion", "Component"),
    "Occurrence": ("fusion", "Occurrence"),
    "ConstructionPlane": ("fusion", "ConstructionPlane"),
    "ConstructionAxis": ("fusion", "ConstructionAxis"),
    "Joint": ("fusion", "Joint"),
    "JointOrigin": ("fusion", "JointOrigin"),
    "RigidGroup": ("fusion", "RigidGroup"),
    "Parameter": ("fusion", "Parameter"),
    "ContactSet": ("fusion", "ContactSet"),
    "Entity": ("core", "Base"),
}
_BODY_SEQ = {"Face": "faces", "Edge": "edges", "Vertex": "vertices"}
_REF_KEYS = {"token", "name", "component", "body", "index", "type"}
_NAMED_COLLECTIONS = (
    "bRepBodies", "meshBodies", "sketches", "constructionPlanes", "constructionAxes",
    "constructionPoints", "occurrences", "joints", "jointOrigins", "rigidGroups",
)


def _cls(kind):
    module, name = _KINDS[kind]
    return getattr(importlib.import_module("adsk." + module), name)


def _classes(kinds):
    """isinstance classes of Ref ``kinds``. Every adsk module has its own ``Base``, so "Entity" takes each one."""
    out = []
    for k in kinds:
        if k == "Entity":
            out += [b for m in ("core", "fusion") if (b := getattr(importlib.import_module("adsk." + m), "Base", None))]
        else:
            out.append(_cls(k))
    return tuple(out)


def entity_ref(obj):
    """Serialise an entity as {type, name, token, component} (design § 5.4); inverse of Ref input."""

    def comp_name(o):
        for chain in (("parentComponent",), ("component",), ("body", "parentComponent"),
                      ("parentSketch", "parentComponent")):
            cur = o
            try:
                for attr in chain:
                    cur = getattr(cur, attr)
            except AttributeError:
                continue
            if cur is not None:
                return getattr(cur, "name", None)
        return None

    return {
        "type": type(obj).__name__,
        "name": getattr(obj, "name", None),
        "token": getattr(obj, "entityToken", None),
        "component": comp_name(obj),
    }


def _need_design(rs):
    if rs.design is None:
        raise ActionError(NO_DESIGN, "%s: no active design" % (rs.path or "parameter"))
    return rs.design


def _ref_shape(v, allow_index_only=False):
    """Validate the JSON shape of a Ref: a non-empty string or an object of known keys."""
    if isinstance(v, str):
        if not v.strip():
            raise ValueError("reference must not be blank")
        return v
    if not isinstance(v, dict):
        raise ValueError("expected a name/token string or an object such as {\"token\": ...}")
    v = {k: x for k, x in v.items() if x is not None}  # outputs carry null name/component: round-trip them
    extra = set(v) - _REF_KEYS
    if extra:
        raise ValueError("unknown reference key(s): %s" % ", ".join(sorted(extra)))
    for k in ("token", "name"):
        if k in v and (not isinstance(v[k], str) or not v[k].strip()):
            raise ValueError("%s must be a non-blank string" % k)
    if "index" in v and (isinstance(v["index"], bool) or not isinstance(v["index"], int) or v["index"] < 0):
        raise ValueError("index must be an integer >= 0")
    if "body" in v:
        _ref_shape(v["body"])
        if "index" not in v:
            raise ValueError("'body' needs 'index'")
    if "token" not in v and "name" not in v and "body" not in v and not (
        allow_index_only and "index" in v
    ):
        raise ValueError("reference needs 'token', 'name', or 'body' with 'index'")
    return v


_REF_SCHEMA = {
    "description": "Entity reference: a name or token string, or {token} / {name, component?} / "
                   "{body, index} (as returned in outputs).",
    "oneOf": [
        {"type": "string"},
        {"type": "object",
         "properties": {"token": {"type": "string"}, "name": {"type": "string"},
                        "component": {}, "body": {}, "index": {"type": "integer", "minimum": 0},
                        "type": {"type": "string"}},
         "additionalProperties": False},
    ],
}


@dataclass(frozen=True)
class RefMarker:
    """Annotated metadata identifying a Ref-typed parameter (needs Fusion objects to resolve)."""

    kind: str


def uses_refs(annotation):
    """True if the annotation is, contains, or has a pydantic-model field that is a Ref type."""
    origin = typing.get_origin(annotation)
    if origin is Annotated:
        base, *meta = typing.get_args(annotation)
        return any(isinstance(m, RefMarker) for m in meta) or uses_refs(base)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return any(uses_refs(f.rebuild_annotation()) for f in annotation.model_fields.values())
    return any(uses_refs(a) for a in typing.get_args(annotation))


@dataclass(frozen=True)
class RefSpec(Spec):
    kind: str
    raw: Any

    def resolve(self, rs):
        return resolve_ref((self.kind,), self.raw, rs)


def _ref_type(kind):
    def check(v):
        return v if isinstance(v, RefSpec) else RefSpec(kind, _ref_shape(v))

    return Annotated[Any, BeforeValidator(check), WithJsonSchema(_REF_SCHEMA), RefMarker(kind)]


Body = _ref_type("Body")
MeshBody = _ref_type("MeshBody")
Face = _ref_type("Face")
Edge = _ref_type("Edge")
Vertex = _ref_type("Vertex")
Sketch = _ref_type("Sketch")
SketchCurve = _ref_type("SketchCurve")
SketchPoint = _ref_type("SketchPoint")
Feature = _ref_type("Feature")
Component = _ref_type("Component")
Occurrence = _ref_type("Occurrence")
ConstructionPlane = _ref_type("ConstructionPlane")
ConstructionAxis = _ref_type("ConstructionAxis")
Joint = _ref_type("Joint")
JointOrigin = _ref_type("JointOrigin")
RigidGroup = _ref_type("RigidGroup")
Parameter = _ref_type("Parameter")
ContactSet = _ref_type("ContactSet")
Entity = _ref_type("Entity")


def _named_entities(design, component=None):
    """Every named entity of the design (or of one component), without duplicates."""
    seen = set()

    def fresh(e):
        tok = getattr(e, "entityToken", id(e))
        if tok in seen:
            return False
        seen.add(tok)
        return True

    if component is None:
        comps = [design.rootComponent] + list(design.allComponents)
    else:
        comps = [component]
    for c in comps:
        if component is None and fresh(c):
            yield c
        for attr in _NAMED_COLLECTIONS:
            for e in getattr(c, attr, ()):
                if fresh(e):
                    yield e
    feature_cls = _cls("Feature")
    try:
        tl = design.timeline
        items = [tl.item(i) for i in range(tl.count)]
    except Exception:
        items = []
    for to in items:
        e = getattr(to, "entity", None)
        if isinstance(e, feature_cls) and fresh(e):
            if component is None or getattr(getattr(e, "parentComponent", None), "entityToken", None) == component.entityToken:
                yield e
    if component is None:
        for p in getattr(design, "allParameters", ()):
            if fresh(p):
                yield p


def _wrong_type(kinds, e):
    return ActionError(
        INVALID_PARAMS,
        "expected %s but found %s" % (" or ".join(kinds), type(e).__name__),
        hints=["resolved entity: %s" % (entity_ref(e),)],
    )


def _by_token(kinds, token, rs):
    classes = _classes(kinds)
    found = list(_need_design(rs).findEntityByToken(token) or [])
    if not found:
        return None
    for e in found:
        if isinstance(e, classes):
            return e
    raise _wrong_type(kinds, found[0])


def _by_name(kinds, name, rs, component=None):
    design = _need_design(rs)
    classes = _classes(kinds)
    if "ContactSet" in kinds:
        pool = list(getattr(design, "contactSets", ()))
    else:
        pool = list(_named_entities(design, component))
    named = [e for e in pool if getattr(e, "name", None) == name]
    hits = [e for e in named if isinstance(e, classes)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise ActionError(
            AMBIGUOUS,
            "%s: %d entities are named %r" % (rs.path or "reference", len(hits), name),
            hints=["pass a token, or scope with component"],
            candidates=[entity_ref(e) for e in hits],
        )
    if named:
        raise _wrong_type(kinds, named[0])
    return None


def resolve_ref(kinds, raw, rs):
    """Resolve a Ref (any of the forms of design § 5.4) to an entity of one of ``kinds``."""
    where = rs.path or "reference"
    if "ContactSet" in kinds and not hasattr(_need_design(rs), "contactSets"):
        raise ActionError(UNSUPPORTED, "%s: this Fusion build has no contact sets" % where)
    if isinstance(raw, str):
        e = _by_name(kinds, raw, rs)
        if e is None:
            e = _by_token(kinds, raw, rs)
        if e is None:
            raise ActionError(NOT_FOUND, "%s: nothing named or tokened %r" % (where, raw))
        return e
    if "token" in raw:
        e = _by_token(kinds, raw["token"], rs)
        if e is None:
            raise ActionError(NOT_FOUND, "%s: no entity with token %r" % (where, raw["token"]))
        return e
    if "body" in raw:
        seq_kind = next((k for k in kinds if k in _BODY_SEQ), None)
        if seq_kind is None:
            raise ActionError(INVALID_PARAMS, "%s: {body, index} is only valid for faces, edges and vertices" % where)
        body = resolve_ref(("Body",), raw["body"], rs.child(where + ".body"))
        seq = list(getattr(body, _BODY_SEQ[seq_kind]))
        if raw["index"] >= len(seq):
            raise ActionError(
                INVALID_PARAMS,
                "%s: index %d out of range (body has %d %s)"
                % (where, raw["index"], len(seq), _BODY_SEQ[seq_kind]),
            )
        return seq[raw["index"]]
    scope = None
    if "component" in raw:
        scope = resolve_ref(("Component",), raw["component"], rs.child(where + ".component"))
    e = _by_name(kinds, raw["name"], rs, scope)
    if e is None:
        raise ActionError(NOT_FOUND, "%s: nothing named %r" % (where, raw["name"]))
    return e


# --- Plane, Axis, Profile, Point2, TimelineItem ----------------------------------------------------

_ORIGIN_PLANES = {"XY": "xYConstructionPlane", "XZ": "xZConstructionPlane", "YZ": "yZConstructionPlane"}
_ORIGIN_AXES = {"X": "xConstructionAxis", "Y": "yConstructionAxis", "Z": "zConstructionAxis"}


def _origin_component(rs):
    comp = rs.params.get("component")
    return comp if comp is not None else _need_design(rs).activeComponent


@dataclass(frozen=True)
class PlaneSpec(Spec):
    raw: Any

    def resolve(self, rs):
        import adsk.core

        if isinstance(self.raw, str) and self.raw in _ORIGIN_PLANES:
            return getattr(_origin_component(rs), _ORIGIN_PLANES[self.raw])
        e = resolve_ref(("ConstructionPlane", "Face"), self.raw, rs)
        if isinstance(e, _cls("Face")) and not isinstance(e.geometry, adsk.core.Plane):
            raise ActionError(INVALID_PARAMS, "%s: the face is not planar" % (rs.path or "plane"))
        return e


@dataclass(frozen=True)
class AxisSpec(Spec):
    raw: Any

    def resolve(self, rs):
        import adsk.core

        if isinstance(self.raw, str) and self.raw in _ORIGIN_AXES:
            return getattr(_origin_component(rs), _ORIGIN_AXES[self.raw])
        e = resolve_ref(("ConstructionAxis", "Edge", "SketchLine"), self.raw, rs)
        if isinstance(e, _cls("Edge")) and not isinstance(e.geometry, adsk.core.Line3D):
            raise ActionError(INVALID_PARAMS, "%s: the edge is not linear" % (rs.path or "axis"))
        return e


def _plane(v):
    if isinstance(v, PlaneSpec):
        return v
    if isinstance(v, str) and v in _ORIGIN_PLANES:
        return PlaneSpec(v)
    return PlaneSpec(_ref_shape(v))


def _axis(v):
    if isinstance(v, AxisSpec):
        return v
    if isinstance(v, str) and v in _ORIGIN_AXES:
        return AxisSpec(v)
    return AxisSpec(_ref_shape(v))


Plane = Annotated[
    Any, BeforeValidator(_plane),
    WithJsonSchema({"description": "\"XY\", \"XZ\", \"YZ\", or a Ref to a construction plane or planar face.",
                    "oneOf": [{"type": "string"}, {"type": "object"}]}),
    RefMarker("Plane"),
]
Axis = Annotated[
    Any, BeforeValidator(_axis),
    WithJsonSchema({"description": "\"X\", \"Y\", \"Z\", or a Ref to a construction axis, linear edge or sketch line.",
                    "oneOf": [{"type": "string"}, {"type": "object"}]}),
    RefMarker("Axis"),
]


@dataclass(frozen=True)
class ProfileSpec(Spec):
    sketch: Any
    index: Any  # None | int | tuple[int, ...]

    def resolve(self, rs):
        import adsk.core

        where = rs.path or "profile"
        sketch = resolve_ref(("Sketch",), self.sketch, rs.child(where + ".sketch"))
        profiles = list(sketch.profiles)

        def pick(i):
            if i >= len(profiles):
                raise ActionError(
                    INVALID_PARAMS,
                    "%s: profile index %d out of range (sketch has %d profiles)"
                    % (where, i, len(profiles)),
                )
            return profiles[i]

        if self.index is None:
            if len(profiles) != 1:
                raise ActionError(
                    INVALID_PARAMS,
                    "%s: sketch has %d profiles; give index" % (where, len(profiles)),
                )
            return profiles[0]
        if isinstance(self.index, int):
            return pick(self.index)
        coll = adsk.core.ObjectCollection.create()
        for i in self.index:
            coll.add(pick(i))
        return coll


def _is_index(i):
    return isinstance(i, int) and not isinstance(i, bool) and i >= 0


def _profile(v):
    if isinstance(v, ProfileSpec):
        return v
    if not isinstance(v, dict) or "sketch" not in v or set(v) - {"sketch", "index"}:
        raise ValueError('expected {"sketch": Ref, "index": int | [int, ...]}')
    idx = v.get("index")
    if isinstance(idx, (list, tuple)):
        if not idx or not all(_is_index(i) for i in idx):
            raise ValueError("index list must be non-empty integers >= 0")
        idx = tuple(idx)
    elif idx is not None and not _is_index(idx):
        raise ValueError("index must be an integer >= 0 or a list of them")
    return ProfileSpec(_ref_shape(v["sketch"]), idx)


Profile = Annotated[
    Any, BeforeValidator(_profile),
    WithJsonSchema({"description": "{sketch: Ref, index: int | [int, ...]}; index may be omitted only "
                                   "when the sketch has exactly one profile.",
                    "type": "object", "required": ["sketch"]}),
    RefMarker("Profile"),
]


@dataclass(frozen=True)
class Point2Spec(Spec):
    items: tuple

    def resolve(self, rs):
        import adsk.core

        vals = [s.resolve(rs).cm for s in self.items]
        if len(vals) == 2:
            return adsk.core.Point3D.create(vals[0], vals[1], 0)
        sketch = rs.params.get("sketch")
        if sketch is None:
            raise ActionError(
                INVALID_PARAMS,
                "%s: a 3-item point needs the action's 'sketch' parameter" % (rs.path or "point"),
            )
        p = sketch.modelToSketchSpace(adsk.core.Point3D.create(*vals))
        return adsk.core.Point3D.create(p.x, p.y, 0)


def _point2(v):
    if isinstance(v, Point2Spec):
        return v
    if not isinstance(v, (list, tuple)) or len(v) not in (2, 3):
        raise ValueError("expected [x, y] (sketch space) or [x, y, z] (model space)")
    return Point2Spec(tuple(_scalar_input("length")(i) for i in v))


Point2 = Annotated[
    Any, BeforeValidator(_point2),
    WithJsonSchema({"type": "array", "items": {"oneOf": [{"type": "number"}, {"type": "string"}]},
                    "minItems": 2, "maxItems": 3,
                    "description": "[x, y] in sketch space, or [x, y, z] in model space."}),
]


@dataclass(frozen=True)
class TimelineSpec(Spec):
    raw: Any

    def resolve(self, rs):
        design = _need_design(rs)
        where = rs.path or "timeline item"
        if isinstance(self.raw, dict) and set(self.raw) == {"index"}:
            n = self.raw["index"]
            try:
                tl = design.timeline
                item = tl.item(n) if 0 <= n < tl.count else None
            except Exception:
                item = None
            if item is None:
                raise ActionError(NOT_FOUND, "%s: no timeline item at index %d" % (where, n))
            return item
        e = resolve_ref(("Entity",), self.raw, rs)
        to = getattr(e, "timelineObject", None)
        if to is None:
            raise ActionError(INVALID_PARAMS, "%s: %s has no timeline item" % (where, type(e).__name__))
        return to


def _timeline(v):
    if isinstance(v, TimelineSpec):
        return v
    if isinstance(v, dict) and set(v) == {"index"}:
        if not _is_index(v["index"]):
            raise ValueError("index must be an integer >= 0")
        return TimelineSpec(v)
    return TimelineSpec(_ref_shape(v))


TimelineItem = Annotated[
    Any, BeforeValidator(_timeline),
    WithJsonSchema({"description": "A Ref to a timeline-backed entity, or {index: n}.",
                    "oneOf": [{"type": "string"}, {"type": "object"}]}),
    RefMarker("TimelineItem"),
]


# --- CAM refs ---------------------------------------------------------------------------------------


def _cam_product(rs):
    doc = rs.ctx.document
    product = doc.products.itemByProductType("CAMProductType") if doc is not None else None
    cam = _cls_cam().CAM.cast(product) if product is not None else None
    if cam is None:
        raise ActionError(NOT_FOUND, "%s: the active document has no CAM product" % (rs.path or "cam"))
    return cam


def _cls_cam():
    return importlib.import_module("adsk.cam")


def _match_cam(kind, items, raw, setup_name, where):
    token = raw.get("token") if isinstance(raw, dict) else None
    name = raw if isinstance(raw, str) else raw.get("name")
    items = list(items)
    if token is not None:
        hits = [i for i in items if getattr(i, "entityToken", None) == token]
    else:
        hits = [i for i in items if getattr(i, "name", None) == name]
    if not hits:
        raise ActionError(
            NOT_FOUND, "%s: no %s %s" % (where, kind.lower(), ("token %r" % token) if token else repr(name))
        )
    if len(hits) > 1:
        raise ActionError(
            AMBIGUOUS, "%s: %d %ss are named %r" % (where, len(hits), kind.lower(), name),
            hints=["pass a token" + ("" if kind == "Setup" else ", or scope with setup")],
            candidates=[entity_ref(i) for i in hits],
        )
    return hits[0]


@dataclass(frozen=True)
class CamSpec(Spec):
    kind: str  # "Setup" | "Operation"
    raw: Any

    def resolve(self, rs):
        where = rs.path or self.kind.lower()
        cam = _cam_product(rs)
        if self.kind == "Setup":
            return _match_cam("Setup", cam.setups, self.raw, None, where)
        items = cam.allOperations
        if isinstance(self.raw, dict) and "setup" in self.raw:
            setup = _match_cam("Setup", cam.setups, {"name": self.raw["setup"]}, None, where + ".setup")
            items = setup.allOperations
        return _match_cam("Operation", items, self.raw, None, where)


_CAM_KEYS = {"token", "name", "setup", "type"}


def _cam_ref(kind):
    def check(v):
        if isinstance(v, CamSpec):
            return v
        if isinstance(v, str) and v.strip():
            return CamSpec(kind, v)
        if isinstance(v, dict) and not (set(v) - _CAM_KEYS) and ("token" in v or "name" in v) and all(
            isinstance(x, str) and x.strip() for x in v.values()
        ):
            if "setup" in v and kind == "Setup":
                raise ValueError("'setup' is only valid for an operation")
            return CamSpec(kind, v)
        raise ValueError('expected a name, {"name": s}, {"name": s, "setup": s} or {"token": t}')

    return Annotated[
        Any, BeforeValidator(check),
        WithJsonSchema({"description": "CAM %s: a name, {name, setup?}, or {token}." % kind.lower(),
                        "oneOf": [{"type": "string"}, {"type": "object"}]}),
        RefMarker("Cam" + kind),
    ]


CamSetup = _cam_ref("Setup")
CamOperation = _cam_ref("Operation")


# --- resolution walk ----------------------------------------------------------------------------------

_FIRST = ("component", "sketch")  # Plane/Axis/Point2 read these from rs.params


def resolve_value(v, rs):
    """Resolve every Spec inside a validated value (lists, dicts and pydantic models recursed)."""
    if isinstance(v, Spec):
        return v.resolve(rs)
    if isinstance(v, BaseModel):
        update = {}
        for name in type(v).model_fields:
            old = getattr(v, name)
            new = resolve_value(old, rs.child(rs.path + "." + name if rs.path else name))
            if new is not old:
                update[name] = new
        return v.model_copy(update=update) if update else v
    if isinstance(v, (list, tuple)):
        out = [resolve_value(x, rs.child("%s[%d]" % (rs.path, i))) for i, x in enumerate(v)]
        return out if isinstance(v, list) else tuple(out)
    if isinstance(v, dict):
        return {k: resolve_value(x, rs.child("%s.%s" % (rs.path, k))) for k, x in v.items()}
    return v


def resolve_params(ctx, action, validated):
    """Resolve a validated model (or dict) to {param: value}; the one entry for call and call_inline.

    ``component`` and ``sketch`` are resolved first so Plane/Axis/Point2 can use them."""
    if isinstance(validated, BaseModel):
        data = {n: getattr(validated, n) for n in type(validated).model_fields}
    else:
        data = dict(validated)
    rs = Resolver(ctx)
    order = [n for n in _FIRST if n in data] + [n for n in data if n not in _FIRST]
    resolved = {}
    for name in order:
        resolved[name] = resolve_value(data[name], rs.child(name))
        rs.params[name] = resolved[name]
    return {n: resolved[n] for n in data}
