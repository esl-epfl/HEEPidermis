#!/usr/bin/env python3
"""Run a cognitive test standalone from the command line.

Usage (from the repository root):

    python tests/run_test.py stroop --n-colors 4 --rest-time 10 --test-time 60
    python tests/run_test.py stroop --help        # all options for the test
    python tests/run_test.py stroop --save outs/stroop_001.json

Every field of the test's config dataclass becomes a ``--flag`` (dashes for
underscores).  Parameters (with a description for each) also live in the
editable HJSON file ``tests/cognitive/config.hjson``: values there are used
as defaults, and an explicit ``--flag`` overrides the file.  ``--config``
points at a different settings file.  ``--save`` writes the full result
(config, per-trial events and summary) as JSON; without it only the summary
is printed.

To add a new test: create ``tests/cognitive/<name>.py`` with a
``<Name>Config``/``<Name>Test`` pair and register it in
``tests/cognitive/__init__.py::TESTS``.  It is then runnable here and
embeddable in the GUI without changes.
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cognitive import TESTS, settings  # noqa: E402


def str_to_bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def add_config_arguments(parser, config_class) -> None:
    """Expose every config field as a --flag (GUI forms can reuse the idea)."""
    for field in dataclasses.fields(config_class):
        flag = "--" + field.name.replace("_", "-")
        if field.type is bool or field.type == "bool":
            parser.add_argument(flag, type=str_to_bool, default=None,
                                metavar="true/false",
                                help=f"(default: {field.default})")
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
        description="Run an interactive cognitive test (fullscreen Tk window).")
    subparsers = parser.add_subparsers(dest="test", metavar="test")
    for name, test_class in TESTS.items():
        sub = subparsers.add_parser(
            name, help=f"Run the {name} test")
        add_config_arguments(sub, test_class.ConfigClass)
        sub.add_argument("--config", metavar="FILE", default=None,
                         help="HJSON settings file to take the parameters "
                              "from (default: tests/cognitive/config.hjson; "
                              "--flags override the file).")
        sub.add_argument("--save", metavar="PATH", default=None,
                         help="Write the full result as JSON to this path.")
        sub.add_argument("--events", metavar="PATH", default=None,
                         help="JSONL event feed for the GUI (default: sw/gui/outs/stroop/events.jsonl).")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not args.test:
        build_parser().print_help()
        return 2

    test_class = TESTS[args.test]
    # Parameters from the settings file first, then the explicit --flags.
    overrides = settings.config_kwargs(
        args.test, settings.load_settings(args.config),
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
