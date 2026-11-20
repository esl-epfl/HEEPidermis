"""Editable test configuration stored in an HJSON file.

``tests/cognitive/config.hjson`` lists every configurable parameter of every
registered test, each with a short description.  The same file is meant to be
reused by the future GUI forms: :func:`load_settings` gives the raw sections,
:func:`config_kwargs` turns them into keyword arguments for a test's config
dataclass (``common`` < test section < explicit keyword/CLI overrides).

HJSON was chosen over JSON so the file stays hand-editable: comments,
unquoted strings and optional commas are all allowed.
"""

from __future__ import annotations

from dataclasses import fields as dc_fields
from pathlib import Path

# Shipped default; any path can be passed instead.
DEFAULT_FILE = Path(__file__).with_name("config.hjson")


def load_settings(path=None) -> dict:
    """Parse an HJSON settings file into ``{"common": {...}, "<test>": {...}}``.

    Sections (and the whole file) are optional; an empty ``{}`` means "use
    every built-in default".
    """
    try:
        import hjson
    except ImportError as exc:  # pragma: no cover - dependency hint only
        raise ImportError(
            "Reading the test configuration requires the 'hjson' package "
            "(pip install hjson)") from exc
    file = Path(path) if path else DEFAULT_FILE
    if not file.exists():
        return {}
    data = hjson.loads(file.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def config_kwargs(test_name: str, settings: dict, config_class) -> dict:
    """Merge the ``common`` and ``<test_name>`` sections into config kwargs.

    Unknown parameter names raise ``ValueError`` (they are almost always
    typos).  Values are lightly coerced where the config expects a tuple
    (``color_pool`` accepts a list of names or a ``name: hex`` object).
    """
    merged: dict = {}
    merged.update(settings.get("common") or {})
    section = settings.get(test_name) or {}
    merged.update(section)

    known = {f.name: f for f in dc_fields(config_class)}
    kwargs: dict = {}
    for key, value in merged.items():
        if key not in known:
            where = f"section '{test_name}'" if key in section else "'common'"
            raise ValueError(
                f"Unknown parameter '{key}' in the {where} of the test "
                f"configuration (valid: {', '.join(sorted(known))})")
        if key == "color_pool" and isinstance(value, dict):
            # {name: hex} form: names in file order, hexes as overrides.
            kwargs[key] = tuple(value)
            if "color_hexes" in known:
                kwargs["color_hexes"] = {n: str(h) for n, h in value.items()}
            continue
        kwargs[key] = _coerce(key, value)
    return kwargs


def _coerce(key: str, value):
    """Convert HJSON values into what the config dataclasses expect."""
    if key == "color_pool":
        if isinstance(value, str):
            return tuple(value.split())  # single string -> names
        return tuple(value)
    return value
