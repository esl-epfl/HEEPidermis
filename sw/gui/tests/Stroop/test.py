"""Runnable Stroop implementation (the adaptive-pressure variant).

The GUI and ``run_test.py`` import this module for its ``Test`` class; the
configuration contract (``Config``/``FORM``) stays in ``spec.py``.
"""

from .spec import StroopStressConfig as Config
from .stress import StroopStressTest as Test

__all__ = ["Test", "Config"]
