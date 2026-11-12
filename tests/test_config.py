import importlib
import tomllib

import pytest

from defusion import config, units


@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    yield tmp_path
    config.load()  # leave a clean state (tmp dir is gone/empty -> defaults)
    units.set_input_defaults("mm", "deg")


def write(d, text):
    (d / "config.toml").write_text(text, encoding="utf-8")


def test_missing_file_uses_defaults(cfgdir):
    cfg, err = config.load()
    assert err is None and config.load_error() is None and config.settings() is cfg
    assert (cfg.port, cfg.token, cfg.log_level) == (8642, None, "INFO")
    assert cfg.default_profile == ["all", "-script"]
    assert cfg.units == {"length": "mm", "angle": "deg"} and cfg.profiles == {}


def test_default_toml_parses_to_the_defaults(cfgdir):
    write(cfgdir, config.DEFAULT_TOML)
    cfg, err = config.load()
    assert err is None and cfg == config.Config()
    assert tomllib.loads(config.DEFAULT_TOML)["port"] == 8642


def test_valid_file_all_keys(cfgdir):
    write(cfgdir, '''port = 9000
token = "s3cret"
log_level = "DEBUG"
default_profile = ["readonly", "cam"]
[units]
length = "in"
angle = "rad"
[profiles]
my-print = ["3dprinting", "-model.coil"]
''')
    cfg, err = config.load()
    assert err is None
    assert (cfg.port, cfg.token, cfg.log_level) == (9000, "s3cret", "DEBUG")
    assert cfg.default_profile == ["readonly", "cam"]
    assert cfg.units == {"length": "in", "angle": "rad"}
    assert cfg.profiles == {"my-print": ["3dprinting", "-model.coil"]}
    assert config.settings() is cfg


def test_load_sets_input_unit_defaults(cfgdir):
    write(cfgdir, '[units]\nlength = "cm"\nangle = "rad"\n')
    config.load()
    assert units.input_defaults() == {"length": "cm", "angle": "rad"}
    write(cfgdir, "port = 1\n")  # error -> defaults, not the previous file's units
    config.load()
    assert units.input_defaults() == {"length": "mm", "angle": "deg"}


BAD = [
    ("port = 80", "port"),
    ("port = 70000", "port"),
    ('port = "8642"', "port"),
    ("port = true", "port"),
    ('token = ""', "token"),
    ('token = "   "', "token"),
    ("token = 5", "token"),
    ('log_level = "TRACE"', "log_level"),
    ('log_level = "info"', "log_level"),
    ('default_profile = "all"', "default_profile"),
    ("default_profile = []", "default_profile"),
    ('default_profile = ["all", ""]', "default_profile"),
    ('default_profile = ["-"]', "default_profile"),
    ('default_profile = ["a b"]', "default_profile"),
    ("default_profile = [1]", "default_profile"),
    ('[units]\nlength = "furlong"', "units.length"),
    ('[units]\nangle = "grad"', "units.angle"),
    ('[units]\nmass = "kg"', "units.mass"),
    ("units = 5", "units"),
    ('[profiles]\nx = "all"', "profiles.x"),
    ("[profiles]\nx = []", "profiles.x"),
    ("profiles = 1", "profiles"),
    ("bogus = 1", "bogus"),
    ("port = = 1", "cannot parse"),
]


@pytest.mark.parametrize("text,needle", BAD)
def test_invalid_config_is_error_with_no_fallback(cfgdir, text, needle):
    prefix = "" if "default_profile" in text else 'default_profile = ["readonly"]\n'
    write(cfgdir, prefix + text)
    cfg, err = config.load()
    assert err and needle in err
    assert config.load_error() == err
    # fail closed: nothing from the file survives
    assert (cfg.port, cfg.token, cfg.log_level) == (8642, None, "INFO")
    assert cfg.default_profile == ["all", "-script"] and cfg.profiles == {}


def test_all_errors_reported_together(cfgdir):
    write(cfgdir, 'port = 1\nlog_level = "x"\n')
    _, err = config.load()
    assert "port" in err and "log_level" in err


def test_unreadable_file(cfgdir):
    (cfgdir / "config.toml").mkdir()  # a directory where the file should be
    _, err = config.load()
    assert err and "cannot read" in err


def test_non_utf8_file(cfgdir):
    (cfgdir / "config.toml").write_bytes(b"\xff\xfe\x00")
    _, err = config.load()
    assert err and "cannot parse" in err


def test_error_is_logged(cfgdir, caplog):
    write(cfgdir, "port = 1\n")
    with caplog.at_level("ERROR", logger="defusion"):
        config.load()
    assert any("config error" in r.getMessage() for r in caplog.records)


def test_error_clears_on_next_good_load(cfgdir):
    write(cfgdir, "port = 1\n")
    assert config.load()[1]
    write(cfgdir, "port = 2000\n")
    assert config.load()[1] is None and config.load_error() is None


def test_config_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFUSION_CONFIG_DIR", str(tmp_path / "x"))
    assert config._default_dir() == tmp_path / "x"
    monkeypatch.delenv("DEFUSION_CONFIG_DIR")
    assert config._default_dir().name == ".defusion"
    assert config.CONFIG_PATH.name == "config.toml" and config.CONFIG_PATH.parent == config.CONFIG_DIR


# --- folder (REQ-CORE-053, design § 6.1) -------------------------------------------------------------------

@pytest.mark.parametrize("value,want", [('"P"', "P"), ('"My Project/Parts"', "My Project/Parts"), ('" /P//A/ "', "P/A")])
def test_folder_valid(cfgdir, value, want):
    write(cfgdir, "folder = %s\n" % value)
    cfg, err = config.load()
    assert err is None and cfg.folder == want and config.settings().folder == want


def test_folder_default_none_and_commented_in_default_toml(cfgdir):
    assert config.load()[0].folder is None
    assert '# folder = "My Project/Parts"    # optional default folder limit; see § 6.4\n' in config.DEFAULT_TOML


@pytest.mark.parametrize("value,shown", [('" / "', " / "), ("5", "5"), ('""', "")])
def test_folder_invalid_is_config_error(cfgdir, value, shown):
    write(cfgdir, "folder = %s\n" % value)
    cfg, err = config.load()
    msg = "config folder '%s': needs a project-qualified path like Project/Folder" % shown
    assert config.load_error() == err == "invalid config %s: %s" % (config.CONFIG_PATH, msg)
    assert cfg.folder is None
