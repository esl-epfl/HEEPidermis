"""Physical-unit conversion: Timeseries -> conductance Timeseries in siemens.

HEEPidermis uses constant-current excitation: G = I / (V - offset).
Setz et al. (2010) used a different, constant-voltage acquisition circuit.
Algorithm and implementation references are listed in ../REFERENCES.md.
"""

import numpy as np

from ..tools.timeseries import Timeseries
from ..tools.ts_params import TSP_CHANNELS, TSP_MAGNITUDE, TSP_SUBJECT, TSP_UNITS


def normalized_conductance(conductance):
    """Conductance scaled by its mean + 3 sigma amplitude and clipped to 0-1.

    Per-recording normalization that removes the absolute set-up level and
    scale: scaling by the maximum would let single artifacts flatten the rest
    of the recording, while mean + 3 sigma keeps the outliers outside the
    scale and the clipping maps the remaining values to [0, 1]. NaN samples
    stay NaN.
    """
    values = np.asarray(conductance.data, dtype=float)
    finite = values[np.isfinite(values)]
    if not len(finite):
        raise ValueError("Conductance normalization requires at least one valid sample.")
    scale = finite.mean() + 3. * finite.std()
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("Conductance scale (mean + 3 sigma) must be positive.")
    ts = Timeseries(conductance.name, np.clip(values / scale, 0., 1.), conductance.time)
    ts.params.update(conductance.params)
    ts.params[TSP_MAGNITUDE], ts.params[TSP_UNITS] = "conductance", "normalized"
    return ts


def data_to_conductance_S(data):
    """Convert according to magnitude/units in params; preserve the subject name.

    Voltage inputs require a time-aligned 'current' channel. Optional 'offset'
    and 'valid' channels also remain Timeseries objects in TSP_CHANNELS.
    """
    magnitude, unit = data.params[TSP_MAGNITUDE], data.params[TSP_UNITS]
    channels = data.params.get(TSP_CHANNELS, {})

    def channel(name, expected_magnitude, scales):
        ts = channels[name]
        if ts.params[TSP_MAGNITUDE] != expected_magnitude or not np.array_equal(ts.time, data.time):
            raise ValueError(f"Channel {name} has incompatible magnitude or timestamps.")
        return ts.data * scales[ts.params[TSP_UNITS]]

    voltage_units = {"V": 1., "mV": 1e-3, "uV": 1e-6, "µV": 1e-6}
    if magnitude == "conductance":
        values = data.data * {"S": 1., "mS": 1e-3, "uS": 1e-6, "µS": 1e-6, "nS": 1e-9}[unit]
    elif magnitude == "resistance":
        resistance = data.data * {"ohm": 1., "kohm": 1e3, "Mohm": 1e6}[unit]
        values = np.full(len(data.time), np.nan)
        np.divide(1., resistance, out=values, where=resistance > 0)
    elif magnitude == "voltage":
        voltage = data.data * voltage_units[unit]
        if "offset" in channels:
            voltage = voltage - channel("offset", "voltage", voltage_units)
        current = channel("current", "current", {"A": 1., "mA": 1e-3, "uA": 1e-6, "µA": 1e-6})
        values = np.full(len(data.time), np.nan)
        np.divide(current, voltage, out=values, where=(voltage > 0) & (current > 0))
    else:
        raise ValueError(f"No conductance conversion registered for magnitude {magnitude!r}.")
    values[~np.isfinite(values) | (values <= 0)] = np.nan
    if "valid" in channels:
        valid = channel("valid", "validity", {"1": 1.})
        values[valid != 1] = np.nan
    result = Timeseries(data.name, values, data.time)
    result.params.update(data.params)
    result.params[TSP_MAGNITUDE], result.params[TSP_UNITS] = "conductance", "S"
    result.params.setdefault(TSP_SUBJECT, data.name)
    return result
