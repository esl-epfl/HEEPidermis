#include "config_profiles.h"

// A lower idc provides a wider range of skin conductance measurement
const gsr_range_profile_t k_range_profiles[GSR_OPCTRL_PROFILE_COUNT] = {
    { .idac_code = 30U }, // = 1.2 uA (low range)
    { .idac_code = 20U }, // = 0.8 uA
    { .idac_code = 10U  },  // = 0.4 uA (high range)
};

// For now: resolution -> higher refresh rate (higher OSR) -> mode corresponding to higher refresh rate in the controller config
const gsr_resolution_profile_t k_resolution_profiles[GSR_OPCTRL_PROFILE_COUNT] = {
    { .mode = GSR_CTRL_MODE_BASELINE }, // lowest OSR (or refresh rate)
    { .mode = GSR_CTRL_MODE_RECOVERY }, // medium OSR 
    { .mode = GSR_CTRL_MODE_PHASIC },   // highest OSR 
};

const gsr_refresh_profile_t k_refresh_profiles[GSR_OPCTRL_PROFILE_COUNT] = {
    { .refresh_rate_Hz = 20U },  // low refresh rate for baseline tracking
    { .refresh_rate_Hz = 30U },  // medium refresh rate for recovery tracking
    { .refresh_rate_Hz = 40U }, // high refresh rate for phasic event capture
};

const gsr_power_profile_t k_power_profiles[GSR_OPCTRL_PROFILE_COUNT] = {
    { .duty_cycle_code = 4U }, // The VCO is ON 1/4 of the time
    { .duty_cycle_code = 2U }, // The VCO is ON 1/2 of the time
    { .duty_cycle_code = 1U }, // The VCO is always ON 
};
