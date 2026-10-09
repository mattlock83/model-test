"""Read-only packaged examples, independent of the current working directory."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def asset(name):
    packaged = Path(__file__).parent / "data" / name
    return packaged if packaged.exists() else ROOT / name
