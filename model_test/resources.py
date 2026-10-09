"""Read-only packaged examples; writable native tools live outside site-packages."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def asset(name):
    packaged = Path(__file__).parent / "data" / name
    return packaged if packaged.exists() else ROOT / name


def tools_directory():
    if directory := os.environ.get("MODEL_TEST_HOME"):
        return Path(directory).expanduser().resolve()
    if (ROOT / "graphwalker.lock.json").exists() and (ROOT / "pyproject.toml").exists():
        return ROOT / ".tools"
    return Path.home() / ".cache" / "model-test"


def native_binary(name="graphwalker"):
    suffix = ".exe" if os.name == "nt" else ""
    return tools_directory() / "graphwalker-rs" / "target" / "release" / (name + suffix)
