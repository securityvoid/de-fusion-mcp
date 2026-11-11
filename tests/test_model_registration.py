"""Registration (design § 2.1): the model tool exposes exactly the 63 actions with the right access and timeouts."""
import importlib

import pytest
import model_fakes  # noqa: F401  (stubs the sibling tool modules this worktree does not own)

from defusion import actions

READ = {"thread_query", "list_threads", "parameter_list", "material_list", "units_get", "config_inspect",
        "find_attributes", "find_by_token", "visibility_get", "selection_sets_list"}
DESTRUCTIVE = {"parameter_delete", "remove_body", "delete_entities", "set_design_type"}
WRITE = {
    "extrude", "revolve", "sweep", "loft", "fillet", "chamfer", "shell", "mirror", "hole", "rect_pattern",
    "circ_pattern", "thread", "draft", "split_body", "split_face", "offset_faces", "scale", "move", "combine",
    "rename", "coil", "cylinder", "sphere", "torus", "surface_patch", "surface_stitch", "surface_thicken",
    "surface_ruled", "surface_trim", "sheet_flange", "sheet_bend", "sheet_unfold", "sheet_flat_pattern",
    "construction_plane", "construction_axis", "ucs", "parameter_create", "parameter_modify", "material_assign",
    "appearance_set", "color_set", "units_set", "create_configured_design", "visibility_set",
    "selection_set_create", "set_design_intent", "activate_root", "compute_all", "set_modeling_in_assembly",
}
SLOW = {"coil", "thread", "sheet_flange", "sheet_bend", "sheet_unfold", "sheet_flat_pattern"}


@pytest.fixture(scope="module")
def model():
    importlib.import_module("defusion.tools.model")
    return {a.name: a for a in actions.registry() if a.tool == "model"}


def test_exactly_the_63_actions(model):
    assert len(READ) == 10 and len(DESTRUCTIVE) == 4 and len(WRITE) == 49
    assert set(model) == READ | DESTRUCTIVE | WRITE and len(model) == 63


def test_access_classes(model):
    assert {n for n, a in model.items() if a.access == "read"} == READ
    assert {n for n, a in model.items() if a.access == "destructive"} == DESTRUCTIVE
    assert {n for n, a in model.items() if a.access == "write"} == WRITE


def test_timeouts(model):
    assert {n: a.timeout for n, a in model.items() if a.timeout != 30} == {**{n: 60 for n in SLOW}, "compute_all": 300}


def test_sheet_metal_names_and_absent_actions(model):
    assert {n for n in model if n.startswith("sheet")} == {"sheet_flange", "sheet_bend", "sheet_unfold",
                                                          "sheet_flat_pattern"}
    assert not {"box", "delete_all"} & set(model)


def test_destructive_actions_take_confirm_and_others_do_not(model):
    assert all("confirm" in a.params for a in model.values() if a.access == "destructive")
    assert not any("confirm" in a.params for a in model.values() if a.access != "destructive")
