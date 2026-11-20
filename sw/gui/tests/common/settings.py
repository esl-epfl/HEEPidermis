"""Editable test configuration stored in HJSON files.

Each test ships ``configs/defaults.hjson`` (e.g.
``sw/gui/tests/Stroop/configs/defaults.hjson``) listing every configurable
parameter with a short description; the same folder may hold any number of
user-saved configurations, which the GUI can load and save.  The same files
are reused by the GUI forms: :func:`load_settings` gives the raw sections,
:func:`config_kwargs` turns them into keyword arguments for a test's config
dataclass (``common`` < test section < explicit keyword/CLI overrides), and
:func:`save_settings` writes a configuration back to a file.

HJSON was chosen over JSON so the files stay hand-editable: comments,
unquoted strings and optional commas are all allowed.
"""

from __future__ import annotations

from dataclasses import fields as dc_fields
from pathlib import Path


def load_settings(path) -> dict:
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
    file = Path(path)
    if not file.exists():
        return {}
    data = hjson.loads(file.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def test_section(test_name: str, data: dict) -> dict:
    """Normalize a configuration payload to the ``common``/``<test>`` layout.

    Accepts the standard layout, plus hand-written files: a single
    differently-named (or old-named) section, or bare top-level keys with no
    section at all.  Standard files pass through unchanged.
    """
    if not isinstance(data, dict) or not data:
        return {}
    if test_name in data or "common" in data:
        return data
    if len(data) == 1 and isinstance(next(iter(data.values())), dict):
        return {test_name: next(iter(data.values()))}
    return {test_name: data}


def save_settings(path, section: str, values: dict) -> Path:
    """Write ``{section: values}`` as HJSON to ``path`` and return the path.

    Tuples are written as lists (HJSON has no tuple type); ``None`` becomes
    ``null``.  Existing files are never overwritten — the caller decides.
    """
    try:
        import hjson
    except ImportError as exc:  # pragma: no cover - dependency hint only
        raise ImportError(
            "Saving the test configuration requires the 'hjson' package "
            "(pip install hjson)") from exc
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    writable = {key: list(value) if isinstance(value, tuple) else value
                for key, value in values.items()}
    path.write_text(hjson.dumps({section: writable}), encoding="utf-8")
    return path


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
