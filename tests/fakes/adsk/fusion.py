"""Fake ``adsk.fusion``: a design with components and the entity kinds core resolves."""
from types import SimpleNamespace

from . import core
from .core import BoundingBox3D, Collection, Entity, Point3D, UnitsManager, _Fake


class DesignTypes:
    DirectDesignType = 0
    ParametricDesignType = 1


class DesignIntentTypes:
    PartDesignIntentType = 0
    AssemblyDesignIntentType = 1
    HybridDesignIntentType = 2


class CalculationAccuracy:
    LowCalculationAccuracy = 0
    MediumCalculationAccuracy = 1
    HighCalculationAccuracy = 2
    VeryHighCalculationAccuracy = 3


class PhysicalProperties(_Fake):
    """Preset physical properties; moments and principal values are kg*cm^2, axes are Vector3D."""

    def __init__(self, mass, volume, area, centerOfMass, moments=(0, 0, 0, 0, 0, 0),
                 principal=(0, 0, 0), axes=None):
        self.mass, self.volume, self.area = mass, volume, area
        self.density = mass / volume if volume else 0.0
        self.centerOfMass = centerOfMass
        self._moments, self._principal = moments, principal
        self._axes = axes or (core.Vector3D(1, 0, 0), core.Vector3D(0, 1, 0), core.Vector3D(0, 0, 1))

    def getXYZMomentsOfInertia(self):
        return (True,) + tuple(self._moments)

    def getPrincipalMomentsOfInertia(self):
        return (True,) + tuple(self._principal)

    def getPrincipalAxes(self):
        return (True,) + tuple(self._axes)


class FeatureOperations:
    JoinFeatureOperation = 0
    CutFeatureOperation = 1
    IntersectFeatureOperation = 2
    NewBodyFeatureOperation = 3
    NewComponentFeatureOperation = 4


class BRepVertex(Entity):
    def __init__(self, body):
        super().__init__(None)
        self.body = body


class BRepEdge(Entity):
    def __init__(self, body, geometry=None):
        super().__init__(None)
        self.body, self.geometry = body, geometry


class BRepFace(Entity):
    def __init__(self, body, geometry=None):
        super().__init__(None)
        self.body, self.geometry = body, geometry


class BRepBody(Entity):
    def __init__(self, name, component, bbox=((0, 0, 0), (1, 1, 1)), visible=True):
        super().__init__(name)
        self.parentComponent = component
        self._faces, self._edges, self._vertices = [], [], []
        self._bbox = bbox
        self._bbox_error = False
        self.isVisible = visible

    faces = property(lambda s: Collection(s._faces))
    edges = property(lambda s: Collection(s._edges))
    vertices = property(lambda s: Collection(s._vertices))

    def moveToComponent(self, occurrence):
        """Move into the occurrence's component; returns the moved body (new token)."""
        self.parentComponent._bodies.remove(self)
        return occurrence.component.add_body(self.name, bbox=self._bbox, visible=self.isVisible)

    @property
    def boundingBox(self):
        if self._bbox_error:
            raise RuntimeError("bbox failed")
        lo, hi = self._bbox
        return BoundingBox3D(Point3D(*lo), Point3D(*hi))

    def add_face(self, geometry=None):
        f = BRepFace(self, geometry)
        self._faces.append(f)
        self.parentComponent._design._register(f)
        return f

    def add_edge(self, geometry=None):
        e = BRepEdge(self, geometry)
        self._edges.append(e)
        self.parentComponent._design._register(e)
        return e

    def add_vertex(self):
        v = BRepVertex(self)
        self._vertices.append(v)
        self.parentComponent._design._register(v)
        return v


class MeshBody(Entity):
    def __init__(self, name, component):
        super().__init__(name)
        self.parentComponent = component


class ProfileCurve(_Fake):
    def __init__(self, geometry, geometry_type):
        self.geometry, self.geometryType = geometry, geometry_type


class ProfileLoop(_Fake):
    def __init__(self, curves=()):
        self.profileCurves = Collection(curves)


class _AreaProperties(_Fake):
    def __init__(self, area, centroid):
        self.area, self.centroid = area, centroid


class Profile(Entity):
    profileLoops = Collection()  # tests assign per-instance

    def __init__(self, area=1.0, centroid=(0, 0, 0)):
        super().__init__(None)
        self.parentSketch = None
        self.area, self.centroid = area, centroid

    def areaProperties(self):
        return _AreaProperties(self.area, Point3D(*self.centroid))


class SketchCurve(Entity):
    def __init__(self, sketch):
        super().__init__(None)
        self.parentSketch = sketch


class SketchLine(SketchCurve):
    geometry = None  # tests set a Line3D (sketch space)


class SketchCircle(SketchCurve):
    pass


class SketchPoint(Entity):
    def __init__(self, sketch):
        super().__init__(None)
        self.parentSketch = sketch


class _SketchCurves(_Fake):
    def __init__(self, sketch):
        self._sketch = sketch

    sketchLines = property(lambda s: Collection(s._sketch._lines))
    count = property(lambda s: len(s._sketch._lines))


class Sketch(Entity):
    """``origin`` is the sketch plane origin (cm); axes are aligned with model axes."""

    def __init__(self, name, component, origin=(0, 0, 0)):
        super().__init__(name)
        self.parentComponent = component
        self._origin = origin
        self._profiles = []
        self._lines = []
        self.sketchCurves = _SketchCurves(self)

    profiles = property(lambda s: Collection(s._profiles))

    def sketchToModelSpace(self, p):
        o = self._origin
        return Point3D(p.x + o[0], p.y + o[1], p.z + o[2])

    def modelToSketchSpace(self, p):
        o = self._origin
        return Point3D(p.x - o[0], p.y - o[1], p.z - o[2])

    def add_profile(self, **kw):
        p = Profile(**kw)
        p.parentSketch = self
        self._profiles.append(p)
        self.parentComponent._design._register(p)
        return p

    def add_line(self):
        c = SketchLine(self)
        self._lines.append(c)
        self.parentComponent._design._register(c)
        return c

    def add_circle(self):
        c = SketchCircle(self)
        self.parentComponent._design._register(c)
        return c

    def add_point(self):
        p = SketchPoint(self)
        self.parentComponent._design._register(p)
        return p


class Feature(Entity):
    def __init__(self, name, component):
        super().__init__(name)
        self.parentComponent = component


class ExtrudeFeature(Feature):
    pass


class RevolveFeature(Feature):
    axis = None  # tests set a ConstructionAxis, BRepEdge or SketchLine


class ConstructionPlane(Entity):
    def __init__(self, name, component):
        super().__init__(name)
        self.component = component  # real planes/axes expose `.component` only


class ConstructionAxis(Entity):
    def __init__(self, name, component):
        super().__init__(name)
        self.component = component  # real planes/axes expose `.component` only


class ConstructionPoint(Entity):
    def __init__(self, name, component):
        super().__init__(name)
        self.parentComponent = component


class Joint(Entity):
    def __init__(self, name, component):
        super().__init__(name)
        self.parentComponent = component


class JointOrigin(Entity):
    def __init__(self, name, component):
        super().__init__(name)
        self.parentComponent = component


class RigidGroup(Entity):
    def __init__(self, name, component):
        super().__init__(name)
        self.parentComponent = component


class Parameter(Entity):
    """``expression`` defaults to the value; ``role``, ``createdBy``, ``unit`` and ``comment`` are
    test-set. ``dependencyParameters`` is absent unless a test sets it (like older Fusion)."""

    role = ""
    createdBy = None
    unit = ""
    comment = ""

    def __init__(self, name, value, expression=None):
        super().__init__(name)
        self.value = value
        self.expression = expression if expression is not None else "%g" % value


class ModelParameter(Parameter):
    pass


class UserParameter(Parameter):
    pass


class ContactSet(Entity):
    pass


class Occurrence(Entity):
    def __init__(self, name, component, parent):
        super().__init__(name)
        self.component = component
        self.parentComponent = parent
        self._path = None
        self.isGrounded = False
        self.isLightBulbOn = True
        self.isVisible = True
        self.transform2 = core.Matrix3D()

    @property
    def fullPathName(self):
        return self._path or self.name


class FeatureHealthStates:
    HealthyFeatureHealthState = 0
    WarningFeatureHealthState = 1
    ErrorFeatureHealthState = 2
    UnknownFeatureHealthState = 3


class TimelineObject(Entity):
    """Timeline item. ``name`` writes through to the entity; writes to an item inside an expanded
    group raise (like Fusion)."""

    def __init__(self, entity, index):
        self.entity = entity
        self.parentGroup = None
        self.timeline = None
        self._name = None
        self._suppressed = False
        self._deps = []
        self.isGroup = False
        self.healthState = FeatureHealthStates.HealthyFeatureHealthState
        self.errorOrWarningMessage = ""
        super().__init__(entity.name if entity is not None else None)
        self.index = index

    def _guard(self):
        g = self.parentGroup
        if g is not None and not g.isCollapsed:
            raise RuntimeError("item is inside an expanded timeline group")

    @property
    def name(self):
        return self.entity.name if self.entity is not None else self._name

    @name.setter
    def name(self, v):
        if self.entity is None:
            self._name = v
        elif v != self.entity.name:
            self._guard()
            self.entity.name = v

    @property
    def isSuppressed(self):
        return self._suppressed

    @isSuppressed.setter
    def isSuppressed(self, v):
        self._guard()
        self._suppressed = v

    @property
    def isRolledBack(self):
        return self.timeline is not None and self.index >= self.timeline.markerPosition

    def rollTo(self, rollBefore):
        self._guard()
        self.timeline.markerPosition = self.index if rollBefore else self.index + 1
        return True

    def _order_after(self, target):
        items = [o for o in self.timeline._items if o is not self]
        pos = len(items) if target == -1 else target - (1 if target > self.index else 0)
        items.insert(pos, self)
        return items

    def canReorder(self, target):
        items = self._order_after(target)
        pos = {id(o): i for i, o in enumerate(items)}
        return all(pos[id(d)] < pos[id(o)] for o in items for d in o._deps)

    def reorder(self, target):
        self._guard()
        if getattr(self.timeline, "reorder_noop", False):  # live: reorder can silently leave the item in place
            return True
        items = self._order_after(target)
        self.timeline._items[:] = items
        for i, o in enumerate(items):
            o.index = i
        return True


class TimelineGroup(TimelineObject):
    def __init__(self, index, members, timeline):
        super().__init__(None, index)
        self.isGroup = True
        self.isCollapsed = True
        self.timeline = timeline
        self._members = list(members)
        for m in members:
            m.parentGroup = self

    @property
    def count(self):
        return len(self._members)

    def item(self, i):
        return self._members[i]

    def deleteMe(self, deleteContents=False):
        tl = self.timeline
        for m in self._members:
            m.parentGroup = None
        if deleteContents:
            tl._items[:] = [o for o in tl._items if o not in self._members]
            for i, o in enumerate(tl._items):
                o.index = i
        tl._groups.remove(self)
        return True


class TimelineGroups(_Fake):
    def __init__(self, timeline):
        self._tl = timeline

    @property
    def count(self):
        return len(self._tl._groups)

    def item(self, i):
        return self._tl._groups[i]

    def add(self, start, end):
        tl = self._tl
        if not tl._can_group or not 0 <= start <= end < len(tl._items):
            return None
        g = TimelineGroup(start, tl._items[start:end + 1], tl)
        tl._groups.append(g)
        return g

    def __iter__(self):
        return iter(list(self._tl._groups))


class Timeline(_Fake):
    """``_marker`` None means the marker is at the end; ``_ignore_marker_set`` makes the setter a
    no-op; ``_has_groups`` False hides ``timelineGroups``."""

    def __init__(self):
        self._items = []
        self._groups = []
        self._marker = None
        self._ignore_marker_set = False
        self._has_groups = True
        self._can_group = True

    @property
    def count(self):
        return len(self._items)

    @property
    def markerPosition(self):
        return len(self._items) if self._marker is None else min(self._marker, len(self._items))

    @markerPosition.setter
    def markerPosition(self, v):
        if self._ignore_marker_set:
            return
        if not 0 <= v <= len(self._items):
            raise RuntimeError("marker position out of range")
        self._marker = None if v == len(self._items) else v

    @property
    def timelineGroups(self):
        if not self._has_groups:
            raise AttributeError("timelineGroups")
        return TimelineGroups(self)

    def item(self, i):
        if not 0 <= i < len(self._items):
            raise RuntimeError("timeline index out of range")
        return self._items[i]

    def moveToBeginning(self):
        self.markerPosition = 0
        return True

    def moveToEnd(self):
        self.markerPosition = len(self._items)
        return True

    def movetoNextStep(self):
        if self.markerPosition >= len(self._items):
            return False
        self.markerPosition = self.markerPosition + 1
        return True

    def moveToPreviousStep(self):
        if self.markerPosition <= 0:
            return False
        self.markerPosition = self.markerPosition - 1
        return True

    def deleteAllAfterMarker(self):
        del self._items[self.markerPosition:]
        self._marker = None
        return True

    def __iter__(self):
        return iter(list(self._items))


class Snapshot(Entity):
    """Fusion's Snapshot has no ``entityToken``."""

    def __init__(self, snapshots, name, timeline_object):
        super().__init__(name)
        del self.entityToken
        self._snaps = snapshots
        self.timelineObject = timeline_object

    def deleteMe(self):
        self._snaps._items.remove(self)
        return True


class Snapshots(Collection):
    """``hasPendingSnapshot`` is test-controlled (set True to simulate an uncaptured move)."""

    def __init__(self):
        super().__init__()
        self.hasPendingSnapshot = False
        self.reverted = 0
        self._timeline = None

    def add(self):
        tl = self._timeline
        to = tl._items[-1] if tl is not None and tl._items else None
        s = Snapshot(self, "Snapshot%d" % (len(self._items) + 1), to)
        self._items.append(s)
        self.hasPendingSnapshot = False
        return s

    def revertPendingSnapshot(self):
        self.hasPendingSnapshot = False
        self.reverted += 1
        return True


class Component(Entity):
    def __init__(self, name, design):
        super().__init__(name)
        self._design = design
        self._bodies, self._mesh, self._sketches = [], [], []
        self._planes, self._axes, self._points = [], [], []
        self._occ, self._joints, self._jo, self._rg, self._features = [], [], [], [], []
        self._abj = []
        self.partNumber = name
        design._register(self)
        self.originConstructionPoint = design._make(ConstructionPoint, "Origin", self)
        self.xYConstructionPlane = design._make(ConstructionPlane, "XY", self)
        self.xZConstructionPlane = design._make(ConstructionPlane, "XZ", self)
        self.yZConstructionPlane = design._make(ConstructionPlane, "YZ", self)
        self.xConstructionAxis = design._make(ConstructionAxis, "X", self)
        self.yConstructionAxis = design._make(ConstructionAxis, "Y", self)
        self.zConstructionAxis = design._make(ConstructionAxis, "Z", self)

    bRepBodies = property(lambda s: Collection(s._bodies))
    meshBodies = property(lambda s: Collection(s._mesh))
    sketches = property(lambda s: Collection(s._sketches))
    constructionPlanes = property(lambda s: Collection(s._planes))
    constructionAxes = property(lambda s: Collection(s._axes))
    constructionPoints = property(lambda s: Collection(s._points))
    occurrences = property(lambda s: Occurrences(s, "_occ"))
    joints = property(lambda s: Joints(s, "_joints"))
    asBuiltJoints = property(lambda s: AsBuiltJoints(s, "_abj"))
    jointOrigins = property(lambda s: Collection(s._jo))
    rigidGroups = property(lambda s: RigidGroups(s, "_rg"))

    @property
    def allOccurrences(self):
        out = []
        for o in self._occ:
            out.append(o)
            out.extend(o.component.allOccurrences)
        return Collection(out)

    def allOccurrencesByComponent(self, component):
        return Collection([o for o in self.allOccurrences if o.component is component])
    features = property(lambda s: Collection(s._features))
    bodies = bRepBodies  # tests/fakes convenience: real API names it bRepBodies

    # ---- builders used by tests -------------------------------------------
    def add_body(self, name, **kw):
        b = BRepBody(name, self, **kw)
        self._bodies.append(b)
        return self._design._register(b)

    def add_mesh_body(self, name):
        m = MeshBody(name, self)
        self._mesh.append(m)
        return self._design._register(m)

    def add_sketch(self, name, origin=(0, 0, 0)):
        s = Sketch(name, self, origin)
        self._sketches.append(s)
        return self._design._register(s, timeline=True)

    def add_feature(self, name, cls=None):
        f = (cls or ExtrudeFeature)(name, self)
        self._features.append(f)
        return self._design._register(f, timeline=True)

    def add_construction_plane(self, name):
        p = ConstructionPlane(name, self)
        self._planes.append(p)
        return self._design._register(p, timeline=True)

    def add_construction_axis(self, name):
        a = ConstructionAxis(name, self)
        self._axes.append(a)
        return self._design._register(a, timeline=True)

    def add_construction_point(self, name):
        p = ConstructionPoint(name, self)
        self._points.append(p)
        return self._design._register(p, timeline=True)

    def add_joint(self, name):
        j = Joint(name, self)
        self._joints.append(j)
        return self._design._register(j, timeline=True)

    def add_joint_origin(self, name):
        j = JointOrigin(name, self)
        self._jo.append(j)
        return self._design._register(j, timeline=True)

    def add_as_built_joint(self, name):
        j = AsBuiltJoint(name, self)
        self._abj.append(j)
        return self._design._register(j, timeline=True)

    def add_rigid_group(self, name):
        r = RigidGroup(name, self)
        self._rg.append(r)
        return self._design._register(r, timeline=True)

    def add_occurrence(self, name, component_name=None):
        comp = Component(component_name or name + " comp", self._design)
        self._design._subcomponents.append(comp)
        o = Occurrence(name, comp, self)
        self._occ.append(o)
        return self._design._register(o)


class TriangleMeshQualityOptions:
    LowQualityTriangleMesh = 0
    NormalQualityTriangleMesh = 1
    HighQualityTriangleMesh = 2
    VeryHighQualityTriangleMesh = 3


class TriangleMesh(_Fake):
    """Flat xyz coordinates (cm) and flat node indices, like Fusion's ``TriangleMesh``."""

    def __init__(self, coords, indices):
        self.nodeCoordinatesAsDouble, self.nodeIndices = list(coords), list(indices)
        self.nodeCount, self.triangleCount = len(self.nodeCoordinatesAsDouble) // 3, len(self.nodeIndices) // 3


class _MeshCalculator(_Fake):
    def __init__(self, mesh):
        self._mesh, self.quality = mesh, None

    def setQuality(self, q):
        self.quality = q
        return True

    def calculate(self):
        return self._mesh


class MeshManager(_Fake):
    def __init__(self, mesh):
        self.calculator = _MeshCalculator(mesh)

    def createMeshCalculator(self):
        return self.calculator


class InterferenceInput(_Fake):
    def __init__(self, entities):
        self.entities = entities
        self.areCoincidentFacesIncluded = False


class InterferenceResult(_Fake):
    """``createCorrespondingBodies`` must never be called by inspect: it records the call."""

    def __init__(self, entityOne, entityTwo, interferenceBody):
        self.entityOne, self.entityTwo, self.interferenceBody = entityOne, entityTwo, interferenceBody
        self.corresponding_calls = 0

    def createCorrespondingBodies(self, *a):
        self.corresponding_calls += 1


class SectionAnalysis(Entity):
    pass


class SectionAnalyses(_Fake):
    """``createInput`` returns an input with ``isFlipped`` unless ``flip_supported`` is False."""

    def __init__(self, flip_supported=True):
        self._items = []
        self.flip_supported = flip_supported

    @property
    def count(self):
        return len(self._items)

    def createInput(self, plane, offset):
        inp = core.Base()
        inp.plane, inp.offset = plane, offset
        if self.flip_supported:
            inp.isFlipped = False
        return inp

    def add(self, inp):
        a = SectionAnalysis("Analysis%d" % (len(self._items) + 1))
        a.input = inp
        self._items.append(a)
        return a


class Analyses(_Fake):
    def __init__(self, **kw):
        self.sectionAnalyses = SectionAnalyses(**kw)


class Design(_Fake):
    def __init__(self, parametric=True):
        self.designType = (
            DesignTypes.ParametricDesignType if parametric else DesignTypes.DirectDesignType
        )
        self._entities = {}
        self._params = {}
        self._param_objs = []
        self._subcomponents = []
        self._contact_sets = []
        self.isContactAnalysisEnabled = False
        self.isContactSetAnalysis = False
        self.unitsManager = UnitsManager(self)
        self._timeline = Timeline() if parametric else None
        self.rootComponent = Component("Root", self)
        self.activeComponent = self.rootComponent

    def createInterferenceInput(self, entities):
        return InterferenceInput(entities)

    def analyzeInterference(self, inp):
        """Results preset in ``design._interference`` (a list of InterferenceResult)."""
        self._last_interference_input = inp
        return Collection(getattr(self, "_interference", []))

    @property
    def timeline(self):
        if self._timeline is None:
            raise RuntimeError("no timeline in direct design mode")
        return self._timeline

    _has_snapshots = True
    _snap = None

    @property
    def snapshots(self):
        if not self._has_snapshots:
            raise AttributeError("snapshots")
        if self._snap is None:
            self._snap = Snapshots()
            self._snap._timeline = self._timeline
        return self._snap

    def enable_contact_sets(self):
        """Give this design a ``contactSets`` collection (absent by default, like older builds)."""
        self.contactSets = ContactSets(self, "_contact_sets")
        return self.contactSets

    @property
    def allComponents(self):
        return Collection(self._subcomponents)

    @property
    def allParameters(self):
        return Collection(self._param_objs)

    @property
    def userParameters(self):
        return Collection([p for p in self._param_objs if isinstance(p, UserParameter)])

    # ---- internals ---------------------------------------------------------
    def _register(self, e, timeline=False):
        self._entities[e.entityToken] = e
        if timeline and self._timeline is not None:
            to = TimelineObject(e, len(self._timeline._items))
            self._timeline._items.append(to)
            e.__dict__["timelineObject"] = to
            to.timeline = self._timeline
        return e

    def _make(self, cls, *args):
        return self._register(cls(*args))

    def findEntityByToken(self, token):
        e = self._entities.get(token)
        return [e] if e is not None else []

    def add_parameter(self, name, value_cm, user=True):
        p = (UserParameter if user else ModelParameter)(name, value_cm)
        self._params[name] = value_cm
        self._param_objs.append(p)
        return self._register(p)


class ExtrudeFeatureInput(_Fake):
    """Input for ExtrudeFeatures.add."""


class ExtrudeFeatures(_Fake):
    """The collection of extrude features in a component."""

    @property
    def count(self):
        """Number of extrude features."""
        return 0

    def createInput(self, profile, operation):
        """Creates an ExtrudeFeatureInput object for building an extrude.

        Pass the profile to extrude and the feature operation."""
        return ExtrudeFeatureInput()


# ---- assembly: joints, as-built joints, rigid groups, contact sets, occurrences ---------------


class JointTypes:
    RigidJointType = 0
    RevoluteJointType = 1
    SliderJointType = 2
    CylindricalJointType = 3
    PinSlotJointType = 4
    PlanarJointType = 5
    BallJointType = 6


class JointDirections:
    XAxisJointDirection = 0
    YAxisJointDirection = 1
    ZAxisJointDirection = 2


class JointKeyPointTypes:
    CenterKeyPoint = 0
    StartKeyPoint = 1
    MiddleKeyPoint = 2
    EndKeyPoint = 3


class JointGeometry(_Fake):
    """``created`` records every ``createBy*`` call as ``(kind, args)``; ``entity`` is the first arg."""

    created: list = []

    def __init__(self, kind, entity, *args):
        self.kind, self.entity, self.args = kind, entity, args
        JointGeometry.created.append((kind, (entity,) + args))

    @staticmethod
    def createByPlanarFace(face, edge, keyPoint):
        return JointGeometry("planar_face", face, edge, keyPoint)

    @staticmethod
    def createByNonPlanarFace(face, keyPoint):
        return JointGeometry("nonplanar_face", face, keyPoint)

    @staticmethod
    def createByCurve(curve, keyPoint):
        return JointGeometry("curve", curve, keyPoint)

    @staticmethod
    def createByPoint(point):
        return JointGeometry("point", point)


class JointLimits(_Fake):
    def __init__(self):
        self.isMinimumValueEnabled = self.isMaximumValueEnabled = self.isRestValueEnabled = False
        self.minimumValue = self.maximumValue = self.restValue = 0.0


class JointMotion(_Fake):
    def __init__(self, jointType):
        self.jointType = jointType
        self.rotationLimits = JointLimits()
        self.slideLimits = JointLimits()


class _MotionInput(_Fake):
    """Records the motion call as ``motion = (name, *directions)``."""

    def __init__(self):
        self.motion = None

    def setAsRigidJointMotion(self):
        self.motion = ("rigid",)

    def setAsRevoluteJointMotion(self, axis):
        self.motion = ("revolute", axis)

    def setAsSliderJointMotion(self, axis):
        self.motion = ("slider", axis)

    def setAsCylindricalJointMotion(self, axis):
        self.motion = ("cylindrical", axis)

    def setAsPinSlotJointMotion(self, rotation, slide):
        self.motion = ("pin_slot", rotation, slide)

    def setAsPlanarJointMotion(self, normal):
        self.motion = ("planar", normal)

    def setAsBallJointMotion(self, pitch, yaw):
        self.motion = ("ball", pitch, yaw)


_MOTION_TYPES = {
    "rigid": JointTypes.RigidJointType, "revolute": JointTypes.RevoluteJointType,
    "slider": JointTypes.SliderJointType, "cylindrical": JointTypes.CylindricalJointType,
    "pin_slot": JointTypes.PinSlotJointType, "planar": JointTypes.PlanarJointType,
    "ball": JointTypes.BallJointType,
}


class JointInput(_MotionInput):
    def __init__(self, geometry_one, geometry_two):
        super().__init__()
        self.geometry_one, self.geometry_two = geometry_one, geometry_two
        self.offset = self.angle = None
        self.isFlipped = False


class AsBuiltJointInput(_MotionInput):
    def __init__(self, occurrence_one, occurrence_two, geometry):
        super().__init__()
        self.occurrence_one, self.occurrence_two, self.geometry = occurrence_one, occurrence_two, geometry


class AsBuiltJoint(Joint):
    pass


class ValueHolder(_Fake):
    def __init__(self, value):
        self.value = value


class _Live(Collection):
    """A collection that reads its owner's list on every access, so adds show up."""

    def __init__(self, owner, attr):
        self._owner, self._attr = owner, attr

    _items = property(lambda s: getattr(s._owner, s._attr))


def _geo_occurrence(g):
    return getattr(getattr(g, "entity", g), "assemblyContext", None)


def _evaluate(comp, vi, units):
    if vi is None:
        return 0.0
    if vi.kind == "real":
        return vi.value
    return comp._design.unitsManager.evaluateExpression(vi.value, units)


class Joints(_Live):
    def createInput(self, geometry_one, geometry_two):
        return JointInput(geometry_one, geometry_two)

    def add(self, inp):
        comp = self._owner
        j = comp.add_joint("Joint%d" % (len(comp._joints) + 1))
        j.jointMotion = JointMotion(_MOTION_TYPES[inp.motion[0]])
        j.occurrenceOne = _geo_occurrence(inp.geometry_one)
        j.occurrenceTwo = _geo_occurrence(inp.geometry_two)
        j.offset = ValueHolder(_evaluate(comp, inp.offset, "cm"))
        j.angle = ValueHolder(_evaluate(comp, inp.angle, "rad"))
        j.isFlipped = inp.isFlipped
        j.input = inp
        return j


class AsBuiltJoints(_Live):
    def createInput(self, occurrence_one, occurrence_two, geometry):
        return AsBuiltJointInput(occurrence_one, occurrence_two, geometry)

    def add(self, inp):
        comp = self._owner
        j = comp.add_as_built_joint("AsBuiltJoint%d" % (len(comp._abj) + 1))
        j.jointMotion = JointMotion(_MOTION_TYPES[inp.motion[0]])
        j.occurrenceOne, j.occurrenceTwo = inp.occurrence_one, inp.occurrence_two
        j.input = inp
        return j


class RigidGroups(_Live):
    def add(self, occurrences, include_children):
        comp = self._owner
        g = comp.add_rigid_group("RigidGroup%d" % (len(comp._rg) + 1))
        g.occurrences = Collection(list(occurrences))
        g.includeChildren = include_children
        return g


class Occurrences(_Live):
    def addNewComponent(self, transform):
        comp = self._owner
        n = len(comp._design._subcomponents) + 1
        new = Component("Component%d" % n, comp._design)
        comp._design._subcomponents.append(new)
        o = Occurrence("Component%d:1" % n, new, comp)
        o.transform2 = transform
        comp._occ.append(o)
        return comp._design._register(o)


class ContactSets(_Live):
    def add(self, items):
        if not isinstance(items, list):  # Fusion's binding takes a std::vector, not an ObjectCollection
            raise TypeError("in method 'ContactSets_add', argument 2 of type 'std::vector<...>'")
        cs = ContactSet("ContactSet%d" % (len(self._items) + 1))
        cs.parentComponent = self._owner.rootComponent
        cs.isSuppressed = False
        cs.occurrencesAndBodies = Collection(list(items))
        self._items.append(cs)
        return self._owner._register(cs)


# ---- export / mesh import fakes (document tool) --------------------------------------------------
class MeshUnits:
    MillimeterMeshUnit, CentimeterMeshUnit, MeterMeshUnit, InchMeshUnit, FootMeshUnit = range(5)


class MeshRefinementSettings:
    MeshRefinementLow, MeshRefinementMedium, MeshRefinementHigh = range(3)


class ExportOptions(_Fake):
    def __init__(self, kind, filename, geometry):
        self.kind, self.filename, self.geometry = kind, filename, geometry


class MeshExportOptions(ExportOptions):
    """Has the § 8.2 properties; remove one from the class to simulate an older build."""

    meshRefinement = MeshRefinementSettings.MeshRefinementMedium
    isOneFilePerBodyInOccurrence = False
    isBinaryFormat = False
    sendToPrintUtility = True


class ExportManager(_Fake):
    """``create*ExportOptions`` take positional arguments in Fusion's order (``geometry, filename`` for the
    mesh formats, ``filename, geometry`` otherwise; keywords are rejected); ``execute`` writes a small
    file at ``filename`` (or ``<stem>_<n><ext>`` per body with one-file-per-body)."""

    def __init__(self):
        self.calls: list = []
        self.fail = False

    def execute(self, options):
        import pathlib

        self.calls.append(("execute", options))
        if self.fail:
            return False
        path = pathlib.Path(options.filename)
        if getattr(options, "isOneFilePerBodyInOccurrence", False):
            for i, _ in enumerate(options.geometry.bRepBodies):
                path.with_name("%s_%d%s" % (path.stem, i, path.suffix)).write_bytes(b"x")
        else:
            path.write_bytes(b"data")
        return True


def _mk_export(kind, mesh):
    def record(self, filename, geometry):
        self.calls.append(("create_" + kind, filename, geometry))
        return (MeshExportOptions if mesh else ExportOptions)(kind, filename, geometry)

    if mesh:
        return lambda self, geometry, filename: record(self, filename, geometry)
    return lambda self, filename, geometry: record(self, filename, geometry)


for _kind, _name, _mesh in (
    ("f3d", "createFusionArchiveExportOptions", False), ("step", "createSTEPExportOptions", False),
    ("iges", "createIGESExportOptions", False), ("sat", "createSATExportOptions", False),
    ("smt", "createSMTExportOptions", False), ("usdz", "createUSDExportOptions", False),
    ("stl", "createSTLExportOptions", True), ("3mf", "createC3MFExportOptions", True),
    ("obj", "createOBJExportOptions", True),
):
    setattr(ExportManager, _name, _mk_export(_kind, _mesh))


class _MeshBodyList(Collection):
    pass


class MeshBodies(Collection):
    """``add`` creates ``comp.mesh_import['count']`` bodies whose bbox is ``comp.mesh_import['bbox']``."""

    def __init__(self, items, comp):
        super().__init__(items)
        self._comp = comp

    def add(self, path, units, baseOrFormFeature=None):
        comp = self._comp
        comp.__dict__.setdefault("mesh_calls", []).append((path, units, baseOrFormFeature))
        spec = comp.__dict__.get("mesh_import", {"count": 1, "bbox": ((0, 0, 0), (1, 1, 1))})
        out = []
        for i in range(spec["count"]):
            m = comp.add_mesh_body("mesh%d" % i)
            m._bbox = spec["bbox"]
            out.append(m)
        return _MeshBodyList(out)


class BaseFeature(Feature):
    def __init__(self, name, component):
        super().__init__(name, component)
        self.edits: list = []

    def startEdit(self):
        self.edits.append("start")

    def finishEdit(self):
        self.edits.append("finish")


class BaseFeatures(_Fake):
    def __init__(self, comp):
        self._comp = comp

    def add(self):
        return self._comp.add_feature("Base", BaseFeature)


class _Features(_Fake):
    """Proxy over the component's existing ``features`` adding ``baseFeatures``."""

    def __init__(self, inner, comp):
        self._inner, self._comp = inner, comp

    baseFeatures = property(lambda s: BaseFeatures(s._comp))

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _mesh_bbox(self):
    bb = self.__dict__.get("_bbox")
    if bb is None:
        raise AttributeError("boundingBox")
    return BoundingBox3D(Point3D(*bb[0]), Point3D(*bb[1]))


MeshBody.boundingBox = property(_mesh_bbox)
Component.meshBodies = property(lambda s: MeshBodies(s._mesh, s))
_orig_features = Component.features
Component.features = property(lambda s: _Features(_orig_features.fget(s), s))
Design.exportManager = property(lambda s: s.__dict__.setdefault("_export_manager", ExportManager()))
from .historyfakes import *  # noqa: E402,F401,F403  (history recipe fakes)
