"""Compatibility imports for the unchanged HEEPidermis analysis API."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui_common.eda import (ALGORITHMS, DLC_INTERPOLATION_MODES, analyze, decompose,
                            diagonals, factorization, interpolate_signal, inverse_matrix)
