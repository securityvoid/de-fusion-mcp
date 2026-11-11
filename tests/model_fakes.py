"""Model-specific fake adsk objects layered over tests/fakes/adsk (design § 14.1).

Importing this module (a) lets ``defusion.tools.<module>`` import before every sibling tool module
exists, (b) adds the extra ``adsk`` classes/enums the model actions touch and (c) provides ``Env``.
Inputs and feature collections record every call; members can be removed per test to exercise
``UNSUPPORTED`` (``coll.hide(name)``, ``coll.hide_input(attr)``)."""
import importlib
import os
import sys
import types as pytypes

import pytest

_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "De-Fusion", "defusion", "tools")

if "defusion.tools" not in sys.modules:  # import model_* without running tools/__init__ (it needs every tool)
    import defusion

    stub = pytypes.ModuleType("defusion.tools")
    stub.__path__ = [_TOOLS]
    sys.modules["defusion.tools"] = stub
    defusion.tools = stub

from adsk import core, fusion  # noqa: E402

from defusion import actions, units  # noqa: E402
from defusion.context import Ctx  # noqa: E402
from defusion.errors import ActionError  # noqa: E402
from defusion.units import OutputUnits  # noqa: E402

core.Base.objectType = property(lambda s: "adsk::fusion::" + type(s).__name__)


def _enum(name, *members):
    return type(name, (), {m: m for m in members})


def _definition(name):
    return type(name, (), {"create": staticmethod(lambda *a: (name,) + a)})


_ENUMS = {
    "ExtentDirections": ("PositiveExtentDirection", "NegativeExtentDirection", "SymmetricExtentDirection"),
    "SweepOrientationTypes": ("PerpendicularOrientationType", "ParallelOrientationType"),
    "PatternDistanceType": ("SpacingPatternDistanceType", "ExtentPatternDistanceType"),
    "RuledSurfaceTypes": ("NormalRuledSurfaceType", "TangentRuledSurfaceType"),
    "BendPositionTypes": ("InsideBendPositionType", "OutsideBendPositionType", "AdjacentBendPositionType",
                          "TangentBendPositionType"),
    "FoldBendLinePositionTypes": ("CenterFoldBendLinePositionType", "StartFoldBendLinePositionType",
                                  "EndFoldBendLinePositionType"),
    "DistanceUnits": ("MillimeterDistanceUnits", "CentimeterDistanceUnits", "MeterDistanceUnits",
                      "InchDistanceUnits", "FootDistanceUnits"),
    "DesignIntentTypes": ("PartDesignIntentType", "AssemblyDesignIntentType", "HybridDesignIntentType"),
    "FeatureHealthStates": ("HealthyFeatureHealthState", "WarningFeatureHealthState", "ErrorFeatureHealthState"),
}
for _n, _m in _ENUMS.items():
    if not hasattr(fusion, _n) and not hasattr(core, _n):
        setattr(fusion, _n, _enum(_n, *_m))
def _ensure_static(module, cls_name, method, fn):
    """Make sure ``module.cls_name.method`` exists (other fakes may define the class without it)."""
    cls = getattr(module, cls_name, None)
    if cls is None:
        cls = type(cls_name, (), {})
        setattr(module, cls_name, cls)
    if not hasattr(cls, method):
        setattr(cls, method, staticmethod(fn))


for _n in ("DistanceExtentDefinition", "ToEntityExtentDefinition"):
    _ensure_static(fusion, _n, "create", lambda *a, _n=_n: (_n,) + a)
_ensure_static(fusion, "UserCoordinateSystemGeometry", "createByPoint", lambda p: ("UCSGeometry", p))
_ensure_static(core, "Color", "create", lambda r, g, b, a: ("Color", r, g, b, a))


def _patch_core():
    """Re-applied per Env: other fake modules replace ``core.SurfaceTypes`` at import."""
    for i, n in enumerate(("PlaneSurfaceType", "CylinderSurfaceType", "ConeSurfaceType", "SphereSurfaceType",
                           "TorusSurfaceType")):
        if not hasattr(core.SurfaceTypes, n):
            setattr(core.SurfaceTypes, n, i)


def _rec_matrix():
    """A ``Matrix3D`` that records ``setToRotation`` / ``transformBy`` in ``ops``, whatever the shared fake holds."""
    base = core.Matrix3D

    class RecMatrix(base):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.ops = []

        @staticmethod
        def create():
            return RecMatrix()

        def setToRotation(self, *args):
            self.ops.append(("setToRotation",) + args)
            return True

        def transformBy(self, *args):
            self.ops.append(("transformBy",) + args)
            return True

    return RecMatrix


_patch_core()


class Geo:
    """A face/edge geometry stand-in with free attributes."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def plane_geo():
    return Geo(surfaceType=core.SurfaceTypes.PlaneSurfaceType)


def cyl_geo():
    return Geo(surfaceType=core.SurfaceTypes.CylinderSurfaceType)


def _unwrap(v):
    if isinstance(v, core.ValueInput):
        return v.value
    if isinstance(v, core.ObjectCollection):
        return list(v)
    if isinstance(v, (list, tuple)):
        return type(v)(_unwrap(i) for i in v)
    return v


class Inp:
    """Recording input object. ``log`` holds ``(method, *args)`` and ``("=", attr, value)`` tuples."""

    def __init__(self, kind, spec, hidden=()):
        d = self.__dict__
        d["_kind"], d["_methods"], d["log"], d["_hidden"] = kind, set(spec.get("methods", ())), [], set(hidden)
        for a, nested in spec.get("attrs", {}).items():
            if a not in self._hidden:
                d[a] = Inp(a, nested) if isinstance(nested, dict) else nested

    def __getattr__(self, n):
        if n in self.__dict__["_methods"] and n not in self._hidden:
            def method(*a):
                self.log.append((n,) + tuple(_unwrap(x) for x in a))
                return None
            return method
        raise AttributeError(n)

    def __setattr__(self, n, v):
        self.log.append(("=", n, _unwrap(v)))
        self.__dict__[n] = v

    def calls(self, name):
        return [e[1:] for e in self.log if e[0] == name]

    def sets(self, name):
        return [e[2] for e in self.log if e[0] == "=" and e[1] == name]


def S(methods=(), **attrs):
    return {"methods": tuple(methods), "attrs": attrs}


_PB = {"participantBodies": None}
CREATORS = {
    "extrudeFeatures": {"createInput": S(("setOneSideExtent", "setSymmetricExtent", "setTwoSidesExtent"), **_PB)},
    "revolveFeatures": {"createInput": S(("setAngleExtent", "setTwoSideAngleExtent"), **_PB)},
    "sweepFeatures": {"createInput": S((), **_PB)},
    "loftFeatures": {"createInput": S((), loftSections=S(("add",)), centerLineOrRails=S(("addRail", "addCenterLine")), **_PB)},
    "filletFeatures": {"createInput": S(("addConstantRadiusEdgeSet",), edgeSetInputs=S(("addConstantRadiusEdgeSet",)))},
    "chamferFeatures": {
        "createInput2": S((), chamferEdgeSets=S(("addEqualDistanceChamferEdgeSet", "addTwoDistancesChamferEdgeSet",
                                                 "addDistanceAndAngleChamferEdgeSet"))),
        "createInput": S(("setToEqualDistance", "setToTwoDistances", "setToDistanceAndAngle")),
    },
    "shellFeatures": {"createInput": S()},
    "mirrorFeatures": {"createInput": S()},
    "holeFeatures": {k: S(("setPositionByPoint", "setPositionBySketchPoints", "setDistanceExtent", "setAllExtent"), **_PB)
                     for k in ("createSimpleInput", "createCounterboreInput", "createCountersinkInput")},
    "rectangularPatternFeatures": {"createInput": S(("setDirectionTwo",))},
    "circularPatternFeatures": {"createInput": S()},
    "draftFeatures": {"createInput": S(("setSingleAngle", "setTwoAngles"))},
    "splitBodyFeatures": {"createInput": S()},
    "splitFaceFeatures": {"createInput": S()},
    "offsetFacesFeatures": {"createInput": S()},
    "scaleFeatures": {"createInput": S(("setToNonUniform",))},
    "moveFeatures": {"createInput2": S(("defineAsFreeMove",)), "createInput": S()},
    "combineFeatures": {"createInput": S()},
    "removeFeatures": {},
    "patchFeatures": {"createInput": S()},
    "stitchFeatures": {"createInput": S()},
    "thickenFeatures": {"createInput": S()},
    "ruledSurfaceFeatures": {"createInput": S()},
    "trimFeatures": {"createInput": S(bRepCells=None)},
    "flangeFeatures": {"createBaseFlangeInput": S(), "createEdgeFlangeInput": S()},
    "foldFeatures": {"createInput": S(bendLines=S(("add",)))},
    "unfoldFeatures": {"createInput": S()},
    "coilFeatures": {},
    "constructionPlanes": {"createInput": S(("setByOffset", "setByAngle", "setByTwoPlanes", "setByThreePoints",
                                            "setByTangent", "setByDistanceOnPath"))},
    "constructionAxes": {"createInput": S(("setByTwoPlanes", "setByTwoPoints", "setByEdge", "setByCircularFace",
                                          "setByNormalToFaceAtPoint"))},
    "threadFeatures": {"createThreadInfo": S(isRightHanded=None), "createInput": S()},
}


class FakeFeature(fusion.Feature):
    def __init__(self, name, component):
        super().__init__(name, component)
        self.bodies = []
        self.healthState = fusion.FeatureHealthStates.HealthyFeatureHealthState
        self.kind = None


class ThreadQuery:
    """Fake ThreadDataQuery: ``recommend`` is the ``(ok, designation, class)`` answer."""

    def __init__(self):
        self.allThreadTypes = ["ANSI Metric M Profile", "ISO Metric profile"]
        self.sizes = {"ISO Metric profile": ["1", "3", "5"]}
        self.designations = {("ISO Metric profile", "5"): ["M5x0.8"]}
        self.classes = {"M5x0.8": ["6H", "6g"]}
        self.recommend = (True, "M5x0.8", "6H")
        self.calls = []

    def allSizes(self, thread_type):
        self.calls.append(("allSizes", thread_type))
        return self.sizes[thread_type]

    def allDesignations(self, thread_type, size):
        self.calls.append(("allDesignations", thread_type, size))
        return self.designations[(thread_type, size)]

    def allClasses(self, internal, thread_type, designation):
        self.calls.append(("allClasses", internal, thread_type, designation))
        return self.classes[designation]

    def recommendThreadData(self, diameter, internal, thread_type):
        self.calls.append(("recommendThreadData", diameter, internal, thread_type))
        return self.recommend


class FeatColl:
    """A feature collection: ``createX`` calls return recording ``Inp`` objects; ``add`` returns a feature."""

    def __init__(self, comp, kind, creators):
        self._comp, self._kind, self._creators = comp, kind, dict(creators)
        self._hidden, self._hidden_input = set(), set()
        self.created, self.added, self.add_error, self.next_bodies = [], [], None, []
        self.features = []
        if kind == "threadFeatures":
            self.threadDataQuery = ThreadQuery()

    def hide(self, *names):
        self._hidden.update(names)

    def hide_input(self, *attrs):
        self._hidden_input.update(attrs)

    def __getattr__(self, n):
        if n in self.__dict__.get("_creators", ()) and n not in self._hidden:
            def create(*a):
                inp = Inp(n, self._creators[n], self._hidden_input)
                inp.__dict__["args"] = tuple(_unwrap(x) for x in a)
                self.created.append(inp)
                return inp
            return create
        raise AttributeError(n)

    def add(self, arg):
        if self.add_error is not None:
            raise self.add_error
        self.added.append(arg)
        feat = self._comp.add_feature(self._kind, FakeFeature)
        feat.kind = self._kind
        feat.bodies = list(self.next_bodies)
        self.features.append(feat)
        return feat

    @property
    def count(self):
        return len(self.features)

    def item(self, i):
        return self.features[i]


class Features:
    def __init__(self, comp):
        for kind, creators in CREATORS.items():
            setattr(self, kind, FeatColl(comp, kind, creators))
        self.created_paths = []

    def createPath(self, items, chain=True):
        p = ("Path", list(items), chain)
        self.created_paths.append(p)
        return p

    def hide(self, *names):
        for n in names:
            delattr(self, n)


class SharedColl(core.Collection):
    """A collection over a list shared with its owner (so ``add`` shows through)."""

    def __init__(self, shared):
        super().__init__()
        self._items = shared


class Circles:
    def __init__(self, sketch):
        self._sketch, self.made = sketch, []

    def addByCenterRadius(self, center, radius):
        c = self._sketch.add_circle()
        c.center, c.radius = center, radius
        self.made.append(c)
        return c


class Arcs:
    def __init__(self):
        self.made = []

    def addByCenterStartSweep(self, center, start, sweep):
        a = Geo(center=center, start=start, sweep=sweep, startSketchPoint=Geo(), endSketchPoint=Geo())
        self.made.append(a)
        return a


class Lines:
    def __init__(self):
        self.made = []

    def addByTwoPoints(self, a, b):
        ln = Geo(start=a, end=b, isConstruction=False)
        self.made.append(ln)
        return ln


class MSketch(fusion.Sketch):
    """A sketch created by ``comp.sketches.add(plane)``; ``deleteMe`` records and may fail.

    ``profiles`` are derived from the curves: one per circle, plus one for arcs closed by a
    non-construction line."""

    def __init__(self, name, component, plane):
        super().__init__(name, component)
        self.plane, self.deleted, self.delete_error = plane, False, None
        self._made = {}
        self.sketchCurves = Geo(sketchCircles=Circles(self), sketchArcs=Arcs(), sketchLines=Lines())

    @property
    def profiles(self):
        c = self.sketchCurves
        keys = [id(x) for x in c.sketchCircles.made]
        if c.sketchArcs.made and any(not ln.isConstruction for ln in c.sketchLines.made):
            keys.append("arc")
        out = []
        for k in keys:
            if k not in self._made:
                p = self.add_profile()
                p.parentSketch = self
                self._made[k] = p
            out.append(self._made[k])
        return core.Collection(out)

    def deleteMe(self):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted = True
        return True


class SketchColl(SharedColl):
    def __init__(self, comp):
        super().__init__(comp._sketches)
        self._comp = comp

    def add(self, plane):
        sk = MSketch("Sketch%d" % (len(self._items) + 1), self._comp, plane)
        self._items.append(sk)
        return self._comp._design._register(sk, timeline=True)


class CoilFeature(FakeFeature):
    """Stands in for ``adsk.fusion.CoilFeature``; ``participantBodies`` is a class member (feature-detected)."""

    def _set_pb(self, v):
        self._log.append(("participantBodies", v))
        self._pb = v

    participantBodies = property(lambda s: s._pb, _set_pb)


fusion.CoilFeature = CoilFeature


class CoilFeatures:
    """``count`` grows only when the fake app commits a Coil command (see ``Env``)."""

    def __init__(self, comp):
        self._comp, self._items = comp, []

    count = property(lambda s: len(s._items))

    def item(self, i):
        return self._items[i]

    def commit(self, log):
        feat = self._comp.add_feature("Coil%d" % (len(self._items) + 1), CoilFeature)
        feat._log, feat._pb = log, None
        feat.timelineObject.rollTo = lambda before: log.append(("rollTo", before))
        self._items.append(feat)
        return feat


class MOccurrence(fusion.Occurrence):
    def activate(self):
        d = self.parentComponent._design
        d.activeComponent, d.activeOccurrence = self.component, self
        return True


MOccurrence.__name__ = "Occurrence"  # objectType / Ref type come from the class name


class MComponent(fusion.Component):
    features = property(lambda s: s._model_features)  # replaces the shared fake's feature collection
    sketches = property(lambda s: SketchColl(s))
    constructionPlanes = property(lambda s: s._model_features.constructionPlanes)
    constructionAxes = property(lambda s: s._model_features.constructionAxes)

    def __init__(self, name, design):
        super().__init__(name, design)
        self._model_features = Features(self)
        self._model_features.coilFeatures = CoilFeatures(self)
        self.originConstructionPoint = design._make(fusion.ConstructionPoint, "Origin", self)

    def createBRepEdgeProfile(self, edges, chain=True):
        p = ("EdgeProfile", list(edges), chain)
        self._model_features.created_paths.append(p)
        return p

    def add_occurrence(self, name, component_name=None):
        comp = MComponent(component_name or name + " comp", self._design)
        self._design._subcomponents.append(comp)
        o = MOccurrence(name, comp, self)
        self._occ.append(o)
        self._design._all_occ.append(o)
        return self._design._register(o)


MComponent.__name__ = "Component"


_UNIT_CLASS = {"mm": "cm", "cm": "cm", "m": "cm", "in": "cm", "ft": "cm", "deg": "rad", "rad": "rad"}


class UserParams(core.Collection):
    """``design.userParameters``: iterable, ``itemByName`` and the real ``add(name, input, unit, comment)``."""

    def __init__(self, design):
        self._design = design

    @property
    def _items(self):
        return [p for p in self._design._param_objs if isinstance(p, fusion.UserParameter)]

    def add(self, name, value_input, unit, comment):
        um = self._design.unitsManager
        cm = um.evaluateExpression(value_input.value, _UNIT_CLASS.get(unit, ""))
        p = self._design.add_parameter(name, cm, user=True)
        p.expression, p.unit, p.comment = value_input.value, unit, comment
        return p


def _param_delete(self):
    return getattr(self, "delete_ok", True)


fusion.Parameter.deleteMe = _param_delete
fusion.Parameter.isFavorite = False


class Prop(Geo):
    pass


class ColorProp(Prop):
    pass


class Appearance(Geo):
    def __init__(self, name, props=None):
        super().__init__(name=name, id="id-" + name, appearanceProperties=NamedColl(props or []))


class NamedColl(core.Collection):
    """Name/id addressable collection; ``addByCopy`` (appearances) clones the source under a new name."""

    def __init__(self, items=()):
        self._items = list(items)

    def itemById(self, i):
        return next((x for x in self._items if x.id == i), None)

    def addByCopy(self, src, name):
        copy = Appearance(name, [type(p)(id=p.id, name=p.id, value=p.value) for p in src.appearanceProperties])
        self._items.append(copy)
        return copy


core.ColorProperty = ColorProp
if not hasattr(core.Color, "create") or not isinstance(core.Color.__dict__.get("create"), staticmethod):
    core.Color.create = staticmethod(lambda r, g, b, a=255: ("Color", r, g, b, a))


def material(name):
    return Geo(name=name, id="mat-" + name)


def library(name, materials=(), appearances=()):
    return Geo(name=name, materials=NamedColl(materials), appearances=NamedColl(appearances))


class MDesign(fusion.Design):
    userParameters = property(lambda s: UserParams(s))

    def __init__(self, parametric=True):
        super().__init__(parametric)
        self._all_occ = []
        self.activeOccurrence = None
        self.rootComponent = self.activeComponent = MComponent("Root", self)
        self.materials, self.appearances = NamedColl(), NamedColl()

    def activateRootComponent(self):
        self.activeComponent, self.activeOccurrence = self.rootComponent, None
        return True


def _allOccurrencesByComponent(self, comp):
    return core.Collection([o for o in self._design._all_occ if o.component.entityToken == comp.entityToken])


MComponent.allOccurrencesByComponent = _allOccurrencesByComponent


class Env:
    """A fake application + design + Ctx with every model action allowed."""

    def __init__(self, parametric=True):
        importlib.import_module("defusion.tools.model")  # registers every model action
        units.set_input_defaults("mm", "deg")
        _patch_core()
        core.reset_calls()
        self.app = core.Application.reset()
        self.app.materialLibraries = NamedColl()
        self.design = MDesign(parametric)
        self.app.activeDocument = core.Document("Doc", {"DesignProductType": self.design})
        self.comp = self.design.rootComponent
        allowed = {a.key for a in actions.registry()}
        self.ctx = Ctx.for_app(self.app, allowed, ["all"], OutputUnits())
        self.features = self.comp.features
        self._fake_ui_and_coil_command()

    def _fake_ui_and_coil_command(self):
        """Selections, ``terminateActiveCommand`` and a Coil command that commits on ``NuCommands.CommitCmd``."""
        ui, app = self.app.userInterface, self.app
        self.log, self.terminated, self.coil_started, self.start_selection = [], 0, False, None
        self.commit_makes_coil = True
        ui.activeSelections = Geo(items=[], clear=lambda: ui.activeSelections.items.clear(),
                                  add=lambda e: ui.activeSelections.items.append(e))
        ui.terminateActiveCommand = self._terminate
        original = app.executeTextCommand

        def text(cmd):
            out = original(cmd)
            if cmd == "Commands.Start Coil":
                self.coil_started = True
                self.start_selection = list(ui.activeSelections.items)
            elif cmd == "NuCommands.CommitCmd" and self.coil_started and self.commit_makes_coil:
                self.design.activeComponent.features.coilFeatures.commit(self.log)
            return out

        app.executeTextCommand = text
        if self.design._timeline is not None:
            self.design._timeline.moveToEnd = lambda: self.log.append(("moveToEnd",))

    def _terminate(self):
        self.terminated += 1

    def last(self, kind):
        return self.coll(kind).created[-1]

    def coll(self, kind):
        return getattr(self.comp.features, kind)

    def call(self, action, /, **params):
        return actions.call_inline(self.ctx, "model", action, params)

    def err(self, action, /, **params):
        with pytest.raises(ActionError) as e:
            self.call(action, **params)
        return e.value

    @staticmethod
    def r(entity):
        return {"token": entity.entityToken}

    def profile(self, sketch, index=None):
        out = {"sketch": self.r(sketch)}
        if index is not None:
            out["index"] = index
        return out

    def feature(self, name="F", comp=None):
        f = (comp or self.comp).add_feature(name, FakeFeature)
        f.__dict__.setdefault("timelineObject", object())  # a direct design has no timeline to register in
        return f

    def new_profile(self, comp=None):
        sk = (comp or self.comp).add_sketch("S%d" % len((comp or self.comp)._sketches))
        p = sk.add_profile()
        p.parentSketch = sk
        return sk, p

    def body(self, name="B", comp=None, faces=0, edges=0, solid=True):
        b = (comp or self.comp).add_body(name)
        b.isSolid = solid
        for _ in range(faces):
            b.add_face(plane_geo())
        for _ in range(edges):
            b.add_edge()
        return b


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(core, "Matrix3D", _rec_matrix())
    e = Env()
    yield e
    core.Application.reset()


@pytest.fixture
def env_direct(monkeypatch):
    monkeypatch.setattr(core, "Matrix3D", _rec_matrix())
    e = Env(parametric=False)
    yield e
    core.Application.reset()
