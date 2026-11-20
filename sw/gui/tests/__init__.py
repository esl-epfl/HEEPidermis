"""Discoverable tests for the measurement GUI.

Each subfolder of ``sw/gui/tests/`` is one test.  A folder with a
``spec.py`` (NAME, TITLE, DESCRIPTION, Config, FORM) and a ``test.py``
(exposing ``Test``) is a complete, runnable test; a folder without them is
a placeholder that still shows up in the GUI ("No such test was found").
The GUI only talks to this module and to the spec objects — it never needs
changes when a test is added.  See AGENTS.md for the full standard.
"""

import importlib
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
IGNORED = {"__pycache__", "common"}


def available_tests():
    """Scan the test folders (sorted by title).

    Returns ``{key: entry}`` where each entry has: ``key`` (unique test id),
    ``title``, ``description``, ``folder`` (Path), ``spec`` (module or None)
    and ``implemented`` (bool).  Placeholders get the folder name as
    title/key and ``implemented=False``.
    """
    tests = {}
    for folder in sorted(TESTS_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not folder.is_dir() or folder.name in IGNORED or folder.name.startswith((".", "_")):
            continue
        entry = {"key": folder.name.lower(), "title": folder.name,
                 "description": "Not implemented yet.", "folder": folder,
                 "spec": None, "implemented": False}
        try:
            module = importlib.import_module(f".{folder.name}.spec", __package__)
        except ModuleNotFoundError as error:
            if error.name == f"gui.tests.{folder.name}.spec":
                tests[entry["key"]] = entry  # Placeholder folder (e.g. MIST).
                continue
            raise  # A real dependency problem inside the spec must be loud.
        entry.update(key=getattr(module, "NAME", entry["key"]),
                     title=getattr(module, "TITLE", folder.name),
                     description=getattr(module, "DESCRIPTION", ""),
                     spec=module, implemented=True)
        tests[entry["key"]] = entry
    return tests


def load_test(key):
    """Return the runnable ``Test`` class of a discovered test, or None."""
    for entry in available_tests().values():
        if entry["key"] == key and entry["implemented"]:
            module = importlib.import_module(f".{entry['folder'].name}.test", __package__)
            return module.Test
    return None
