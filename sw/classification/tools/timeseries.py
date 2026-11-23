# Copyright 2024 EPFL
# Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# Author: Juan Sapriza - juan.sapriza@epfl.ch

import numpy as np
from copy import deepcopy
import pickle
import hashlib
from .logger import *
from scipy.interpolate import interp1d

from .ts_params import *

class Timeseries:
    """
    Class for representing time series data.

    Attributes:
        name (str): Name of the time series.
        data (list): List of data points.
        time (list): List of time points.
        f_Hz (float): Frequency of the time series.
        length_s (float): Length of the time series in seconds.
        sample_b (int): Size per sample in bits.
        params (dict): Dictionary containing additional parameters.
        scores (dict): Dictionary containing scores associated with the time series.
    """

    def __init__(self,
                name,
                data=None,
                time=None,
                f_Hz=0,
                length_s=0,
                ):
        """
        Initializes a TimeSeries object.

        Args:
            name (str): Name of the time series.
            data (list, optional): List of data points. Defaults to None.
            time (list, optional): List of time points. Defaults to None.
            f_Hz (float, optional): Frequency of the time series. Defaults to 0.
            length_s (float, optional): Length of the time series in seconds. Defaults to 0.
            sample_b (int, optional): Size per sample in bits. Defaults to 0.
        """
        self.name = name
        self.data = np.array(data, dtype=np.float64) if data is not None else np.array([], dtype=np.float64)
        self.time = np.array(time, dtype=np.float64) if time is not None else np.array([], dtype=np.float64)

        if time is None and length_s != 0 and len(self.data):
            T_s = length_s / len(data)
            f_Hz = 1.0 / T_s
            self.time = np.arange(len(self.data), dtype=np.float64) * T_s
        elif time is not None and f_Hz == 0 and len(time) > 1:
            f_Hz = 1.0 / np.median(np.diff(self.time))
            length_s = time[-1] - time[0] + 1 / f_Hz
        elif time is not None and f_Hz > 0:
            length_s = len(time) / f_Hz

        self.params = {
            TSP_F_HZ: f_Hz,
            TSP_LENGTH_S: length_s,
            TSP_STEP_HISTORY: [],
            TSP_LATENCY_HISTORY: [],
        }


    def __str__(self):
        """
        Returns a string representation of the time series object.
        """
        return self.name

    def __getstate__(self):
        """Store numeric arrays as plain lists for NumPy-version-portable pickles."""
        state = self.__dict__.copy()
        state["data"] = np.asarray(self.data).tolist()
        state["time"] = np.asarray(self.time).tolist()
        return state

    def __setstate__(self, state):
        """Rebuild local NumPy arrays when a Timeseries pickle is loaded."""
        self.__dict__.update(state)
        self.data = np.asarray(self.data, dtype=np.float64)
        self.time = np.asarray(self.time, dtype=np.float64)

    def export(self, path="../out/", name=""):
        """
        Exports the time series data to a text file.

        Args:
            path (str, optional): Path to export the file. Defaults to "../out/".
            name (str, optional): Name of the exported file. Defaults to "".
        """
        if name == "":
            name = self.name.replace(" ", "_")
        with open(path + name + ".timeseries", 'w+') as f:
            for t, d in zip(self.time, self.data):
                f.write(f"{t}, {d}\n")

    def export_bin(self, outfile="../out/", bytes=4, bigendian=False):
        """
        Exports the time series data to a binary file.

        Args:
            outfile (str, optional): Path to export the file. Defaults to "../out/".
            bytes (int, optional): Size of each data point in bytes. Defaults to 4.
            bigendian (bool, optional): Indicates whether to use big-endian byte order. Defaults to False.
        """
        if outfile == "../out/":
            outfile += self.name.replace(" ", "_")
        # Save the array to a binary file
        if bytes == 4:
            wordsize = np.int32
        elif bytes == 2:
            wordsize = np.int16
        elif bytes == 1:
            wordsize = np.int8
        else:
            raise ValueError("Invalid word size in bytes! Choose between 1, 2 and 4")
        data = np.array(self.data).astype(wordsize)
        with open(outfile + ".bin", 'wb') as f:
            if bigendian:
                data.byteswap(True).tofile(f)
            else:
                data.tofile(f)

    def dump(self, path="../out/", name=""):
        """
        Dumps the TimeSeries object to a pickle file.

        Args:
            path (str, optional): Path to dump the file. Defaults to "../out/".
            name (str, optional): Name of the dumped file. Defaults to "".
        """
        if name == "":
            name = self.name.replace(" ", "_")
        from copy import deepcopy as cp
        cop = cp(self)
        cop.data = np.asarray(cop.data)
        with open(path + name + ".pkl", 'wb') as f:
            pickle.dump(cop, f)

    @classmethod
    def load(cls, filename):
        """
        Loads a TimeSeries object from a pickle file.

        Args:
            filename (str): Path to the pickle file.

        Returns:
            Timeseries: Loaded TimeSeries object.
        """
        with open(filename, 'rb') as f:
            return pickle.load(f)

    def copy(self):
        """
        Creates a deep copy of the TimeSeries object.

        Returns:
            Timeseries: Deep copy of the TimeSeries object.
        """
        return deepcopy(self)

    def print_params(self):
        """
        Prints the parameters of the TimeSeries object.
        """
        for k, v in self.params.items():
            log(f"{k}\t{v}")

    def accumulate_param(self,  k, v):
        self.params[k] = self.params.get(k, 0) + v

    def min_bits_required(self):
        """
        Calculates the minimum number of bits required for data and time points.

        Returns:
            tuple: A tuple containing the minimum number of bits required for data and time points, respectively.
        """
        try:
            max_d, max_t = int(max(self.data)), int(max(self.time))
        except:
            return 0, 0
        d_b, t_b = max_d.bit_length(), max_t.bit_length()
        return d_b, t_b

    def generate_unique_id(self):
        """
        Generates a unique ID for the timeseries by encoding its parameters keys and values.
        It excludes the ID from the parameters to be encoded.
        """
        filtered_params = {key: value for key, value in self.params.items() if key != 'TSP_ID'}
        params_string = str(sorted(filtered_params.items())).encode()
        self.params[TSP_ID] = hashlib.md5(params_string).hexdigest()
        self.params[TSP_SHORT_ID] = self.params[TSP_ID][-5:]

    def limit_to_metadata(self):
        self.len_time = len(self.time)
        self.len_data = len(self.data)
        self.time = []
        self.data = []

def copy_series(series):
    """
    Creates a deep copy of the input time series.

    Args:
        series (Timeseries): Input time series to be copied.

    Returns:
        Timeseries: Deep copy of the input time series.
    """
    return deepcopy(series)

catalog = {}

def reset_catalog():
    catalog = {}

def update_catalog( series_list ):
    # Create a dictionary to map IDs to TimeSeries objects for O(1) lookup time.
    catalog_additions = {series.params[TSP_ID]: series for series in series_list}
    catalog.update(catalog_additions)

def get_series_by_id(id):
    """
    Given a catalog of TimeSeries objects and a target ID, find and return the TimeSeries with that ID.

    Args:
        catalog (list of TimeSeries): The list containing TimeSeries objects.
        id (str): The unique identifier to search for.

    Returns:
        TimeSeries: The TimeSeries object with the matching ID or None if not found.
    """
    # Return the TimeSeries object with the given ID, or None if the ID is not in the dictionary.
    return catalog.get(id, None)

def save_series(series, filename, input_series = []):
    import dill as pickle
    log("💾 Saving...")
    sizes_MB    = np.array([len(pickle.dumps(s.data))+len(pickle.dumps(s.time)) for s in series])/(1024**2)
    summ_MB     = len(pickle.dumps(series))/(1024**2)
    avg_size_MB = np.average(sizes_MB)
    std_size_MB = np.std(sizes_MB)
    log(f"Size: {summ_MB:0.1f} MB, {len(sizes_MB)} × avg: {avg_size_MB*1024:0.1f} kB (std: {std_size_MB*1024*1024:0.1f} B)")

    with open( filename, 'wb') as f:
        pickle.dump( series, f, protocol=pickle.HIGHEST_PROTOCOL)

    log(f"Steps saved in {filename}")


def load_series(filename):
    import dill as pickle
    log("📂 Loading...")
    with open(filename, "rb") as f:
        series = pickle.load(f)

    try: update_catalog( series )
    except: pass
    return series

def filter_timeseries(timeseries_list, params_dict):
    def matches_params(timeseries, params_dict):
        if timeseries is None: return False
        for key, value in params_dict.items():
            if isinstance(value, list):
                # Check if any of the values in the list match the timeseries params
                if key not in timeseries.params or not any(item in timeseries.params[key] for item in value):
                    return False
            else:
                # Check if the single value matches the timeseries params
                if key not in timeseries.params or timeseries.params[key] != value:
                    return False
        return True

    filtered_timeseries = [ts for ts in timeseries_list if matches_params(ts, params_dict)]
    return filtered_timeseries



def crop_series_time_percentile( series, percentile ):
    o = Timeseries(f"{series.name} cropped to {percentile}%")
    o.params.update(series.params)

    length_n    = len(series.time)
    start_n     = int(length_n*(100-percentile)/200)
    end_n       = length_n - int(length_n*(100-percentile)/200)

    o.time = series.time[start_n:end_n]
    o.data = series.data[start_n:end_n]
    o.params[TSP_LENGTH_S] = o.time[-1] - o.time[0]
    return o.copy()

def interpolate_series_to_match( series, ref ):
    o                   = Timeseries(f"{series.name} interpolated to match {ref.name}")
    o.time              = np.linspace(ref.time[0], ref.time[-1], num=len(ref.time))
    o.data              = np.array( interp1d(series.time, series.data, bounds_error=False, fill_value="extrapolate")(o.time) )
    o.params.update(series.params)
    o.params[TSP_F_HZ]  = ref.params[TSP_F_HZ]
    return o.copy()

def zero_mean_unit_variance(series):
    o = Timeseries(f"{series.name} w/0m1v")
    mean    = np.mean( series.data )
    std     = np.std( series.data )
    o.data  = (series.data - mean)/std
    o.time  = series.time
    o.params.update(series.params)
    return o.copy()

def compute_rmse(series, ref):
    diffs   = np.array(series.data) - np.array(ref.data)
    mse     = np.mean(diffs ** 2)
    rmse    = np.sqrt(mse)
    rmse_db = -20 * np.log10(rmse) if rmse != 0 else float('inf')  # Handle rmse == 0 case
    return rmse_db

