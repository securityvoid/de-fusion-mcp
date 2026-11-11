import pytest

from defusion import config, profiles
from defusion.actions import action
from defusion.profiles import BUILTIN, InvalidProfile, merge, parse_query, resolve


@pytest.fixture
def acts(registry):
    """A registry of test actions across tools."""
    def add(tool, name, access):
        def f(ctx):
            """x."""
        f.__name__ = name
        action(tool, access=access)(f)

    for t, n, a in [
        ("model", "extrude", "write"), ("model", "list", "read"), ("model", "sheet_flange", "write"),
        ("model", "sheet_unfold", "write"), ("model", "coil", "write"), ("model", "purge", "destructive"),
        ("cam", "setup", "write"), ("cam", "list_ops", "read"),
        ("script", "run", "write"), ("server", "status", "read"), ("server", "job_status", "read"),
        ("inspect", "bodies", "read"),
    ]:
        add(t, n, a)
    return registry


def keys(s):
    return {"%s.%s" % t for t in s}


def test_builtin_items_match_design():
    assert BUILTIN == {
        "full": ["all"],
        "readonly": ["-write", "-destructive"],
        "3dprinting": ["all", "-cam", "-model.sheet_*", "-script"],
        "metalworking": ["all", "-script"],
    }


def test_builtin_profiles_resolve(acts):
    everything = keys(resolve(["all"], BUILTIN, acts))
    assert len(everything) == 12
    assert keys(resolve(BUILTIN["full"], BUILTIN, acts)) == everything
    ro = keys(resolve(BUILTIN["readonly"], BUILTIN, acts))
    assert ro == {"model.list", "cam.list_ops", "server.status", "server.job_status", "inspect.bodies"}
    p3 = keys(resolve(BUILTIN["3dprinting"], BUILTIN, acts))
    assert p3 == everything - {"cam.setup", "cam.list_ops", "model.sheet_flange", "model.sheet_unfold", "script.run"}
    assert keys(resolve(BUILTIN["metalworking"], BUILTIN, acts)) == everything - {"script.run"}
    assert keys(resolve(["all", "-script"], BUILTIN, acts)) == everything - {"script.run"}


def test_design_examples(acts):
    p3, ro = "3dprinting", "readonly"
    assert keys(resolve([p3, ro], BUILTIN, acts)) == {"model.list", "server.status", "server.job_status", "inspect.bodies"}
    assert keys(resolve([ro], BUILTIN, acts)) == keys(resolve(BUILTIN[ro], BUILTIN, acts))
    got = keys(resolve([ro, "cam"], BUILTIN, acts))
    assert got == {"model.list", "server.status", "server.job_status", "inspect.bodies", "cam.setup", "cam.list_ops"}


def test_start_rule(acts):
    assert keys(resolve(["server"], BUILTIN, acts)) == {"server.status", "server.job_status"}  # starts empty
    assert "model.extrude" in keys(resolve(["-script"], BUILTIN, acts))  # starts full
    assert "script.run" not in keys(resolve(["-script"], BUILTIN, acts))


def test_left_to_right_order(acts):
    assert "cam.setup" in keys(resolve(["all", "-cam", "cam.setup"], BUILTIN, acts))
    assert "cam.setup" not in keys(resolve(["all", "cam.setup", "-cam"], BUILTIN, acts))


def test_selectors(acts):
    assert keys(resolve(["model.sheet_*"], BUILTIN, acts)) == {"model.sheet_flange", "model.sheet_unfold"}
    assert keys(resolve(["model.coil"], BUILTIN, acts)) == {"model.coil"}
    assert keys(resolve(["destructive"], BUILTIN, acts)) == {"model.purge"}
    assert keys(resolve(["inspect", "write"], BUILTIN, acts)) >= {"inspect.bodies", "model.extrude"}


def test_user_profile_overrides_builtin_and_splices(acts):
    user = {"readonly": ["server"], "mine": ["3dprinting", "-model.coil"], "both": ["mine", "readonly"]}
    merged = merge(BUILTIN, user)
    assert merged["readonly"] == ["server"] and merged["full"] == ["all"]
    assert keys(resolve(["readonly"], merged, acts)) == {"server.status", "server.job_status"}
    assert "model.coil" not in keys(resolve(["mine"], merged, acts))
    assert "model.extrude" in keys(resolve(["mine"], merged, acts))
    # nested splice: 'both' = mine + readonly(server) -> first item adds, so union
    assert "server.status" in keys(resolve(["both"], merged, acts))
    assert BUILTIN["readonly"] == ["-write", "-destructive"]  # builtin untouched


@pytest.mark.parametrize("name", ["model", "cam", "script", "all", "read", "write", "destructive"])
def test_reserved_profile_names_rejected(name):
    with pytest.raises(InvalidProfile) as e:
        merge(BUILTIN, {name: ["all"]})
    assert e.value.item == name


@pytest.mark.parametrize("name", ["", "a b", "x.y", "a/b"])
def test_bad_profile_names_rejected(name):
    with pytest.raises(InvalidProfile):
        merge(BUILTIN, {name: ["all"]})


def test_cycle_and_negated_profile(acts):
    with pytest.raises(InvalidProfile) as e:
        merge(BUILTIN, {"a": ["b"], "b": ["a"]})
    assert "cycle" in e.value.reason
    with pytest.raises(InvalidProfile) as e:
        merge(BUILTIN, {"a": ["a"]})
    assert "cycle" in e.value.reason
    with pytest.raises(InvalidProfile) as e:
        resolve(["-readonly"], BUILTIN, acts)
    assert e.value.item == "-readonly"
    with pytest.raises(InvalidProfile) as e:
        merge(BUILTIN, {"a": ["-readonly"]})
    assert e.value.item == "-readonly"


def test_unmatched_selectors_name_the_item(acts):
    for item in ["model.nosuch*", "-model.nosuch*", "nosuch", "nosuch.x", "all-", "model."]:
        with pytest.raises(InvalidProfile) as e:
            resolve(["all", item], BUILTIN, acts)
        assert e.value.item == item
    assert str(e.value) .startswith("invalid profile item 'model.': ")
    assert str(e.value).endswith("; valid selectors are in design § 6.3")


def test_tool_with_no_registered_actions_is_error(registry):
    with pytest.raises(InvalidProfile) as e:
        resolve(["cam"], BUILTIN, registry)
    assert e.value.item == "cam" and "matches no action" in e.value.reason


def test_blank_item_in_list():
    with pytest.raises(InvalidProfile):
        merge(BUILTIN, {"a": ["all", "-"]})


def test_parse_query():
    assert parse_query("") is None and parse_query(None) is None and parse_query("x=1") is None
    assert parse_query("profile=3dprinting,readonly") == ["3dprinting", "readonly"]
    assert parse_query("profile=3dprinting%2Creadonly&x=1") == ["3dprinting", "readonly"]
    assert parse_query("profile=-model.coil") == ["-model.coil"]
    assert parse_query("profile=a%2C%20b") == ["a", "b"]
    for qs in ["profile=", "profile=%20", "profile=a,,b", "profile=a,", "profile=,a", "profile=a&profile=b",
               "profile=a&profile=a", "profile"]:
        with pytest.raises(InvalidProfile):
            parse_query(qs)


def test_config_rejects_bad_profiles_and_default_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    for text, needle in [
        ('[profiles]\nmodel = ["all"]', "model"),
        ('[profiles]\na = ["b"]\nb = ["a"]', "cycle"),
        ('default_profile = ["-readonly"]', "-readonly"),
        ('[profiles]\nx = ["-readonly"]', "-readonly"),
    ]:
        (tmp_path / "config.toml").write_text(text)
        cfg, err = config.load()
        assert err and needle in err, text
    (tmp_path / "config.toml").write_text('default_profile = ["mine"]\n[profiles]\nmine = ["readonly", "cam"]\n')
    assert config.load()[1] is None
    (tmp_path / "config.toml").unlink()
    config.load()
