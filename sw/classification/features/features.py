"""Common feature-name vocabulary shared by extraction, classification and plotting.

Each possible feature is a string constant defined here;
conductance_S_to_features labels its outputs with these constants, and
main.py selects a list of them to extract and feed to the SVM.
"""

# Tonic level statistics.
LEVEL_MEAN_S           = "level_mean_S"
LEVEL_MAX_S            = "level_max_S"
LEVEL_MIN_S            = "level_min_S"
LEVEL_SLOPE_S_PER_S    = "level_slope_S_per_s"

# Skin conductance peak heights.
PEAK_HEIGHT_MEAN_S     = "peak_height_mean_S"
PEAK_RATE_PER_MIN      = "peak_rate_per_min"
PEAK_HEIGHT_Q25_S      = "peak_height_q25_S"
PEAK_HEIGHT_Q50_S      = "peak_height_q50_S"
PEAK_HEIGHT_Q75_S      = "peak_height_q75_S"
PEAK_HEIGHT_Q85_S      = "peak_height_q85_S"
PEAK_HEIGHT_Q95_S      = "peak_height_q95_S"

# Instantaneous peak rates within a window.
INSTANT_RATE_Q25_HZ    = "instant_rate_q25_Hz"
INSTANT_RATE_Q50_HZ    = "instant_rate_q50_Hz"
INSTANT_RATE_Q75_HZ    = "instant_rate_q75_Hz"
INSTANT_RATE_Q85_HZ    = "instant_rate_q85_Hz"
INSTANT_RATE_Q95_HZ    = "instant_rate_q95_Hz"

# Features whose amplitude scales with the absolute conductance level; with
# relative_windows they are divided by the mean conductance of their own
# window, so a 25% rise reads as 1.25 regardless of the baseline SCL. The
# rate features are already level-independent and stay absolute.
AMPLITUDE_FEATURES = (
    LEVEL_MEAN_S, LEVEL_MAX_S, LEVEL_MIN_S, LEVEL_SLOPE_S_PER_S,
    PEAK_HEIGHT_MEAN_S, PEAK_HEIGHT_Q25_S, PEAK_HEIGHT_Q50_S,
    PEAK_HEIGHT_Q75_S, PEAK_HEIGHT_Q85_S, PEAK_HEIGHT_Q95_S,
)

FEATURE_NAMES = (
    LEVEL_MEAN_S, LEVEL_MAX_S, LEVEL_MIN_S, LEVEL_SLOPE_S_PER_S,
    PEAK_HEIGHT_MEAN_S, PEAK_RATE_PER_MIN,
    PEAK_HEIGHT_Q25_S, PEAK_HEIGHT_Q50_S, PEAK_HEIGHT_Q75_S,
    PEAK_HEIGHT_Q85_S, PEAK_HEIGHT_Q95_S,
    INSTANT_RATE_Q25_HZ, INSTANT_RATE_Q50_HZ, INSTANT_RATE_Q75_HZ,
    INSTANT_RATE_Q85_HZ, INSTANT_RATE_Q95_HZ,
)

FEATURE_SELECTION_JUAN = [
    LEVEL_SLOPE_S_PER_S,
    PEAK_HEIGHT_Q25_S,
    PEAK_RATE_PER_MIN,
]

# Setz et al., Table I: MIST, nonrelative features, linear SVM.
MIST_LINEAR_FEATURES = (
    PEAK_HEIGHT_MEAN_S, PEAK_HEIGHT_Q50_S, PEAK_HEIGHT_Q85_S,
    INSTANT_RATE_Q85_HZ, INSTANT_RATE_Q95_HZ,
)
