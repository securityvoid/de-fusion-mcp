"""Fake ``adsk.cam``: a CAM product with setups, operations, parameters, libraries and posts."""
from types import SimpleNamespace

from .core import URL, Collection, Entity, _Fake
from .core import Products as _Products


class OperationTypes:
    MillingOperation = 0
    TurningOperation = 1
    JetOperation = 2
    AdditiveOperation = 3


class OperationStates:
    IsValidOperationState = 0
    IsInvalidOperationState = 1
    SuspendedOperationState = 2
    NoToolpathOperationState = 3


class LibraryLocations:
    LocalLibraryLocation = 0
    CloudLibraryLocation = 1
    Fusion360LibraryLocation = 2


class _Value(_Fake):
    def __init__(self, value):
        self.value = value


class CAMParameter(_Fake):
    """A named parameter; ``expression`` is writable unless ``readonly``."""

    def __init__(self, name, expression="", value=None, title=None, readonly=False):
        self.name = name
        self.title = title if title is not None else name
        self._expression = expression
        self.value = _Value(value if value is not None else expression)
        self._readonly = readonly

    @property
    def expression(self):
        return self._expression

    @expression.setter
    def expression(self, text):
        if self._readonly:
            raise RuntimeError("parameter %s is read-only" % self.name)
        self._expression = text


def parameters(**exprs):
    """A ``CAMParameters`` collection from ``name=expression`` pairs."""
    return Collection(CAMParameter(n, e) for n, e in exprs.items())


class Tool(_Fake):
    def __init__(self, number=1, description="tool", type="flat end mill", diameter=1.0):
        self.parameters = Collection(
            [
                CAMParameter("tool_number", value=number),
                CAMParameter("tool_description", value=description),
                CAMParameter("tool_type", value=type),
                CAMParameter("tool_diameter", value=diameter),
            ]
        )


class Operation(Entity):
    def __init__(self, name, parentSetup=None, strategy="face", tool=None):
        super().__init__(name)
        self.parentSetup = parentSetup
        self.strategy = strategy
        self.tool = tool
        self.hasToolpath = False
        self.isToolpathValid = False
        self.isSuppressed = False
        self.isGenerating = False
        self.operationState = OperationStates.NoToolpathOperationState
        self.hasWarning = False
        self.hasError = False
        self.parameters = Collection()


class Setup(Entity):
    def __init__(self, name, operationType=OperationTypes.MillingOperation):
        super().__init__(name)
        self.operationType = operationType
        self.parameters = parameters(job_stockMode="'default'")
        self._ops = []
        self.machine = None  # ``models`` is set by Setups.add (absent on a bare Setup)
        self.operations = Operations(self)

    @property
    def allOperations(self):
        return Collection(self._ops)

    def deleteMe(self):
        self._cam._setups.remove(self)
        return True

    def add_operation(self, name, **kw):
        op = Operation(name, parentSetup=self, **kw)
        self._ops.append(op)
        return op


class OperationInput(_Fake):
    def __init__(self, strategy):
        self.strategy, self.tool, self.displayName = strategy, None, None


class Operations(_Fake):
    """``setup.operations``: ``compatibleStrategies`` (``None`` = the attribute is missing),
    ``createInput`` and ``add``; ``add_ignores_name`` mimics builds that ignore ``displayName``."""

    def __init__(self, setup, strategies=("face", "adaptive", "pocket2d")):
        self._setup, self.add_ignores_name = setup, False
        self._strategies = strategies

    def __getattr__(self, name):
        if name == "compatibleStrategies" and self._strategies is not None:
            return [SimpleNamespace(name=n) for n in self._strategies]
        raise AttributeError(name)

    def createInput(self, strategy):
        return OperationInput(strategy)

    def add(self, oi):
        n = len(self._setup._ops) + 1
        name = oi.displayName if oi.displayName and not self.add_ignores_name else "%s%d" % (oi.strategy, n)
        op = self._setup.add_operation(name, strategy=oi.strategy, tool=oi.tool)
        op.parameters = parameters(feed="'100'", stepover="'2 mm'")
        return op


class SetupInput(_Fake):
    def __init__(self, operationType):
        self.operationType, self.models, self.name, self.machine = operationType, [], None, None


class Setups(Collection):
    def __init__(self, cam):
        super().__init__()
        self._cam = cam

    @property
    def _items(self):
        return self._cam._setups

    @_items.setter
    def _items(self, value):
        pass

    def createInput(self, operationType):
        return SetupInput(operationType)

    def add(self, si):
        s = self._cam.add_setup(si.name or "Setup%d" % (len(self._cam._setups) + 1),
                                operationType=si.operationType)
        s.models, s.machine = list(si.models), si.machine
        for n in ("job_stockOffsetMode", "job_stockOffsetSides", "job_stockOffsetTop",
                  "job_stockOffsetBottom", "wcs_origin_mode", "wcs_orientation_mode"):
            s.parameters._items.append(CAMParameter(n, "'default'" if n.startswith("wcs") else "0 mm"))
        return s


class GenerateToolpathFuture(_Fake):
    """Completes after ``polls`` reads of ``isGenerationCompleted`` (``never`` = never completes);
    completing makes every operation's toolpath valid."""

    def __init__(self, ops, polls=0, never=False):
        self._ops, self._left, self._never, self._done = list(ops), polls, never, False

    def _poll(self):
        if self._never:
            return False
        if self._left > 0:
            self._left -= 1
            return False
        if not self._done:
            self._done = True
            for op in self._ops:
                op.hasToolpath = op.isToolpathValid = True
                op.operationState = OperationStates.IsValidOperationState
        return True

    @property
    def isGenerationCompleted(self):
        return self._poll()


class LegacyFuture(GenerateToolpathFuture):
    """A future whose completion probe is ``isCompleted`` only."""

    @property
    def isGenerationCompleted(self):
        raise AttributeError("isGenerationCompleted")

    @property
    def isCompleted(self):
        return self._poll()


class NoProbeFuture(_Fake):
    """A future with no completion probe at all."""


class PostOutputUnitOptions:
    DocumentUnitsOutput = 0
    MillimetersOutput = 1
    InchesOutput = 2


class PostProcessInput(_Fake):
    def __init__(self, programName, postConfig, outputFolder, units):
        self.programName, self.postConfig = programName, postConfig
        self.outputFolder, self.postOutputUnitOptions = outputFolder, units
        self.programComment, self.isOpenInEditor = "", True

    @staticmethod
    def create(programName, postConfig, outputFolder, units):
        return PostProcessInput(programName, postConfig, outputFolder, units)


class NCProgramPostProcessOptions(_Fake):
    @staticmethod
    def create():
        return NCProgramPostProcessOptions()


def _write_program(cam, folder, name, comment):
    """What a post processor does: write ``<folder>/<name>.nc`` (unless the fake is told not to)."""
    if cam.post_writes:
        import os

        with open(os.path.join(folder, name + ".nc"), "w") as f:
            f.write("(DEFUSION)\n(%s)\n" % comment)


class NCProgramInput(_Fake):
    def __init__(self):
        self.displayName, self.operations = "", []
        self.parameters = parameters(
            nc_program_filename="", nc_program_output_folder="", nc_program_comment="",
            nc_program_openInEditor="true",
        )


class NCProgram(_Fake):
    def __init__(self, cam, ni):
        self._cam, self._input = cam, ni
        self.postConfiguration = None
        self.deleted = False

    def postProcess(self, options):
        if self._cam.post_error is not None:
            raise self._cam.post_error
        v = lambda n: self._input.parameters.itemByName(n).value.value  # noqa: E731
        self._cam.post_calls.append(("nc", self.postConfiguration, v("nc_program_openInEditor")))
        _write_program(self._cam, v("nc_program_output_folder"), v("nc_program_filename"),
                       v("nc_program_comment"))

    def deleteMe(self):
        self.deleted = True
        self._cam.ncPrograms.programs.remove(self)


class NCPrograms(_Fake):
    def __init__(self, cam):
        self._cam, self.programs, self.inputs = cam, [], []

    def createInput(self):
        self.inputs.append(NCProgramInput())
        return self.inputs[-1]

    def add(self, ni):
        self.programs.append(NCProgram(self._cam, ni))
        return self.programs[-1]


class LibraryManager(_Fake):
    def __init__(self):
        self.postLibrary = self.toolLibraries = self.machineLibrary = None


class CAMManager(_Fake):
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.libraryManager = LibraryManager()
        return cls._instance

    @classmethod
    def reset(cls):
        cls._instance = None


class CAM(_Fake):
    def __init__(self):
        self._setups = []
        self.ncPrograms = NCPrograms(self)
        self.genericPostFolder = None
        self.post_writes, self.post_error, self.post_calls = True, None, []
        self.future = lambda ops: GenerateToolpathFuture(ops)
        self.generate_calls = []

    def postProcess(self, target, inp):
        """``target`` is a setup or a collection of operations; records the call."""
        self.post_calls.append(("file", target, inp))
        _write_program(self, inp.outputFolder, inp.programName, inp.programComment)

    def generateToolpath(self, ops):
        targets = list(ops)
        self.generate_calls.append(("ops", [o.name for o in targets]))
        return self.future(targets)

    def generateAllToolpaths(self, skip_valid):
        targets = [o for s in self._setups for o in s._ops]
        self.generate_calls.append(("all", bool(skip_valid)))
        return self.future(targets)

    @property
    def setups(self):
        return Setups(self)

    @property
    def allOperations(self):
        return Collection([o for s in self._setups for o in s._ops])

    def add_setup(self, name, **kw):
        s = Setup(name, **kw)
        s._cam = self
        self._setups.append(s)
        return s


class Library(_Fake):
    """In-memory asset library. ``roots`` maps a location to its root URL string; ``tree`` maps a
    folder URL string to ``(folder URL strings, asset URL strings)``; ``items`` maps an asset URL
    string to the object returned by the typed accessors."""

    def __init__(self, roots, tree, items=None, machines=None):
        self._roots, self._tree = dict(roots), dict(tree)
        self._items, self._machines = dict(items or {}), dict(machines or {})

    def urlByLocation(self, loc):
        return URL.create(self._roots[loc]) if loc in self._roots else None

    def childFolderURLs(self, url):
        return [URL.create(u) for u in self._tree.get(url.toString(), ((), ()))[0]]

    def childAssetURLs(self, url):
        return [URL.create(u) for u in self._tree.get(url.toString(), ((), ()))[1]]

    def postConfigurationAtURL(self, url):
        return self._items[url.toString()]

    def toolLibraryAtURL(self, url):
        return self._items[url.toString()]

    def childMachines(self, url):
        if url.toString() not in self._machines:
            raise RuntimeError("3 : Given URL does not point to an existing folder.")
        return list(self._machines[url.toString()])


# --- documents and workspaces ----------------------------------------------------------------------


class Products(_Products):
    """Like Fusion, ``itemByProductType`` raises for a product the document does not have."""

    def itemByProductType(self, product_type):
        p = super().itemByProductType(product_type)
        if p is None:
            raise RuntimeError("product %s does not exist" % product_type)
        return p


def document(design=None, cam=None, name="Doc"):
    """A document holding the given design and/or CAM products (missing ones raise on lookup)."""
    from .core import Document

    items = {}
    if design is not None:
        items["DesignProductType"] = design
    if cam is not None:
        items["CAMProductType"] = cam
    doc = Document(name, items)
    doc.products = Products(items)
    return doc


class Workspace(_Fake):
    def __init__(self, ui, id, on_activate=None):
        self._ui, self.id, self._on_activate = ui, id, on_activate

    def activate(self):
        self._ui.activeWorkspace = self
        self._ui.activations.append(self.id)
        if self._on_activate is not None:
            self._on_activate()


def attach_workspaces(ui, doc, create_cam=None, active="FusionSolidEnvironment"):
    """Give a fake ``UserInterface`` workspaces. Activating ``CAMEnvironment`` adds ``create_cam``
    (a ``CAM``) to ``doc`` when given; ``ui.activations`` records each activated id."""
    ui.activations = []

    def make_cam():
        if create_cam is not None:
            doc.products._items["CAMProductType"] = create_cam

    spaces = [Workspace(ui, "FusionSolidEnvironment"), Workspace(ui, "CAMEnvironment", make_cam)]
    ui.workspaces = Collection(spaces)
    ui.workspaces.itemById = lambda i: next((w for w in spaces if w.id == i), None)
    ui.activeWorkspace = next(w for w in spaces if w.id == active)
    return ui
