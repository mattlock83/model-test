"""Explicit runtime properties; no executable or credential discovery from the environment."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv.parser import parse_stream

KEYS = {
    "GRAPHWALKER_BIN",
    "TYPESAFE_API_KEY",
    "TYPESAFE_MODEL",
    "CDP_URL",
    "CHROME_EXECUTABLE",
    "CHROME_PROFILE_DIR",
    "CHROME_DEBUG_PORT",
    "TEXT_MODEL_API_KEY",
    "TEXT_MODEL_BASE_URL",
    "TEXT_MODEL",
}


def resolve_path(value, root):
    path = Path(value).expanduser()
    return (root / path).resolve()


def executable(value, root, key):
    path = resolve_path(value, root)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f"{key} must name an existing executable file in the properties file")
    return path


def parse_http_url(value, label):
    """Check URL syntax without including potentially sensitive values in errors."""
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and (parsed.port is None or 1 <= parsed.port <= 65535)
            and not any(character.isspace() for character in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"Supply a valid HTTP(S) {label}")
    return parsed


def debugging_url(value):
    """Validate the property and CLI override using the same endpoint contract."""
    parsed = parse_http_url(value, "CDP_URL")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("CDP_URL must be an HTTP(S) endpoint without credentials, query or fragment")
    return value.rstrip("/")


def read_properties(path):
    values = {}
    try:
        with path.open(encoding="utf-8") as stream:
            for entry in parse_stream(stream):
                if entry.error:
                    raise ValueError(f"Invalid property syntax at line {entry.original.line}")
                if entry.key is None:
                    continue
                if entry.key not in KEYS:
                    raise ValueError(f"Unknown configuration property at line {entry.original.line}")
                if entry.key in values:
                    raise ValueError(f"Duplicate configuration property: {entry.key}")
                values[entry.key] = entry.value or ""
    except (OSError, UnicodeError):
        raise ValueError("Cannot read the properties file; supply --config PATH") from None
    return values


@dataclass(frozen=True)
class RuntimeConfig:
    path: Path
    graphwalker: Path | None
    api_key: str = field(repr=False)
    model: str = "jev-latest"
    cdp_url: str = ""
    chrome_executable: Path | None = None
    chrome_profile: Path | None = None
    chrome_port: int = 9222
    text: dict = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, path, *, live=False, discovery=False):
        path = Path(path).expanduser().resolve()
        values = read_properties(path)
        if not discovery and not values.get("GRAPHWALKER_BIN"):
            raise ValueError("Set GRAPHWALKER_BIN in the properties file to your GraphWalker executable")
        binary = None if discovery else executable(values["GRAPHWALKER_BIN"], path.parent, "GRAPHWALKER_BIN")
        if live and not values.get("TYPESAFE_API_KEY", "").strip():
            raise ValueError("Set TYPESAFE_API_KEY in the same properties file for live Jev testing")
        cdp_url = values.get("CDP_URL", "")
        try:
            port = int(values.get("CHROME_DEBUG_PORT", "9222"))
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise ValueError("CHROME_DEBUG_PORT must be an integer from 1024 through 65535") from None
        chrome = values.get("CHROME_EXECUTABLE")
        profile = values.get("CHROME_PROFILE_DIR")
        return cls(
            path=path,
            graphwalker=binary,
            api_key=values.get("TYPESAFE_API_KEY", ""),
            model=values.get("TYPESAFE_MODEL") or "jev-latest",
            cdp_url=debugging_url(cdp_url) if cdp_url else "",
            chrome_executable=executable(chrome, path.parent, "CHROME_EXECUTABLE") if chrome else None,
            chrome_profile=resolve_path(profile, path.parent) if profile else None,
            chrome_port=port,
            text={key: value for key, value in values.items() if key.startswith("TEXT_MODEL")},
        )
