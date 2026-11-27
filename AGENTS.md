# Repository guidance

## Project
HEEPidermis is an X-HEEP-based RISC-V platform for physiological
signal acquisition. This repository includes hardware, embedded
firmware, a Python desktop GUI, and experimental tools.

Start with README.md and the documentation nearest the code being changed.

## Editing rules
- Make minimal modifications. Only edit or reformat files when necessary
  for the requested task. Avoid unrelated cleanup and refactoring.
- Do not modify RTL, templates (including *.tpl), or anything inside
  hw/vendor/. These may be read for reference.
- This restriction also applies to indirect changes made by generators,
  formatters, build commands, or dependency updates.
  Do not run make cheep-gen, make mcu-gen, make vendor-update, or
  util/format-verible: they modify protected files.
- If a task requires changes to protected files, explain the blocker.
- Keep code simple. This is research and testing software, not production
  infrastructure. Prefer straightforward implementations understandable
  by non-experts; avoid unnecessary abstractions and dependencies.
- Use descriptive names and explain non-obvious calculations, units,
  hardware assumptions, and timing behavior.
- Follow surrounding conventions and preserve unrelated user changes.
- Preserve recordings, calibration data, and local configuration.
  Do not overwrite them as part of testing.

## Repository map
- sw/applications/: embedded applications.
- sw/external/lib/: firmware libraries and SDKs.
- sw/gui/: Python acquisition, visualization, and analysis workspace.
- sw/gui/sdk/: device-specific integrations.
- sw/gui/tests/: interactive experimental tasks; read its AGENTS.md
  before working there.
- scripts/ and util/: analysis, build, and supporting tools.
- docs/: project documentation.
- config/: hardware configuration inputs.
- hw/ and tb/: hardware and simulation sources.
- build/: generated build artifacts.

## Development and validation
- Run top-level make commands from the repository root.
- Use make help to discover targets and sw/gui/README.md for GUI setup.
- make gui launches the desktop workspace.
- Inspect build prerequisites and output paths before running commands;
  some builds write through paths into hw/vendor/.
- Run the smallest relevant existing tests or checks.
  Add tests when they meaningfully verify changed behavior; avoid
  unnecessary test scaffolding.
- GUI tests may need Tk and a display; firmware and simulation checks
  need their respective toolchains.
- Report which checks ran, their results, and any checks blocked by
  missing tools, hardware, or the protected-file restrictions.
- Update nearby documentation when usage or behavior changes.

## Python environments
- The project's Python code runs inside conda environments, not the system
  Python. The project env is `heepidermis` (see environment.yml; Python 3.8),
  and variants like `heepidermis_test*` exist. Verify with
  `~/anaconda3/envs/<env>/bin/python --version`.
- Whenever code needs a new module or dependency, install it into the
  relevant conda environment(s) with their own pip (e.g.
  `~/anaconda3/envs/heepidermis/bin/pip install <package>`), covering every
  env that runs the affected code, and record it in the matching
  requirements file (`sw/gui/requirements.txt`,
  `sw/classification/requirements.txt`). Do not rely on system-site
  packages: a module importable by /usr/bin/python3 may still be missing
  where make gui actually runs.
- Keep new code compatible with the environments' Python (3.8): no 3.9+
  syntax (e.g. `list[str]` builtins generics without `from __future__
  import annotations`, `match` statements).
- Run tests with the environment interpreter when they exercise code whose
  dependencies live there, not only with the system Python.
