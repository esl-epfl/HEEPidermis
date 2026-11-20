#!/usr/bin/env python3
"""Run a GUI-standard test standalone from the command line.

Usage (from the repository root):

    python3 sw/gui/tests/run_test.py stroop --n-colors 4 --rest-time 10 --test-time 60
    python3 sw/gui/tests/run_test.py stroop --help        # all options of one test
    python3 sw/gui/tests/run_test.py stroop --save outs/stroop_001.json

Every field of the test's config dataclass becomes a ``--flag`` (dashes for
underscores).  Parameters (with a description for each) also live in the
editable HJSON file ``sw/gui/tests/<Test>/configs/defaults.hjson``: values
there are used as defaults, and an explicit ``--flag`` overrides the file.  ``--config``
points at a different settings file.  ``--save`` writes the full result
(config, per-trial events and summary) as JSON; without it only the summary
is printed.  Every event is also echoed to the terminal and appended to the
JSONL feed the GUI listens to (see AGENTS.md).

To add a new test: create ``sw/gui/tests/<Name>/`` following the standard in
``sw/gui/tests/AGENTS.md`` — it is then runnable here and appears in the GUI
without any GUI modification.
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # The sw/ folder.

from gui.tests import available_tests, load_test  # noqa: E402
from gui.tests.common import settings  # noqa: E402
from gui.tests.common.events import DEFAULT_PATH  # noqa: E402


def str_to_bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def add_config_arguments(parser, config_class) -> None:
    """Expose every config field as a --flag (the GUI forms do the same)."""
    for field in dataclasses.fields(config_class):
        flag = "--" + field.name.replace("_", "-")
        if field.type is bool or field.type == "bool":
            parser.add_argument(flag, type=str_to_bool, default=None,
                                metavar="true/false",
                                help=f"(default: {field.default})")
        elif isinstance(field.default, (tuple, list)):
            # Sequence fields (e.g. the color pool) take space-separated items.
            parser.add_argument(flag, type=lambda s: tuple(s.split()), default=None,
                                metavar="ITEM ...", help=f"(default: {' '.join(field.default)})")
        elif field.default is None:
            # Optional field (e.g. `int | None`): infer the type from the
            # annotation since there is no default value to take it from.
            ann = str(field.type)
            conv = (int if "int" in ann
                    else float if "float" in ann else str)
            parser.add_argument(flag, type=conv, default=None,
                                help=f"(default: {field.default})")
        else:
            parser.add_argument(flag, type=type(field.default), default=None,
                                help=f"(default: {field.default})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_test.py",
        description="Run an interactive test (fullscreen Tk window).")
    subparsers = parser.add_subparsers(dest="test", metavar="test")
    for entry in available_tests().values():
        if not entry["implemented"]:
            continue  # Placeholder folders are GUI-only.
        sub = subparsers.add_parser(entry["key"], help=f"Run the {entry['title']} test")
        add_config_arguments(sub, entry["spec"].Config)
        sub.add_argument("--config", metavar="FILE", default=None,
                         help="HJSON settings file to take the parameters from "
                              f"(default: {entry['folder'] / 'configs' / 'defaults.hjson'}; "
                              "--flags override the file).")
        sub.add_argument("--save", metavar="PATH", default=None,
                         help="Write the full result as JSON to this path.")
        sub.add_argument("--events", metavar="PATH", default=None,
                         help="JSONL event feed for the GUI "
                              f"(default: {DEFAULT_PATH}).")
    return parser


def defaults_file(test_key) -> Path:
    for entry in available_tests().values():
        if entry["key"] == test_key:
            return entry["folder"] / "configs" / "defaults.hjson"
    return Path("/dev/null")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not args.test:
        build_parser().print_help()
        return 2

    test_class = load_test(args.test)
    if test_class is None:
        print(f"No such test was found: {args.test}")
        return 1
    # Parameters from the settings file first, then the explicit --flags.
    overrides = settings.config_kwargs(
        args.test,
        settings.test_section(args.test,
                              settings.load_settings(args.config or defaults_file(args.test))),
        test_class.ConfigClass)
    overrides.update({key: value
                      for key, value in vars(args).items()
                       if key not in ("test", "save", "config", "events")
                      and value is not None})
    save = args.save

    test = test_class(**overrides)
    result = test.run(event_path=args.events)  # Blocks until the window closes (or Escape).

    if result is None:
        print("Test did not produce a result.")
        return 1
    print(f"\n{result.test} test {'ABORTED' if result.aborted else 'finished'}"
          f" · {result.duration:.1f} s")
    for key, value in result.summary.items():
        print(f"  {key}: {value}")
    if save:
        result.save(save)
        print(f"Result saved to {save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
