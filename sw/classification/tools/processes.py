# Copyright 2024 EPFL
# Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
#
# Author: Juan Sapriza - juan.sapriza@epfl.ch

from scipy.signal import butter, filtfilt, cheby1, bessel, ellip
from scipy import interpolate
import numpy as np

from .timeseries import *

def pas(series, e):
    """
    Implement a Polygonal Approximator.

    Args:
        series (Timeseries): Input time series.
        e (float): Error threshold for compression.

    Returns:
        Timeseries: Polygonally approximated time series.
    """
    data = series.data
    time = series.time
    dx = 1  # Time differential
    f = 0  # Cost function
    x = 0  # Time sample
    y = 0  # Value sample
    t_ = 0  # Time of the previous fiducial point
    p = 0  # Time of a peak
    l = 0  # Length
    o_data = []  # Output data
    o_time = []  # Output time

    for i in range(1, len(time)):
        dy = data[i] - data[i - 1]
        x += dx
        y += dy
        f = f + x * dx + y * dy
        displ = abs(y) + x
        if displ < l and p == 0:
            p = i - 1
        l = displ
        if abs(f) > e:  # ToDo: Maybe adjust e to reach a certain level of compression?
            t = i - 1 if p == 0 else p
            o_data.append(data[t])
            o_time.append(time[t])
            f = 0
            p = 0
            x = (i - t) * dx
            y = data[i] - data[t]
            t_ = t
            l = abs(y) + x

    o = Timeseries(series.name + " PAS")
    o.data = np.array(o_data)
    o.time = np.array(o_time)
    return o.copy()

def neo(series, win):
    """
    NEO operator.

    Args:
        series (Timeseries): Input time series.
        win (float): Window size.

    Returns:
        Timeseries: Time series after NEO operation.
    """
    o = Timeseries(series.name + " NEO")
    o.params[TSP_F_HZ] = series.params[TSP_F_HZ]
    t_diff = int(o.params[TSP_F_HZ] * win)
    o_data = []
    o_time = []

    for i in range(t_diff, len(series.data)):
        dx = series.time[i] - series.time[i - t_diff]
        dy = series.data[i] - series.data[i - t_diff]
        dydx = dy / dx
        neo_value = dydx**2 - series.data[i] * dydx
        o_data.append(neo_value)
        o_time.append(series.time[i])

    o.data = np.array(o_data)
    o.time = np.array(o_time)
    return o.copy()

def aso(series, win):
    """
    Amplitude Slope Operator (ASO).

    Args:
        series (Timeseries): Input time series.
        win (float): Window size.

    Returns:
        Timeseries: Time series after ASO operation.
    """
    o = Timeseries(series.name + " ASO")
    o.params[TSP_F_HZ] = series.params[TSP_F_HZ]
    t_diff = int(o.params[TSP_F_HZ] * win)
    o_data = []
    o_time = []

    for i in range(1, len(series.data)):
        dx = series.time[i] - series.time[i - t_diff]
        dy = series.data[i] - series.data[i - t_diff]
        dydx = dy / dx
        aso_value = series.data[i] * dydx
        o_data.append(aso_value)
        o_time.append(series.time[i])

    o.data = np.array(o_data)
    o.time = np.array(o_time)
    return o.copy()

def as2o(series, win):
    """
    Amplitude Slope Squared Operator (AS2O).

    Args:
        series (Timeseries): Input time series.
        win (float): Window size.

    Returns:
        Timeseries: Time series after AS2O operation.
    """
    o = Timeseries(series.name + " AS2O")
    o.params[TSP_F_HZ] = series.params[TSP_F_HZ]
    t_diff = int(o.params[TSP_F_HZ] * win) if o.params[TSP_F_HZ] != 0 else win
    o_data = []
    o_time = []

    for i in range(t_diff, len(series.data)):
        dx = series.time[i] - series.time[i - t_diff]
        dy = series.data[i] - series.data[i - t_diff]
        dydx = dy / dx
        as2o_value = series.data[i] * dydx**2
        o_data.append(as2o_value)
        o_time.append(series.time[i])

    o.data = np.array(o_data)
    o.time = np.array(o_time)
    return o.copy()

def needle(series, win):
    """
    Needle function to detect inflection points and compute corresponding values.

    Args:
        series (Timeseries): Input time series.
        win (float): Window size.

    Returns:
        Timeseries: Processed time series.
    """
    o = Timeseries(series.name + " needle'd")
    o.params[TSP_F_HZ] = series.params[TSP_F_HZ]
    t_diff = int(o.params[TSP_F_HZ] * win) if o.params[TSP_F_HZ] != 0 else win
    k = Timeseries("inflections")

    d = np.zeros(len(series.data))
    d[0] = 0
    o_data = [1]
    o_time = [0]

    for j in range(1, len(series.data)):
        d[j] = 1 if series.data[j] - series.data[j - t_diff] > 0 else -1
        if d[j] * d[j - 1] < 0:
            k.data = np.append(k.data, j - 1)

    for i in range(1, len(k.data)):
        dx = series.time[int(k.data[i])] - series.time[int(k.data[i - 1])]
        dy = series.data[int(k.data[i])] - series.data[int(k.data[i - 1])]
        needle_value = (dy**2) / dx
        o_data.append(needle_value)
        o_time.append(series.time[int(k.data[i])])

    o.data = np.array(o_data)
    o.time = np.array(o_time)
    return o.copy()

def ac_couple(series, win):
    """
    AC coupling function to remove DC offset.

    Args:
        series (Timeseries): Input time series.
        win (int): Window size.

    Returns:
        Timeseries: AC coupled time series.
    """
    if win == 0:
        return series.copy()

    o = Timeseries(series.name + " AC coupled")
    o.params.update(series.params)
    o_data = []
    o_time = []

    for i in range(win, len(series.time)):
        m = np.mean(series.data[i - win:i])
        ac_value = series.data[i] - m
        o_data.append(ac_value)
        o_time.append(series.time[i])

    o.data = np.array(o_data)
    o.time = np.array(o_time)
    return o.copy()

def mean_sub(series, win):
    """
    Mean subtraction function to remove mean over a window.

    Args:
        series (Timeseries): Input time series.
        win (int): Window size.

    Returns:
        Timeseries: Time series with mean subtracted.
    """
    o = Timeseries(series.name + " Mean subtracted")
    o.params.update(series.params)
    o_data = []
    o_time = []

    for i in range(win, len(series.time)):
        m = np.mean(series.data[i - win:i])
        mean_sub_value = series.data[i] - m
        o_data.append(mean_sub_value)
        o_time.append(series.time[i])

    o.data = np.array(o_data)
    o.time = np.array(o_time)
    return o.copy()

def pseudo_mean(series, bits):
    """
    Calculate pseudo mean for the input time series.

    Args:
        series (Timeseries): Input time series.
        bits (int): Number of bits for scaling.

    Returns:
        Timeseries: Time series with pseudo mean calculated.
    """
    o = Timeseries(series.name + " pMean")
    o.params.update(series.params)
    m = int(series.data[0])
    mb = int(series.data[0]) << bits
    o_data = []
    o_time = []

    for i in range(len(series.time)):
        mb = int(mb - m + series.data[i])  # m[i]xb = m[i-1]xb - m[i-1] + s[i]]
        m = mb >> bits  # m[i] = m[i]xb /b
        o_data.append(m)
        o_time.append(series.time[i])

    o.data = np.array(o_data)
    o.time = np.array(o_time)
    return o.copy()

def lpf_butter(series, cutoff, order):
    """
    Apply a low-pass Butterworth filter to the input time series.

    Args:
        series (Timeseries): Input time series.
        cutoff (float): Cutoff frequency for the low-pass filter.
        order (int): Order of the Butterworth filter.

    Returns:
        Timeseries: Low-pass filtered time series.
    """
    o = Timeseries(series.name + " LPF")
    o.params.update(series.params)

    # Normalize the cutoff frequency with respect to the Nyquist frequency
    nyquist = 0.5 * series.params[TSP_F_HZ]
    normal_cutoff = cutoff / nyquist

    # Create Butterworth filter coefficients
    b, a = butter(order, normal_cutoff, btype='low', analog=False)

    # Apply the filter to the data
    o.data = filtfilt(b, a, series.data)
    o.time = series.time

    return o.copy()

def lpf_brickwall(series, cutoff):
    """
    Apply an ideal brick-wall low-pass filter to the input time series.

    Args:
        series (Timeseries): Input time series.
        cutoff (float): Cutoff frequency for the low-pass filter.

    Returns:
        Timeseries: Brick-wall low-pass filtered time series.
    """
    o = Timeseries(series.name + " Brickwall LPF")
    o.params.update(series.params)

    fs = series.params[TSP_F_HZ]
    nyquist = 0.5 * fs

    if cutoff <= 0 or cutoff > nyquist:
        raise ValueError(f"Cutoff must be between 0 and Nyquist frequency ({nyquist} Hz)")

    # Transform to frequency domain
    spectrum = np.fft.rfft(series.data)
    frequencies = np.fft.rfftfreq(len(series.data), d=1/fs)

    # Ideal brick-wall filtering
    spectrum[frequencies > cutoff] = 0

    # Transform back to time domain
    o.data = np.fft.irfft(spectrum, n=len(series.data))
    o.time = series.time

    return o.copy()

def butter_bandpass(lowcut, highcut, fs, order=4):
    """
    Create Butterworth bandpass filter coefficients.

    Args:
        lowcut (float): Lower cutoff frequency.
        highcut (float): Upper cutoff frequency.
        fs (float): Sampling frequency.
        order (int, optional): Order of the Butterworth filter. Defaults to 4.

    Returns:
        tuple: Filter coefficients (b, a).
    """
    nyquist = 0.5 * fs
    low = lowcut / nyquist
    high = highcut / nyquist

    # Create Butterworth bandpass filter coefficients
    b, a = butter(order, [low, high], btype='band')

    return b, a

def bpf_filter(series, lowcut, highcut, order=4, filter_type='butter'):
    """
    Apply a specified band-pass filter to the input time series.

    Args:
        series (Timeseries): Input time series.
        lowcut (float): Lower cutoff frequency for the band-pass filter.
        highcut (float): Upper cutoff frequency for the band-pass filter.
        order (int, optional): Order of the filter. Defaults to 4.
        filter_type (str, optional): Type of the filter ('butter' or 'cheby'). Defaults to 'butter'.

    Returns:
        Timeseries: Band-pass filtered time series.
    """
    o = Timeseries(series.name + " BPF")
    o.params.update(series.params)

    fs = series.params[TSP_F_HZ]  # Assume the sampling frequency is stored here

    # Select the filter type based on the input
    if filter_type == 'butter':
        b, a = butter(order, [lowcut, highcut], fs=fs, btype='band')
    elif filter_type == 'cheby':
        b, a = cheby1(order, 0.5, [lowcut, highcut], fs=fs, btype='band')
    elif filter_type == 'bessel':
        b, a = bessel(order, [lowcut, highcut], fs=fs, btype='band', norm='phase')
    elif filter_type == 'ellip':
        b, a = ellip(order, 0.5, 40, [lowcut, highcut], fs=fs, btype='band')  # 40 dB stopband attenuation
    else:
        raise ValueError("Unsupported filter type. Choose 'butter' or 'cheby'.")

    # Apply the filter to the data
    o.data = filtfilt(b, a, series.data)
    o.time = series.time

    return o.copy()


def add_offset(series, offset):
    """
    Add a constant offset to the input time series.

    Args:
        series (Timeseries): Input time series.
        offset (float): Offset to be added to the data.

    Returns:
        Timeseries: Time series with added offset.
    """
    # Add offset to the data
    offset_data = np.array(series.data) + offset

    o       = Timeseries(f"{series.name} offset {offset}", time=series.time )
    o.params.update(series.params)
    o.data  = offset_data

    return o.copy()


def offset_to_pos_and_map(series, bits):
    """
    Convert offset to positive values and map them to the specified number of bits.

    Args:
        series (Timeseries): Input time series.
        bits (int): Number of bits for mapping.

    Returns:
        Timeseries: Timeseries with positive offset values mapped to the specified number of bits.
    """
    o = Timeseries(series.name + " map abs", time=series.time, f_Hz=series.params[TSP_F_HZ])
    o.params.update(series.params)
    o.params[TSP_SAMPLE_B] = bits

    # Ensure data is a numpy array of type float32
    data = np.array(series.data, dtype=np.float32)

    # Push everything above 0
    minv = np.min(data)
    if minv < 0:
        data -= minv  # Vectorized operation to shift all data points

    maxd = np.max(data)
    max_val = (2 ** bits) / 2 - 1

    # Map values to the specified number of bits
    o.data = np.clip(max_val * data / maxd, -max_val, max_val)

    return o.copy()


def spike_det_lc(series, dt, count):
    """
    Spike detection using a Level Crossing algorithm.

    Args:
        series (Timeseries): Input time series.
        dt (float): Time difference threshold for burst detection.
        count (int): Number of consecutive data points to consider for burst detection.

    Returns:
        Timeseries: Detected spikes time series.
    """
    o = Timeseries("sDETlc")  # Initialize output Timeseries
    data = series.data
    time = series.time

    # Filter out zero values from the data and corresponding time points
    non_zero_data = [d for d in data if d != 0]
    non_zero_time = [t for t, d in zip(time, data) if d != 0]
    o_time = []

    # Iterate over the data points to detect bursts
    for i in range(count, len(non_zero_data)):
        if all(d == non_zero_data[i] for d in non_zero_data[i - count + 1: i + 1]):  # Check for burst
            if non_zero_time[i] - non_zero_time[i - count] < dt:  # Check burst duration
                o_time.append(non_zero_time[i])

    o.time = np.array(o_time)
    return o.copy()


def oversample(series, order, interpolation=interpolate.interp1d):
    """
    Oversample the input time series by a given order using cubic spline interpolation.

    Args:
        series (Timeseries): Input time series.
        order (int): Oversampling order.

    Returns:
        Timeseries: Oversampled time series.
    """
    # Create a new Timeseries object for oversampled data
    o = Timeseries(f"Sx{order}")

    # Update parameters for oversampled data
    o.params.update(series.params)
    o.params[TSP_F_HZ] = series.params[TSP_F_HZ] * order

    # Cubic spline interpolation of the input data to oversample
    spline = interpolation(series.time, series.data)
    num_points = int((series.time[-1] - series.time[0]) * o.params[TSP_F_HZ]) + 1
    o.time = np.linspace(series.time[0], series.time[-1], num_points)
    o.data = spline(o.time)

    return o.copy()


def compute_sdr(original_signal, new_signal, interpolate=False):
    """
    Compute Signal-to-Distortion Ratio (SDR) between the original signal and the new signal.

    Args:
        original_signal (ndarray): Original signal.
        new_signal (ndarray): New signal.
        interpolate (bool, optional): Whether to interpolate the new signal to match the length of the original signal.
                                      Defaults to False.

    Returns:
        float: Signal-to-Distortion Ratio (SDR) in dB.
    """
    # If interpolation is enabled, interpolate the new signal
    if interpolate:
        # Interpolate the new signal to match the length of the original signal
        interpolated_new_signal = np.interp(
            np.arange(len(original_signal)),
            np.arange(0, len(original_signal), len(original_signal) / len(new_signal)),
            new_signal
        )
    else:
        # Otherwise, use sample-and-hold
        interpolated_new_signal = np.repeat(new_signal, len(original_signal) // len(new_signal))
        interpolated_new_signal = np.resize(interpolated_new_signal, len(original_signal))

    # Compute the power of the original signal and the distortion
    power_original_signal = np.sum(np.square(original_signal))
    power_distortion = np.sum(np.square(original_signal - interpolated_new_signal))

    # Ensure non-zero power_distortion to avoid division by zero
    if power_distortion == 0:
        raise ValueError("Power of distortion should be non-zero for SDR computation.")

    # Compute the SDR in dB
    sdr = 10 * np.log10(power_original_signal / power_distortion)

    return sdr


def add_noise(series, drop_rate_dBpdec=-3, initial_magnitude=100, line_magnitude=0.1):
    """
    Add noise to the input time series.

    Args:
        series (Timeseries): Input time series.
        drop_rate_dBpdec (float, optional): Drop rate in dB per decade. Defaults to -3.
        initial_magnitude (float, optional): Initial magnitude of the noise. Defaults to 100.
        line_magnitude (float, optional): Magnitude of sinusoidal noise. Defaults to 0.1.

    Returns:
        Timeseries: Time series with added noise.
    """
    o = Timeseries("Noisy signal")  # Initialize output Timeseries
    o.time = series.time
    o.params.update(series.params)

    # Generate random noise with desired characteristics
    num_samples = len(series.data)
    freqs = np.fft.fftfreq(num_samples, 1 / series.params[TSP_F_HZ])
    magnitude = initial_magnitude / (1 + (freqs / 1) ** 2) ** (abs(drop_rate_dBpdec) / 20)  # Magnitude with drop rate
    phase = np.random.uniform(0, 2 * np.pi, num_samples)  # Random phase
    spectrum = magnitude * np.exp(1j * phase)
    noise = np.fft.ifft(spectrum).real

    # Add sinusoidal noise at 50 Hz
    sinusoidal_noise = line_magnitude * np.sin(2 * np.pi * 50 * series.time)

    # Combine the noises
    total_noise = noise + sinusoidal_noise

    # Add noise to the original signal
    o.data = series.data + total_noise

    o.params[TSP_NOISE_ADDED] = Timeseries("Added noise", time = o.time, data=total_noise)
    # Set noise parameters
    o.params[TSP_NOISE_DROP_RATE_DBPDEC] = drop_rate_dBpdec
    o.params[TSP_NOISE_DC_COMP] = initial_magnitude

    return o.copy()

def norm_bits(series, bits):
    """
    Normalize the input time series to fit within the specified number of bits.

    Args:
        series (Timeseries): Input time series.
        bits (int): Number of bits for normalization.

    Returns:
        Timeseries: Normalized time series.
    """
    o = Timeseries(series.name + " Norm")
    o.time = series.time
    o.params.update(series.params)

    # Sort the absolute values to find the top 10 largest values
    sorted_data = np.sort(np.abs(series.data))
    maxs = sorted_data[-10:]
    maxd = np.average(maxs)
    max_val = (2 ** bits) / 2 - 1

    # Normalize the data and clip to the specified range
    normalized_data = np.clip(max_val * series.data / maxd, -max_val, max_val)

    o.data = normalized_data

    return o.copy()

def normalize(series):
    """
    Normalize the input time series.

    Args:
        series (Timeseries): Input time series.

    Returns:
        Tuple[Timeseries, float]: Tuple containing the normalized time series and the normalization factor.
    """
    # Calculate normalization factor
    factor = 1 / (max(abs(np.max(series.data)), abs(np.min(series.data))))

    # Create the normalized time series
    o = Timeseries("Normalized", time = series.time)
    o.params.update(series.params)
    o_data = np.array(series.data)
    o_data *= factor
    o.data = o_data

    # Set amplitude range parameter
    o.params[TSP_AMPL_RANGE] = [0, 1]

    return o.copy(), factor

def normalize_01(series):
    """
    Normalize the data of a TimeSeries instance to the range [0, 1].

    Parameters:
    series (TimeSeries): The TimeSeries instance with 'time' and 'data' attributes.

    Returns:
    TimeSeries: A new TimeSeries instance with 'time' unchanged and 'data' normalized.
    """

    o = Timeseries("Normalzied")
    o.params.update(series.params)
    min_val = np.min(series.data)
    max_val = np.max(series.data)

    o.data = (series.data - min_val) / (max_val - min_val)
    o.time = series.time
    o.params[TSP_AMPL_RANGE] = [0,1]
    return o.copy()


def scale(series, factor):
    """
    Scale the input time series by a given factor.

    Args:
        series (Timeseries): Input time series.
        factor (float): Scaling factor.

    Returns:
        Timeseries: Scaled time series.
    """
    # Create the scaled time series
    o = Timeseries(f" scaled x{factor}")
    o.data = series.data*factor
    o.time = series.time
    o.params.update(series.params)
    return o.copy()
