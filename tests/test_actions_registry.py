"""Registry, tool surface and help tests."""
import sys
import textwrap
from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel, Field, ValidationError

from defusion import actions, types
from defusion.actions import ActionDefinitionError, action, build_tool, help_result
from defusion.context import Ctx
from defusion.errors import ActionError
from defusion.types import Body, Length, Operation, TimelineItem


def bad(**kw):
    def deco(fn):
        with pytest.raises(ActionDefinitionError):
            action(**kw)(fn)

    return deco


def test_valid_action_registers(registry):
    @action("model", access="write", timeout=60)
    def extrude(ctx: Ctx, distance: Length, operation: Operation = "new_body", name: str | None = None) -> dict:
        """Extrude a sketch profile.

        Longer help text."""
        return {}

    a = registry.get("model", "extrude")
    assert a.summary == "Extrude a sketch profile."
    assert a.help.startswith("Extrude a sketch profile.") and "Longer help text." in a.help
    assert (a.access, a.timeout, a.undo_group, a.main_thread) == ("write", 60, True, True)
    schema = a.input_schema()
    assert set(schema["properties"]) == {"distance", "operation", "name"}
    assert schema["required"] == ["distance"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["operation"]["enum"] == list(__import__("typing").get_args(Operation))
    assert extrude is not None  # decorator returns the function


def test_name_override_and_default(registry):
    @action("sketch", name="list", access="read")
    def list_sketches(ctx: Ctx, offset: int = 0, limit: int = 100) -> dict:
        """List sketches."""

    @action("sketch", access="read")
    def create(ctx: Ctx) -> dict:
        """Create."""

    assert registry.get("sketch", "list") is not None and registry.get("sketch", "list_sketches") is None
    assert registry.get("sketch", "create") is not None


def test_model_forbids_extra_and_validates(registry):
    @action("model", access="read")
    def a(ctx: Ctx, n: int) -> dict:
        """A."""

    m = registry.get("model", "a").model
    assert m.model_validate({"n": 1}).n == 1
    with pytest.raises(ValidationError):
        m.model_validate({"n": 1, "zzz": 2})
    with pytest.raises(ValidationError):
        m.model_validate({})


def test_destructive_adds_confirm(registry):
    @action("document", access="destructive")
    def close(ctx: Ctx, force: bool = False) -> dict:
        """Close."""

    a = registry.get("document", "close")
    assert a.params == ("force", "confirm")
    assert a.model.model_validate({}).confirm is False
    assert a.input_schema()["properties"]["confirm"]["type"] == "boolean"

    @action("document", access="write")
    def save(ctx: Ctx) -> dict:
        """Save."""

    assert "confirm" not in registry.get("document", "save").params


def test_destructive_cannot_declare_confirm(registry):
    @bad(tool="document", access="destructive")
    def x(ctx: Ctx, confirm: bool = False) -> dict:
        """X."""


def test_allowed_annotations(registry):
    class Opts(BaseModel):
        a: int = 1
        bodies: list[Body] = []

    @action("model", access="write")
    def many(
        ctx: Ctx,
        s: str, i: int, f: float, b: bool,
        lst: list[str], dct: dict[str, Any], ds: dict[str, str], di: dict[str, int],
        df: dict[str, float], db: dict[str, bool], lit: Literal["a", "b"],
        opt: str | None, u: int | str, tl: int | TimelineItem,
        ann: Annotated[int, Field(ge=0, description="d")],
        mdl: Opts, ref: Body, refs: list[Body], length: Length, op: Operation,
        fname: types.FileName, plane: types.Plane,
    ) -> dict:
        """Many."""

    assert registry.get("model", "many") is not None


@pytest.mark.parametrize(
    "ann", [list, tuple[int, int], set[int], dict, dict[int, str], dict[str, list[int]], dict[str, bytes], bytes, Any, object, "int | bytes"]
)
def test_unsupported_annotations_raise(registry, ann):
    def make():
        def f(ctx, x):
            """F."""

        f.__annotations__ = {"x": ann}
        return f

    with pytest.raises(ActionDefinitionError):
        action("model", access="read")(make())


def test_declaration_errors(registry):
    @bad(tool="nosuch", access="read")
    def a(ctx: Ctx) -> dict:
        """A."""

    @bad(tool="model", access="bogus")
    def b(ctx: Ctx) -> dict:
        """B."""

    @bad(tool="model")
    def b2(ctx: Ctx) -> dict:
        """No access."""

    @bad(tool="model", access="read")
    def kw(ctx: Ctx, **kwargs: int) -> dict:
        """Kwargs."""

    @bad(tool="model", access="read")
    def star(ctx: Ctx, *args: int) -> dict:
        """Args."""

    @bad(tool="model", access="read")
    def camelCase(ctx: Ctx) -> dict:
        """Name."""

    @bad(tool="model", access="read")
    def p(ctx: Ctx, camelParam: int) -> dict:
        """Param."""

    @bad(tool="model", access="read")
    def noann(ctx: Ctx, x) -> dict:
        """No annotation."""

    @bad(tool="model", access="read")
    def noctx(x: int) -> dict:
        """No ctx."""

    @bad(tool="model", access="read")
    def nodoc(ctx: Ctx) -> dict:
        pass

    @bad(tool="model", access="read", name="help")
    def h(ctx: Ctx) -> dict:
        """Help."""

    @bad(tool="model", access="read", name="Bad-Name")
    def n(ctx: Ctx) -> dict:
        """N."""

    assert len(registry) == 0


def test_main_thread_false_rules(registry):
    @action("server", access="read", main_thread=False)
    def status(ctx: Ctx) -> dict:
        """Status."""

    assert registry.get("server", "status").main_thread is False

    @bad(tool="server", access="write", main_thread=False)
    def w(ctx: Ctx) -> dict:
        """W."""

    @bad(tool="server", access="read", main_thread=False)
    def r(ctx: Ctx, b: Body) -> dict:
        """R."""

    @bad(tool="server", access="read", main_thread=False)
    def r2(ctx: Ctx, b: list[Body]) -> dict:
        """R2."""

    class M(BaseModel):
        b: Body

    @bad(tool="server", access="read", main_thread=False)
    def r3(ctx: Ctx, m: M) -> dict:
        """R3."""

    @action("server", access="read", main_thread=False)
    def ok(ctx: Ctx, n: Length, k: int | None = None) -> dict:
        """Ok."""


def test_duplicate_raises(registry):
    @action("model", access="read")
    def dup(ctx: Ctx) -> dict:
        """One."""

    with pytest.raises(ActionDefinitionError, match="model.dup"):
        @action("model", access="read")
        def dup(ctx: Ctx) -> dict:  # noqa: F811
            """Two."""

    @action("sketch", name="dup", access="read")  # same python name on another tool is fine
    def dup2(ctx: Ctx) -> dict:
        """Three."""


def test_isolated_registry_restores():
    outer = actions.registry()
    with actions.isolated_registry() as r:
        assert actions.registry() is r and r is not outer
    assert actions.registry() is outer


# ---- tool surface (REQ-012) --------------------------------------------------------------------------

@pytest.fixture
def acts(registry):
    @action("model", access="read")
    def measure(ctx: Ctx, body: Body) -> dict:
        """Measure."""

    @action("model", access="write")
    def extrude(ctx: Ctx, distance: Length) -> dict:
        """Extrude."""

    @action("model", access="destructive")
    def purge(ctx: Ctx) -> dict:
        """Purge."""

    @action("view", access="read")
    def shot(ctx: Ctx) -> dict:
        """Shot."""

    return registry


ALL = frozenset({"model.measure", "model.extrude", "model.purge", "view.shot"})


def test_build_tool_schema_and_description(acts):
    t = build_tool("model", ALL)
    assert t["name"] == "model"
    assert t["inputSchema"] == {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["help", "extrude", "measure", "purge"]},
            "params": {"type": "object", "default": {}},
            "units": {"type": "object",
                      "properties": {"length": {"type": "string"}, "angle": {"type": "string"}}},
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    assert t["description"] == (
        'Registry, tool surface and help tests. Actions: extrude, measure, purge. Call action="help" with '
        'params {"action": "<name>"} for its parameters.'
    )
    assert ".." not in t["description"]
    assert "distance" not in str(t["inputSchema"])  # per-action schemas never in tools/list


def test_build_tool_filters_by_allowed(acts):
    t = build_tool("model", frozenset({"model.measure", "view.shot"}))
    assert t["inputSchema"]["properties"]["action"]["enum"] == ["help", "measure"]
    assert "extrude" not in t["description"] and "Actions: measure." in t["description"]
    assert build_tool("model", frozenset({"view.shot"})) is None
    assert build_tool("cam", ALL) is None


def test_build_tool_annotations(acts):
    ro = build_tool("model", frozenset({"model.measure"}))["annotations"]
    assert ro == {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
    mixed = build_tool("model", ALL)["annotations"]
    assert mixed["readOnlyHint"] is False and mixed["destructiveHint"] is True
    w = build_tool("model", frozenset({"model.measure", "model.extrude"}))["annotations"]
    assert w["readOnlyHint"] is False and w["destructiveHint"] is False


# ---- help (REQ-024) --------------------------------------------------------------------------------------------

def test_help_list_only_allowed(acts):
    r = help_result("model", frozenset({"model.measure", "model.purge"}), {})
    assert r == {"tool": "model", "actions": [
        {"name": "measure", "summary": "Measure.", "access": "read"},
        {"name": "purge", "summary": "Purge.", "access": "destructive"}]}


def test_help_action_detail(acts):
    r = help_result("model", ALL, {"action": "extrude"})
    assert set(r) == {"name", "summary", "help", "access", "timeout", "input_schema"}
    assert r["name"] == "extrude" and r["access"] == "write" and r["timeout"] == 30
    assert "distance" in r["input_schema"]["properties"]
    assert "confirm" in help_result("model", ALL, {"action": "purge"})["input_schema"]["properties"]


def test_help_hidden_action_not_allowed(acts):
    with pytest.raises(ActionError) as e:
        help_result("model", frozenset({"model.measure"}), {"action": "extrude"})
    assert e.value.code == "NOT_ALLOWED" and e.value.hints == ["measure"]


def test_help_unknown_and_bad_params(acts):
    with pytest.raises(ActionError) as e:
        help_result("model", ALL | {"model.ghost"}, {"action": "ghost"})
    assert e.value.code == "UNKNOWN_ACTION"
    for bad_params in ({"action": 5}, {"foo": 1}, "x"):
        with pytest.raises(ActionError) as e:
            help_result("model", ALL, bad_params)
        assert e.value.code == "INVALID_PARAMS"


# ---- tools package (REQ-011) -----------------------------------------------------------------------------------------

def make_pkg(tmp_path, monkeypatch, files):
    pkg = tmp_path / "fakepkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for name, body in files.items():
        (pkg / (name + ".py")).write_text(textwrap.dedent(body))
    monkeypatch.syspath_prepend(str(tmp_path))
    for m in [m for m in sys.modules if m.startswith("fakepkg")]:
        monkeypatch.delitem(sys.modules, m)
    return "fakepkg"


def test_tool_modules_must_register_an_action(registry, tmp_path, monkeypatch):
    pkg = make_pkg(tmp_path, monkeypatch, {
        "good": '''
            """Good tool."""
            from defusion.actions import action
            @action("model", access="read")
            def g(ctx):
                """G."""
        ''',
        "empty": '"""Empty tool."""\n',
        "nodoc": '''
            from defusion.actions import action
            @action("view", access="read")
            def v(ctx):
                """V."""
        ''',
    })
    actions.import_tool_modules(pkg, ["good"])
    (tmp_path / "fakepkg" / "idx.py").write_text('"""Index."""\nfrom . import idx_sub\n')
    (tmp_path / "fakepkg" / "idx_sub.py").write_text(textwrap.dedent('''
        """Sub."""
        from defusion.actions import action
        @action("sketch", access="read")
        def s(ctx):
            """S."""
    '''))
    actions.import_tool_modules(pkg, ["idx"])  # registered by a prefixed sub-module
    with pytest.raises(RuntimeError, match="fakepkg.empty"):
        actions.import_tool_modules(pkg, ["empty"])
    with pytest.raises(RuntimeError, match="fakepkg.nodoc"):
        actions.import_tool_modules(pkg, ["nodoc"])


def test_tools_init_lists_the_ten_modules():
    src = open(actions.__file__.replace("actions.py", "tools/__init__.py")).read()
    for m in "document sketch model assembly history inspect view cam script server".split():
        assert '"%s"' % m in src
    assert "import_tool_modules" in src
