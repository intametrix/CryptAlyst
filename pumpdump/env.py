"""Minimal ``.env`` loader (no third-party dependency).

Reads ``KEY=VALUE`` lines, ignores blanks and ``#`` comments, and never
overrides a variable that already exists in the real environment -- so
``FOO=bar python run.py`` beats ``FOO=baz`` in a ``.env`` file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, Optional

_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off", ""}


def parse_env_file(path: os.PathLike | str) -> Dict[str, str]:
    """Parse a ``.env`` file into a dict. Missing file -> empty dict."""
    out: Dict[str, str] = {}
    p = Path(path)
    if not p.is_file():
        return out
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # Strip matching single/double quotes, and a trailing inline comment
        # only when the value is unquoted.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key] = value
    return out


def load_env(path: Optional[os.PathLike | str] = None) -> Dict[str, str]:
    """Load ``path`` (default ``./.env``) into ``os.environ``, then return it."""
    file_values = parse_env_file(path if path is not None else ".env")
    for key, value in file_values.items():
        os.environ.setdefault(key, value)
    return file_values


# --------------------------------------------------------------------------
# Typed accessors used by config.py
# --------------------------------------------------------------------------

def get_str(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def get_float(name: str, default: float) -> float:
    raw = get_str(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def get_int(name: str, default: int) -> int:
    raw = get_str(name)
    if raw is None:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def get_bool(name: str, default: bool) -> bool:
    raw = get_str(name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    return default


def get_list(name: str, default: Iterable[str]) -> list:
    raw = get_str(name)
    if raw is None:
        return list(default)
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def mask(secret: Optional[str]) -> str:
    """Render a secret safely for logs: ``abcd...wxyz`` or ``(unset)``."""
    if not secret:
        return "(unset)"
    if len(secret) <= 10:
        return secret[0] + "*" * (len(secret) - 1)
    return f"{secret[:4]}...{secret[-4:]} (len={len(secret)})"
