"""Minimal fallback logger used by the bundled Timeseries module.

If your original adc-emu logger.py is available, you can replace this file with it.
"""


def log(*args, **kwargs):
    print(*args, **kwargs)
