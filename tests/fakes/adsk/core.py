"""Fake ``adsk.core``. Every public attribute access on a fake instance records
``(Class.attr, threading.get_ident())`` in ``CALLS`` so tests can assert thread use."""
import copy
import itertools
import math
import queue
import re
import threading

CALLS: list[tuple[str, int]] = []
FIRE_THREADS: list[int] = []
_tokens = itertools.count(1)


def reset_calls():
    CALLS.clear()
    FIRE_THREADS.clear()


def calling_threads() -> set[int]:
    return {t for _, t in CALLS}


class _Fake:
    _unrecorded: frozenset = frozenset()

    def __getattribute__(self, name):
        if not name.startswith("_") and name not in object.__getattribute__(self, "_unrecorded"):
            CALLS.append((type(self).__name__ + "." + name, threading.get_ident()))
        return object.__getattribute__(self, name)

    @classmethod
    def cast(cls, obj):
        return obj if isinstance(obj, cls) else None


class Base(_Fake):
    @property
    def isValid(self):
        return self.__dict__.get("_valid", True)

    @isValid.setter
    def isValid(self, value):
        self.__dict__["_valid"] = value


class Collection(_Fake):
    """List-like collection with the Fusion ``count`` / ``item`` / ``itemByName`` API."""

    def __init__(self, items=()):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        if not 0 <= i < len(self._items):
            return None
        return self._items[i]

    def itemByName(self, name):
        for it in self._items:
            if getattr(it, "name", None) == name:
                return it
        return None

    def __iter__(self):
        return iter(list(self._items))

    def __len__(self):
        return len(self._items)

    def __getitem__(self, i):
        return self._items[i]


class Point3D(_Fake):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z

    @staticmethod
    def create(x, y, z):
        return Point3D(x, y, z)

    def asArray(self):
        return (self.x, self.y, self.z)


class Vector3D(_Fake):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z

    @staticmethod
    def create(x, y, z):
        return Vector3D(x, y, z)

    def copy(self):
        return Vector3D(self.x, self.y, self.z)

    def normalize(self):
        n = math.sqrt(self.x ** 2 + self.y ** 2 + self.z ** 2)
        if n:
            self.x, self.y, self.z = self.x / n, self.y / n, self.z / n
        return n > 0


class SurfaceTypes:
    PlaneSurfaceType = 0
    CylinderSurfaceType = 1
    ConeSurfaceType = 2
    SphereSurfaceType = 3
    TorusSurfaceType = 4
    EllipticalCylinderSurfaceType = 5
    EllipticalConeSurfaceType = 6
    NurbsSurfaceType = 7


class Curve3DTypes:
    Line3DCurveType = 0
    Arc3DCurveType = 1
    Circle3DCurveType = 2
    Ellipse3DCurveType = 3
    EllipticalArc3DCurveType = 4
    NurbsCurve3DCurveType = 5
    InfiniteLine3DCurveType = 6


class Line3D(_Fake):
    pass


class Plane(_Fake):
    pass


class Cylinder(_Fake):
    pass


class BoundingBox3D(_Fake):
    def __init__(self, minPoint, maxPoint):
        self.minPoint, self.maxPoint = minPoint, maxPoint


class ObjectCollection(Collection):
    @staticmethod
    def create():
        return ObjectCollection()

    def add(self, item):
        self._items.append(item)
        return True


class ValueInput(_Fake):
    def __init__(self, kind, value):
        self.kind, self.value = kind, value

    @staticmethod
    def createByString(s):
        return ValueInput("string", s)

    @staticmethod
    def createByReal(r):
        return ValueInput("real", r)


class Attribute(_Fake):
    def __init__(self, group, name, value):
        self.groupName, self.name, self.value = group, name, value


class Attributes(_Fake):
    def __init__(self):
        self._d = {}

    def add(self, group, name, value):
        a = Attribute(group, name, value)
        self._d[(group, name)] = a
        return a

    def itemByName(self, group, name):
        return self._d.get((group, name))

    @property
    def count(self):
        return len(self._d)


class Entity(Base):
    """Common base for fake model entities: name, token, attributes."""

    assemblyContext = None

    def __init__(self, name=None):
        self.name = name
        self.entityToken = "tok-%d" % next(_tokens)
        self.attributes = Attributes()

    def createForAssemblyContext(self, occurrence):
        """An assembly-context proxy: a shallow copy whose ``assemblyContext`` is ``occurrence``."""
        proxy = copy.copy(self)
        proxy.assemblyContext = occurrence
        proxy.nativeObject = self
        return proxy

    @property
    def objectType(self):
        return "adsk::fusion::" + type(self).__name__


_LEN = {"mm": 0.1, "cm": 1.0, "m": 100.0, "in": 2.54, "ft": 30.48, "um": 1e-4}
_ANG = {"deg": math.pi / 180, "rad": 1.0}
_TOK = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?)(?:\s*(?P<unit>[A-Za-z]+))?|(?P<id>[A-Za-z_]\w*)|(?P<op>[()+\-*/]))"
)


class UnitsManager(_Fake):
    def __init__(self, design=None):
        self._design = design
        self.defaultLengthUnits = "mm"

    def _eval(self, expr, units):
        table = _ANG if units in _ANG else _LEN if units in _LEN else {}
        default = {"cm": self.defaultLengthUnits, "rad": "deg"}.get(units, "")
        params = getattr(self._design, "_params", {}) if self._design is not None else {}
        out, pos = [], 0
        s = expr.strip()
        has_id = bool(re.search(r"(?<![\d.])[A-Za-z_]\w*", re.sub(r"(?<=\d)\s*[A-Za-z]+", "", s)))
        if not s:
            raise RuntimeError("empty expression")
        while pos < len(s):
            m = _TOK.match(s, pos)
            if not m or m.end() == pos:
                raise RuntimeError("bad expression")
            pos = m.end()
            if m["num"]:
                unit = m["unit"] or default
                if units == "":
                    if m["unit"]:
                        raise RuntimeError("unit in unitless expression")
                    out.append(m["num"])
                else:
                    if not m["unit"] and has_id:
                        out.append(m["num"])  # bare factor next to a parameter
                    elif unit not in table:
                        raise RuntimeError("bad unit " + unit)
                    else:
                        out.append("(%s*%r)" % (m["num"], table[unit]))
            elif m["id"]:
                if m["id"] not in params:
                    raise RuntimeError("unknown name " + m["id"])
                out.append("(%r)" % params[m["id"]])
            else:
                out.append(m["op"])
        try:
            result = eval("".join(out), {"__builtins__": {}}, {})
        except Exception as e:
            raise RuntimeError("bad expression") from e
        if units in table or units == "":
            return float(result)
        raise RuntimeError("bad units")

    def isValidExpression(self, expr, units):
        try:
            self._eval(expr, units)
            return True
        except RuntimeError:
            return False

    def evaluateExpression(self, expr, units=""):
        return self._eval(expr, units)


class CustomEventArgs(_Fake):
    def __init__(self, eventId, additionalInfo=""):
        self.eventId, self.additionalInfo = eventId, additionalInfo


class CustomEventHandler(_Fake):
    def notify(self, args):  # overridden by subclasses
        pass


class CustomEvent(_Fake):
    def __init__(self, event_id):
        self._id = event_id
        self._handlers = []

    def add(self, handler):
        self._handlers.append(handler)
        return True

    def remove(self, handler):
        if handler in self._handlers:
            self._handlers.remove(handler)
            return True
        return False


class CommandDefinition(_Fake):
    """``execute`` runs the callable given at construction (a test hook, e.g. an undo that mutates
    the fake design)."""

    def __init__(self, command_id, on_execute=None):
        self.id, self._on_execute, self.executed = command_id, on_execute, 0

    def execute(self):
        self.executed += 1
        if self._on_execute is not None:
            self._on_execute()
        return True


class CommandDefinitions(_Fake):
    def __init__(self):
        self._defs = {}

    def add(self, command_id, on_execute=None):
        self._defs[command_id] = CommandDefinition(command_id, on_execute)
        return self._defs[command_id]

    def itemById(self, command_id):
        return self._defs.get(command_id)


class Selection(_Fake):
    def __init__(self, entity, point=None):
        self.entity, self.point = entity, point


class UserInterface(_Fake):
    def __init__(self):
        self.message_boxes = []
        self.activeSelections = Collection()
        self.commandDefinitions = CommandDefinitions()

    def messageBox(self, text, *a, **k):
        self.message_boxes.append(text)
        return 0


class Products(_Fake):
    def __init__(self, items=None):
        self._items = dict(items or {})

    def itemByProductType(self, product_type):
        return self._items.get(product_type)

    @property
    def count(self):
        return len(self._items)


class Document(_Fake):
    def __init__(self, name="Untitled", products=None):
        self.name = name
        self.products = Products(products)


class OrientedBoundingBox3D(_Fake):
    def __init__(self, centerPoint, length, width, height):
        self.centerPoint, self.length, self.width, self.height = centerPoint, length, width, height


class MeasureResults(_Fake):
    def __init__(self, value, positionOne=None, positionTwo=None, positionThree=None):
        self.value = value
        self.positionOne, self.positionTwo, self.positionThree = positionOne, positionTwo, positionThree


class MeasureManager(_Fake):
    """Returns the preset ``obb`` / ``distance`` / ``angle`` (an Exception instance is raised)."""

    def __init__(self):
        self.obb = self.distance = self.angle = None
        self.args = []

    def _out(self, v):
        if isinstance(v, Exception):
            raise v
        return v

    def getOrientedBoundingBox(self, entity, lengthDirection, widthDirection):
        self.args.append(("obb", entity, lengthDirection, widthDirection))
        return self._out(self.obb)

    def measureMinimumDistance(self, a, b):
        self.args.append(("distance", a, b))
        return self._out(self.distance)

    def measureAngle(self, *entities):
        self.args.append(("angle",) + entities)
        return self._out(self.angle)


class Application(_Fake):
    """Fake application. A background thread plays Fusion's main thread: it delivers
    fired custom events to registered handlers. ``hold()``/``release()`` pause it."""

    _instance = None
    _unrecorded = frozenset({"fireCustomEvent", "commands", "main_thread_id"})

    def __init__(self):
        self.userInterface = UserInterface()
        self.measureManager = MeasureManager()
        self.activeDocument = None
        self._active_product = None
        self.commands: list[str] = []
        self.text_command_errors: dict[str, Exception] = {}
        self.text_command_results: dict[str, str] = {}
        self.logs: list[str] = []
        self._events: dict[str, CustomEvent] = {}
        self._queue: queue.Queue = queue.Queue()
        self._drop = 0
        self._hold = threading.Event()
        self._hold.set()
        self._parked = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self.main_thread_id = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        if cls._instance is not None:
            cls._instance.stop_main_loop()
        cls._instance = cls()
        return cls._instance

    @property
    def activeProduct(self):
        if self._active_product is not None:
            return self._active_product
        doc = self.activeDocument
        return doc.products.itemByProductType("DesignProductType") if doc else None

    @activeProduct.setter
    def activeProduct(self, p):
        self._active_product = p

    def log(self, msg, *a, **k):
        self.logs.append(msg)

    def executeTextCommand(self, cmd):
        self.commands.append(cmd)
        for key, exc in self.text_command_errors.items():
            if key in cmd:
                raise exc
        r = self.text_command_results.get(cmd, "")
        return r() if callable(r) else r

    def registerCustomEvent(self, event_id):
        if event_id in self._events:
            raise RuntimeError("event already registered: " + event_id)
        ev = CustomEvent(event_id)
        self._events[event_id] = ev
        return ev

    def unregisterCustomEvent(self, event_id):
        return self._events.pop(event_id, None) is not None

    def fireCustomEvent(self, event_id, additionalInfo=""):
        FIRE_THREADS.append(threading.get_ident())
        if self._drop > 0:
            self._drop -= 1
            return False
        self._queue.put((event_id, additionalInfo))
        return True

    # ---- test controls -------------------------------------------------
    def drop_next_fires(self, n):
        self._drop = n

    def hold(self):
        """Pause the main loop: returns once it is parked (not delivering events)."""
        self._hold.clear()
        self._parked.wait(2)

    def release(self):
        self._hold.set()

    def start_main_loop(self):
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="fusion-main", daemon=True)
        self._thread.start()
        while self.main_thread_id is None:
            pass

    def stop_main_loop(self):
        self._stop.set()
        self._hold.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self.main_thread_id = None

    def _loop(self):
        self.main_thread_id = threading.get_ident()
        while not self._stop.is_set():
            if not self._hold.is_set():
                self._parked.set()
                self._hold.wait()
                self._parked.clear()
                continue
            self._deliver_pending(timeout=0.01)

    def _deliver_pending(self, timeout=0):
        while True:
            try:
                eid, info = self._queue.get(timeout=timeout) if timeout else self._queue.get_nowait()
            except queue.Empty:
                return
            ev = self._events.get(eid)
            if ev is not None:
                for h in list(ev._handlers):
                    h.notify(CustomEventArgs(eid, info))
            if timeout:
                return


class SurfaceTypes:
    PlaneSurfaceType = 0
    CylinderSurfaceType = 1
    ConeSurfaceType = 2
    SphereSurfaceType = 3
    TorusSurfaceType = 4
    EllipticalCylinderSurfaceType = 5
    EllipticalConeSurfaceType = 6
    NurbsSurfaceType = 7


class Matrix3D(_Fake):
    """Identity transform by default; ``coordinate_system`` is (origin, x, y, z) as Point3D/Vector3D."""

    def __init__(self):
        self.coordinate_system = (Point3D(0, 0, 0), Vector3D(1, 0, 0), Vector3D(0, 1, 0), Vector3D(0, 0, 1))

    @staticmethod
    def create():
        return Matrix3D()

    def getAsCoordinateSystem(self):
        return self.coordinate_system


class URL(_Fake):
    """Fake ``adsk.core.URL`` (used by the CAM libraries)."""

    def __init__(self, text):
        self._text = text

    @staticmethod
    def create(text):
        return URL(text)

    def toString(self):
        return self._text


# ---- document / data fakes (document tool) -------------------------------------------------------
class DocumentTypes:
    FusionDesignDocumentType = 0
    FusionDrawingDocumentType = 1


class DocumentReference(_Fake):
    def __init__(self, dataFile, isOutOfDate=False, version=1, referencedDocument=None):
        self.dataFile, self.isOutOfDate, self.version = dataFile, isOutOfDate, version
        self.referencedDocument = referencedDocument


class FusionDocument(Document):
    """A document with the lifecycle API. ``fail`` holds method names that return False;
    ``calls`` records ``(method, *args)``."""

    objectType = "adsk::fusion::FusionDocument"

    def __init__(self, name="Untitled", products=None, dataFile=None):
        super().__init__(name, products)
        self.creationId = "cid-%d" % next(_tokens)
        self.isVisible, self.isModified = True, False
        self.dataFile = dataFile
        self.fail: set = set()
        self.calls: list = []
        self._refs: list = []
        self._all_refs: list = []

    @property
    def isActive(self):
        return Application.get().activeDocument is self

    @property
    def documentReferences(self):
        return Collection(self._refs)

    @property
    def allDocumentReferences(self):
        return Collection(self._all_refs or self._refs)

    def _op(self, name, *args):
        self.calls.append((name,) + args)
        return name not in self.fail

    def activate(self):
        if not self._op("activate"):
            return False
        Application.get().activeDocument = self
        return True

    def save(self, description):
        if not self._op("save", description):
            return False
        self.dataFile.versionNumber += 1
        self.dataFile.latestVersionNumber = self.dataFile.versionNumber
        return True

    def saveAs(self, name, folder, description, tag):
        if not self._op("saveAs", name, folder, description, tag):
            return False
        self.name = name
        self.dataFile = folder._add_file(name, "f3d", pending=True)
        return True

    def close(self, saveChanges):
        if not self._op("close", saveChanges):
            return False
        app = Application.get()
        docs = app.documents._items
        if self in docs:
            docs.remove(self)
        if app.activeDocument is self:
            app.activeDocument = docs[-1] if docs else None
        return True

    def advanceToLatest(self):
        return self._op("advanceToLatest")

    def updateAllReferences(self):
        return self._op("updateAllReferences")


class Documents(Collection):
    def __init__(self, app):
        super().__init__()
        self._app = app
        self.calls: list = []
        self.fail: set = set()

    def _new(self, name, dataFile=None, design=None):
        from . import fusion

        doc = FusionDocument(name, {"DesignProductType": design or fusion.Design(parametric=True)}, dataFile)
        self._items.append(doc)
        self._app.activeDocument = doc
        return doc

    def add(self, documentType):
        self.calls.append(("add", documentType))
        return None if "add" in self.fail else self._new("Untitled")

    def open(self, dataFile, visible=True):
        self.calls.append(("open", dataFile, visible))
        return None if "open" in self.fail else self._new(dataFile.name, dataFile)


class DataUser(_Fake):
    def __init__(self, displayName):
        self.displayName = displayName


class DataFile(_Fake):
    """A cloud file. ``id`` is a local path until ``finish_upload()`` (like Fusion after saveAs)."""

    def __init__(self, folder, name, extension="f3d", id=None):
        self.parentFolder, self.parentProject = folder, folder.parentProject
        self.name, self.fileExtension, self.description = name, extension, ""
        self.versionNumber = self.latestVersionNumber = 1
        self.isReadOnly = self.isInUse = False
        self.createdBy = DataUser("Tester")
        self.dateCreated = self.dateModified = 1700000000
        self.fusionWebURL = "https://fusion.example/" + name
        self.id = id or self.parentProject._data._new_id("file")
        self.calls: list = []
        self.fail: set = set()

    def finish_upload(self):
        self.id = self.parentProject._data._new_id("file")
        self.parentProject._data._files[self.id] = self

    def refresh(self, handler):
        self.calls.append(("refresh",))
        return "refresh" not in self.fail

    def copy(self, folder):
        self.calls.append(("copy", folder))
        return None if "copy" in self.fail else folder._add_file(self.name, self.fileExtension)

    def move(self, folder):
        self.calls.append(("move", folder))
        if "move" in self.fail:
            return False
        self.parentFolder._files.remove(self)
        self.parentFolder = folder
        folder._files.append(self)
        return True

    def deleteMe(self):
        self.calls.append(("deleteMe",))
        if "deleteMe" in self.fail:
            return False
        self.parentFolder._files.remove(self)
        self.parentProject._data._files.pop(self.id, None)
        return True


class DataFolder(_Fake):
    def __init__(self, name, parentFolder, parentProject):
        self.name, self.parentFolder, self.parentProject = name, parentFolder, parentProject
        self.id = parentProject._data._new_id("folder")
        self._folders, self._files = [], []
        parentProject._data._folders[self.id] = self

    isRoot = property(lambda s: s.parentFolder is None)
    dataFolders = property(lambda s: Collection(s._folders))
    dataFiles = property(lambda s: Collection(s._files))

    def _add_file(self, name, extension="f3d", pending=False):
        df = DataFile(self, name, extension, id="/local/" + name if pending else None)
        self._files.append(df)
        if not pending:
            self.parentProject._data._files[df.id] = df
        return df

    add_file = _add_file

    def add_folder(self, name):
        f = DataFolder(name, self, self.parentProject)
        self._folders.append(f)
        return f


class DataProject(_Fake):
    def __init__(self, data, name):
        self._data = data
        self.name, self.id = name, data._new_id("project")
        self.rootFolder = DataFolder(name, None, self)


class DataHub(_Fake):
    def __init__(self, data, name):
        self._data = data
        self.name, self.id = name, data._new_id("hub")
        self._projects = []

    dataProjects = property(lambda s: Collection(s._projects))

    def add_project(self, name):
        p = DataProject(self._data, name)
        self._projects.append(p)
        if self._data.activeProject is None:
            self._data.activeProject = p
        return p


class Data(_Fake):
    def __init__(self):
        self._hubs, self._files, self._folders, self._n = [], {}, {}, 0
        self.activeHub = self.activeProject = None

    dataHubs = property(lambda s: Collection(s._hubs))

    def _new_id(self, kind):
        self._n += 1
        return "urn:adsk.%s:%d" % (kind, self._n)

    def add_hub(self, name):
        h = DataHub(self, name)
        self._hubs.append(h)
        if self.activeHub is None:
            self.activeHub = h
        return h

    def findFileById(self, id):
        return self._files.get(id)

    def findFolderById(self, id):
        return self._folders.get(id)


class ImportOptions(_Fake):
    def __init__(self, kind, path, plane=None):
        self.kind, self.path, self.planarEntity = kind, path, plane


class ImportManager(_Fake):
    """Option factories record the kind; ``importToTarget`` adds what a real import would."""

    def __init__(self, app):
        self._app = app
        self.calls: list = []
        self.fail: set = set()

    def importToNewDocument(self, options):
        self.calls.append(("importToNewDocument", options))
        return None if "importToNewDocument" in self.fail else self._app.documents._new("Imported")

    def importToTarget(self, options, target):
        self.calls.append(("importToTarget", options, target))
        if "importToTarget" in self.fail:
            return False
        if options.kind in ("dxf", "svg"):
            target.add_sketch("Imported sketch")
        else:
            target.add_occurrence("Imported occ").component.add_body("Imported body")
            target.add_body("Imported loose body")
        return True


def _mk_import(kind):
    def make(self, path):
        self.calls.append(("create_" + kind, path))
        return ImportOptions(kind, path)

    return make


for _kind, _name in (
    ("f3d", "createFusionArchiveImportOptions"), ("step", "createSTEPImportOptions"),
    ("iges", "createIGESImportOptions"), ("sat", "createSATImportOptions"),
    ("smt", "createSMTImportOptions"), ("svg", "createSVGImportOptions"),
):
    setattr(ImportManager, _name, _mk_import(_kind))


def _dxf_options(self, path, plane):
    self.calls.append(("create_dxf", path, plane))
    return ImportOptions("dxf", path, plane)


ImportManager.createDXF2DImportOptions = _dxf_options


def _lazy(key, factory):
    def get(self):
        if key not in self.__dict__:
            self.__dict__[key] = factory(self)
        return self.__dict__[key]

    return property(get)


Application.documents = _lazy("_documents", Documents)
Application.data = _lazy("_data", lambda app: Data())
Application.importManager = _lazy("_import_manager", ImportManager)
