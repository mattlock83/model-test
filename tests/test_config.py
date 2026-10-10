import os
import sys
from pathlib import Path

import pytest

from testwalker.config import RuntimeConfig
from testwalker.graphwalker import generate_path


def properties(tmp_path, extra="", key="unit-test-property-key"):
    path = tmp_path / "testwalker.properties"
    path.write_text(f"GRAPHWALKER_BIN={sys.executable}\nTYPESAFE_API_KEY={key}\n{extra}")
    return path


def test_explicit_file_wins_over_environment_and_keys_are_not_in_repr(tmp_path, monkeypatch):
    path = properties(tmp_path)
    monkeypatch.setenv("GRAPHWALKER_BIN", "/wrong/ambient/executable")
    monkeypatch.setenv("TYPESAFE_API_KEY", "wrong-ambient-key")
    config = RuntimeConfig.load(path, live=True)
    assert config.graphwalker == Path(sys.executable).resolve()
    assert config.api_key == "unit-test-property-key"
    assert config.api_key not in repr(config) and "wrong-ambient-key" not in repr(config)


def test_relative_quoted_paths_resolve_from_config_not_working_directory(tmp_path, monkeypatch):
    directory = tmp_path / "settings with spaces"
    directory.mkdir()
    binary = directory / "graph walker"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    path = directory / "settings.properties"
    path.write_text("GRAPHWALKER_BIN='graph walker'\nCHROME_PROFILE_DIR='chrome profile'\n")
    monkeypatch.chdir(tmp_path)
    config = RuntimeConfig.load(path)
    assert config.graphwalker == binary.resolve()
    assert config.chrome_profile == directory / "chrome profile"


def test_plan_uses_native_core_without_api_key(tmp_path):
    config = RuntimeConfig.load(properties(tmp_path, key=""))
    assert config.api_key == ""
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
        RuntimeConfig.load(config.path, live=True)


@pytest.mark.parametrize(
    "contents, message",
    [
        ("TESTWALKER_CORE_BIN=/does/not/exist\n", "existing executable"),
        ("GRAPHWALKER_BIN=/does/not/exist\n", "existing executable"),
        (f"GRAPHWALKER_BIN={sys.executable}\nGRAPHWALKER_BIN={sys.executable}\n", "Duplicate"),
        (f"GRAPHWALKER_BIN={sys.executable}\nCDP_URL=ftp://localhost\n", "CDP_URL"),
        (f"GRAPHWALKER_BIN={sys.executable}\nCHROME_DEBUG_PORT=abc\n", "CHROME_DEBUG_PORT"),
        (f"GRAPHWALKER_BIN={sys.executable}\nCHROME_DEBUG_PORT=80\n", "CHROME_DEBUG_PORT"),
        (f"GRAPHWALKER_BIN={sys.executable}\nSOME_SECRET=do-not-print-this\n", "Unknown"),
    ],
)
def test_invalid_config_is_rejected_without_printing_values(tmp_path, contents, message):
    path = tmp_path / "bad.properties"
    path.write_text(contents)
    with pytest.raises(ValueError, match=message) as error:
        RuntimeConfig.load(path)
    assert "do-not-print-this" not in str(error.value)


def test_missing_file_does_not_use_ambient_config(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPHWALKER_BIN", sys.executable)
    monkeypatch.setenv("TYPESAFE_API_KEY", "ambient-key")
    with pytest.raises(ValueError, match="Cannot read"):
        RuntimeConfig.load(tmp_path / "missing.properties", live=True)


def test_properties_are_literal_data_not_environment_interpolation(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET", "should-not-expand")
    config = RuntimeConfig.load(properties(tmp_path, key="literal-${SECRET}"), live=True)
    assert config.api_key == "literal-${SECRET}"


def test_native_runner_requires_explicit_executable_even_when_env_exists(model, tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPHWALKER_BIN", sys.executable)
    with pytest.raises(ValueError, match="properties file"):
        generate_path(model, tmp_path / "model.json")


def test_non_executable_binary_is_rejected(tmp_path):
    binary = tmp_path / "not-executable"
    binary.write_text("nothing")
    binary.chmod(0o600)
    if os.name == "nt":
        pytest.skip("Executable mode bits are not enforced on Windows")
    path = properties(tmp_path)
    path.write_text(f"GRAPHWALKER_BIN={binary}\n")
    with pytest.raises(ValueError, match="existing executable"):
        RuntimeConfig.load(path)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:abc",
        "http://localhost:65536",
        "http://localhost:0",
        "http://[broken",
        "http://user:do-not-print-this@localhost",
        "http://localhost/?token=do-not-print-this",
        "http://localhost/#fragment",
    ],
)
def test_invalid_debugging_urls_fail_during_config_loading(tmp_path, url):
    with pytest.raises(ValueError, match="CDP_URL") as error:
        RuntimeConfig.load(properties(tmp_path, extra=f"CDP_URL={url}\n"))
    assert "do-not-print-this" not in str(error.value)
