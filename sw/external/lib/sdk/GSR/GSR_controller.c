#define GSR_IDAC_LSB_NA              40U
#define GSR_IDAC_MAX_CODE            255U
#define GSR_MAX_CURRENT_NA           ((uint32_t)GSR_IDAC_MAX_CODE * GSR_IDAC_LSB_NA)
#define GSR_VCO_SUPPLY_VOLTAGE_UV    800000U
#define GSR_VIN_MIN_UV               330000U
#define GUARD_IDC_NA                 50U // guard i_dc to prevent going out of range in the next conductance measurement; 50nA corresponds to 0.1 uS of change in conductance
#define VCO_VARIANCE                 3U // variance in the VCO frequency-to-voltage conversion, used for sensitivity estimation
#define F_NYQ_HZ                     2U // Nyquist frequency for the GSR measurement
#define RESOLUTION_DB_SCALE      100U   // Return value in dB * 100
#define ADEV_VAR
#define min(a, b) (((a) < (b)) ? (a) : (b))

#include "GSR_controller.h"

#define INTR_DMA_TRANS_DONE (( 1 << 19 ))


static dma_target_t s_gsr_dma_src;
static dma_target_t s_gsr_dma_dst;
static dma_trans_t  s_gsr_dma_trans;
static gsr_dma_acq_t *s_dma_acq = NULL;

void gsr_dma_intr_handler_trans_done(uint8_t channel)
{
    if (channel == 0U && s_dma_acq != NULL) {
        if (s_dma_acq->window_ready) {
            s_dma_acq->overrun = true;
        }

        s_dma_acq->window_ready = true;
    }
}

// Update the baseline estimate using a simple exponential-style moving average.
static uint32_t calculate_baseline(uint32_t prev_baseline, uint32_t sample) {
    
    return (uint32_t)(((uint64_t)7U * prev_baseline + sample) / 8U);
}

// Reconfigure the iDAC current used for conductance measurement, with range checking based on the current limits.
void gsr_controller_set_current(gsr_controller_t *ctrl, uint8_t idac_code) {
    // current configuration 
    ctrl->config.idac_code = idac_code;
    
    // Delegate actual hardware/current model update to the clean SDK
    gsr_update_current(idac_code);
}

static gsr_status_t controller_set_vco(gsr_controller_t *ctrl, gsr_ctrl_mode_t mode, uint8_t duty_cycle_code) {
    if (ctrl == NULL) return GSR_STATUS_INVALID_ARGUMENT;
    uint32_t new_rate_Hz;
    gsr_status_t ret;
    switch (mode) {
        case GSR_CTRL_MODE_BASELINE:
            new_rate_Hz = ctrl->config.baseline_refresh_rate_Hz;
            break;
        case GSR_CTRL_MODE_PHASIC:
            new_rate_Hz = ctrl->config.phasic_refresh_rate_Hz;
            break;
        case GSR_CTRL_MODE_RECOVERY:
            new_rate_Hz = ctrl->config.recovery_refresh_rate_Hz;
            break;
        default:
            return GSR_STATUS_INVALID_ARGUMENT;
    }
    ctrl->config.current_refresh_rate_Hz = new_rate_Hz; // keep track of the current refresh rate in the controller state for reference
    if(!ctrl->dlc_used) {
        ret = gsr_status_from_vco(vco_config(ctrl->config.channel, new_rate_Hz, duty_cycle_code));
    } else {
        ret = gsr_status_from_vco(vco_dlc_config(ctrl->config.channel, new_rate_Hz));
    }
    return ret;
}

static uint32_t max_current_for_conductance_nS(uint32_t conductance_nS) {
    const uint32_t delta_v_min_uV = GSR_VCO_SUPPLY_VOLTAGE_UV - GSR_VIN_MIN_UV;
    uint32_t max_current_nA = (uint32_t)(((uint64_t)conductance_nS * delta_v_min_uV) / 1000000ULL);

    return min(max_current_nA, GSR_MAX_CURRENT_NA); 
}

// TODO: Compute the frequency error of the VCO (based on allen deviation measurements)
static uint32_t compute_frequency_error_Hz(uint32_t vin_uV, uint32_t integration_rate_Hz, uint8_t variance)
{

    // We use a fixed value for the frequency error based on the Allen deviation measurements of the VCO. 
    #ifdef ADEV_VAR
        return 250; // approximated by 100Hz (see report and hw/vendor/analog-library/VCO/VCO_characteristics/figs/frequency_uncertainty_vs_vin.svg plot for justification)
    #else
        return integration_rate_Hz;
    #endif
}

// Compute the conductance sensitivity (delta G) of the VCO around a given Vin, based on the i_dc and refresh rate.
static uint32_t compute_conductance_sensitivity_nS(uint32_t conductance_nS, uint32_t vin_uV, uint32_t current_nA, uint32_t integration_rate_Hz)
{
    uint32_t frequency_error_Hz = compute_frequency_error_Hz(vin_uV, integration_rate_Hz, VCO_VARIANCE);
    uint32_t kvco_Hz_per_V = vco_get_kvco_Hz_per_V(vin_uV);

    // Note: 32-bit arithmetic is safe from overflow because
    // kvco_Hz_per_V < 6'000'000 Hz/V (from measurements in scripts/plotter)
    // current_nA < 10'200 nA (based on front-end settings)
    // frequency_error_Hz < 10'000 Hz (based on measurements in scripts/plotter)
    // conductance_nS < 500'000 nS (very conservative upper bound for skin conductance)
    // worst case denom = 7 * 10^10  = 2^37 < 2^63, so the 64-bit intermediate is safe from overflow
    // worst case numer = 10'000 * 500'000 * 500'000 = 2.5 * 10^15 = 2^51 < 2^63, so the 64-bit intermediate is safe from overflow 
    uint64_t denom = kvco_Hz_per_V * current_nA + frequency_error_Hz * conductance_nS;

    if (denom == 0ULL) return 0; // avoid division by zero, sensitivity is effectively zero if the VCO frequency doesn't change with voltage or if there is no current
    
    uint64_t numer = frequency_error_Hz * conductance_nS * conductance_nS;

    uint32_t delta_G_nS = (uint32_t)(numer / denom);

    return delta_G_nS;
}

/*
return the Q1 approximation of log2(x), which is 2*log2(x) rounded to the nearest integer.
Q1 means the value is scaled by 2. Example: return 7 means 3.5
*/
static uint32_t approx_log2_q1_u32(uint32_t x)
{
    if (x == 0U) return 0U;

    uint32_t n = 0U;
    uint32_t tmp = x;

    while (tmp >>= 1U) n++;

    uint32_t log2_q1 = n << 1U;

    // Add 0.5 if the bit just below the MSB is set
    if (n > 0U) log2_q1 += (x >> (n - 1U)) & 1U;

    return log2_q1;
}

static uint32_t compute_conductance_resolution_dB(const gsr_controller_t *ctrl, uint32_t sensitivity_nS)
{
    uint32_t OSR = ctrl->config.current_refresh_rate_Hz >> 1U; // because F_NYQ_HZ = 2Hz
    uint32_t amplitude_nS = ctrl->sample.amplitude_nS;

    if (sensitivity_nS == 0U || amplitude_nS == 0U || OSR == 0U) return 0;

    uint32_t log2_A_q1 = approx_log2_q1_u32(amplitude_nS);
    uint32_t log2_OSR_q1 = approx_log2_q1_u32(OSR);
    uint32_t log2_dG_q1 = approx_log2_q1_u32(sensitivity_nS);

    /*
     * resolution[dB] = 10 * (log10(OSR) + 2log10(A) - 2log10(DeltaG))
     *                ≈ 3 * (log2(OSR) + 2log2(A) - 2log2(DeltaG))
     *
     * All log2 values are Q1, so result is also Q1 dB. (Q1 means value / 2.)
     */
    int32_t resolution_dB_q1 =
    3 * (((int32_t)log2_A_q1 << 1)
        + (int32_t)log2_OSR_q1
        - ((int32_t)log2_dG_q1 << 1));

    if (resolution_dB_q1 <= 0) return 0U;

    /* Convert from Q1 dB to integer dB by dividing by 2 (Q1 means value / 2)  
     * and multiplying by 100 to return resolution in dB * 100 for better precision. 
     */
    return ((uint32_t)resolution_dB_q1 * RESOLUTION_DB_SCALE) >> 1;
}

/*
Compute the amplitude of the current sample relative to the baseline in nS.
*/
static uint32_t compute_amplitude_nS(const gsr_controller_t *ctrl) {
    if (ctrl->sample.baseline_nS == 0U) return 0U; // this can happen at the very beginning when no baseline has been established yet.
    return (ctrl->sample.G_nS >= ctrl->sample.baseline_nS)
             ? (ctrl->sample.G_nS - ctrl->sample.baseline_nS)
             : (ctrl->sample.baseline_nS - ctrl->sample.G_nS);
}
/*
Detect whether the current sample indicates the onset of a phasic event using either
deviation from the baseline or slope magnitude
*/
static bool event_detected(const gsr_controller_t *ctrl){

    uint32_t amp = compute_amplitude_nS(ctrl);
    uint32_t slope_abs = (ctrl->sample.slope_nS >= 0) ? (uint32_t)ctrl->sample.slope_nS : (uint32_t)(-ctrl->sample.slope_nS);

    return (amp >= ctrl->amplitude_threshold_nS) || (slope_abs >= ctrl->slope_threshold_nS);

}

// Check whether the signal has returned close enough to baseline
static bool signal_settled(const gsr_controller_t *ctrl) {

    uint32_t amp = compute_amplitude_nS(ctrl);
    uint32_t slope_abs = (ctrl->sample.slope_nS >= 0) ? (uint32_t)ctrl->sample.slope_nS : (uint32_t)(-ctrl->sample.slope_nS);

    return (amp <= ctrl->settle_threshold_nS) && (slope_abs <= ctrl->settle_threshold_nS);

}

// Load a default controller configuration for standard GSR operation.
gsr_status_t gsr_set_default_settings(gsr_controller_t *ctrl) {

    if (ctrl == 0) {
        return GSR_STATUS_INVALID_ARGUMENT;
    }

    ctrl->config.channel = VCO_CHANNEL_P;
    ctrl->config.duty_cycle_code = 1; // 100% duty cycle
    ctrl->config.M = 1; // no oversampling by default, just take one measurement per sample. This can be increased for more noisy environments at the cost of temporal resolution and power consumption.
    ctrl->config.baseline_refresh_rate_Hz = 2;
    ctrl->config.phasic_refresh_rate_Hz = 10;
    ctrl->config.recovery_refresh_rate_Hz = 5;
    ctrl->config.idac_code = 20; // 0.8 uA 
    ctrl->config.current_refresh_rate_Hz = ctrl->config.baseline_refresh_rate_Hz; // initialize the current refresh rate to the baseline rate
    ctrl->amplitude_threshold_nS = 80;
    ctrl->slope_threshold_nS = 40;
    ctrl->settle_threshold_nS = 25;
    ctrl->recovery_count_required = 8;

    ctrl->dlc_used = false;
    ctrl->dma_used = false;

    return GSR_STATUS_OK;
}

// Update the controller configuration and apply it to the hardware. 
gsr_status_t gsr_controller_set_config(gsr_controller_t *ctrl) {
    
    if (ctrl == NULL) return GSR_STATUS_INVALID_ARGUMENT;
    
    gsr_status_t ret;
    gsr_config_t *config = &ctrl->config;
    if (config->baseline_refresh_rate_Hz == 0U || config->phasic_refresh_rate_Hz == 0U || config->recovery_refresh_rate_Hz == 0U || config->idac_code == 0U) {
        return GSR_STATUS_INVALID_ARGUMENT;
    }

    // setup the config of the iDAC Hardware registers
    gsr_controller_set_current(ctrl, config->idac_code);
    
    // setup the config of the VCO Hardware registers
    ret = controller_set_vco(ctrl, ctrl->mode, config->duty_cycle_code);
    if (ret != GSR_STATUS_OK) return ret;

    return ret;
}

static gsr_status_t gsr_dma_init(gsr_controller_t *ctrl)
{
    dma_config_flags_t res;

    if (ctrl == NULL || ctrl->dma == NULL || ctrl->dma->write_buf == NULL) return GSR_STATUS_INVALID_ARGUMENT;

    // assign the global pointer to the dma acquisition struct 
    s_dma_acq = ctrl->dma;
    ctrl->dma->window_ready = false;
    ctrl->dma->overrun = false;
    ctrl->dma->completed_buf = NULL;
    ctrl->dma->running = false;
    ctrl->dma->discard_samples = 1U; // discard the first sample after starting DMA as it can be corrupted based on empirical observations

    dma_init(NULL);

    s_gsr_dma_src.ptr       = (uint8_t *)(VCO_DECODER_START_ADDRESS +
                                        VCO_DECODER_VCO_DECODER_CNT_REG_OFFSET);
    s_gsr_dma_src.trig      = DMA_TRIG_SLOT_EXT_RX;
    s_gsr_dma_src.inc_d1_du = 0;
    s_gsr_dma_src.type      = DMA_DATA_TYPE_WORD;

    s_gsr_dma_dst.ptr       = (uint8_t *)ctrl->dma->write_buf;
    s_gsr_dma_dst.trig      = DMA_TRIG_MEMORY;
    s_gsr_dma_dst.inc_d1_du = 1;
    s_gsr_dma_dst.type      = DMA_DATA_TYPE_WORD;

    s_gsr_dma_trans.src        = &s_gsr_dma_src;
    s_gsr_dma_trans.dst        = &s_gsr_dma_dst;
    s_gsr_dma_trans.dim        = DMA_DIM_CONF_1D;
    s_gsr_dma_trans.channel    = 0;
    s_gsr_dma_trans.size_d1_du = ctrl->dma->samples_per_window;
    s_gsr_dma_trans.win_du     = 0;
    s_gsr_dma_trans.end        = DMA_TRANS_END_INTR;
    s_gsr_dma_trans.mode       = DMA_TRANS_MODE_SINGLE;
    s_gsr_dma_trans.hw_fifo_en = false;

    s_gsr_dma_trans.flags = 0x0;

    res = dma_validate_transaction(&s_gsr_dma_trans,
                                   DMA_ENABLE_REALIGN,
                                   DMA_PERFORM_CHECKS_INTEGRITY);
    if (res != DMA_CONFIG_OK) return GSR_STATUS_INVALID_ARGUMENT;

    res = dma_load_transaction(&s_gsr_dma_trans);
    if (res != DMA_CONFIG_OK) return GSR_STATUS_INVALID_ARGUMENT;

    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);
    CSR_SET_BITS(CSR_REG_MIE, INTR_DMA_TRANS_DONE);

    res = dma_launch(&s_gsr_dma_trans);
    if (res != DMA_CONFIG_OK) return GSR_STATUS_INVALID_ARGUMENT;

    ctrl->dma->running = true;

    return GSR_STATUS_OK;
}

static gsr_status_t gsr_dma_start_window(gsr_controller_t *ctrl)
{
    dma_config_flags_t res;

    if (ctrl == NULL || ctrl->dma == NULL || ctrl->dma->write_buf == NULL ||
         ctrl->dma->buf_a == NULL || ctrl->dma->buf_b == NULL) {
        return GSR_STATUS_INVALID_ARGUMENT;
    }

    ctrl->dma->window_ready = false;

    ctrl->dma->completed_buf = ctrl->dma->write_buf;

    if (ctrl->dma->completed_buf == ctrl->dma->buf_a) {
        ctrl->dma->write_buf =  ctrl->dma->buf_b;
    } else {
        ctrl->dma->write_buf = ctrl->dma->buf_a;
    }

    s_gsr_dma_dst.ptr = (uint8_t *)ctrl->dma->write_buf;
    s_gsr_dma_trans.flags = 0x0;

    res = dma_load_transaction(&s_gsr_dma_trans);
    if (res != DMA_CONFIG_OK) return GSR_STATUS_INVALID_ARGUMENT;

    res = dma_launch(&s_gsr_dma_trans);
    if (res != DMA_CONFIG_OK) return GSR_STATUS_INVALID_ARGUMENT;

    return GSR_STATUS_OK;
}

// Initialize controller state variables and start the GSR front-end.
gsr_status_t gsr_controller_init(gsr_controller_t *ctrl) {
    
    gsr_status_t ret;
    if (ctrl == 0) {
        return GSR_STATUS_INVALID_ARGUMENT;
    }

    ctrl->mode = GSR_CTRL_MODE_BASELINE;
    gsr_sample_t init_sample = {
        .G_nS = 0U,
        .prev_G_nS = 0U,
        .vin_uV = 0U,
        .baseline_nS = 0U,
        .amplitude_nS = 0U,
        .slope_nS = 0,
        .current_nA = gsr_current_from_idac_code_nA(ctrl->config.idac_code),
        .valid = false
    };
    ctrl->sample = init_sample;

    ctrl->recovery_counter = 0;
    ctrl->max_current_nA = GSR_MAX_CURRENT_NA;

    ctrl->initialized = false;

    // initialize with the baseline refresh rate
    if (ctrl->dlc_used) {
        ret = gsr_init_dlc(ctrl->config.channel, ctrl->config.baseline_refresh_rate_Hz, ctrl->config.idac_code, &ctrl->dlc_cfg);
    } else {
        ret = gsr_init(ctrl->config.channel, ctrl->config.baseline_refresh_rate_Hz, ctrl->config.idac_code);
    }
    if (ret != GSR_STATUS_OK) {
        return ret;
    }

    if (ctrl->dma_used) {
        ret = gsr_dma_init(ctrl);
        if (ret != GSR_STATUS_OK) {
            return ret;
        }
    }

    return ret;
}

static gsr_status_t gsr_read_sample_dma(gsr_controller_t *ctrl)
{
    gsr_status_t ret;
    int valid = 0;
    uint32_t conductance_nS = 0;
    uint32_t rms_conductance_nS = 0; // will be later used to compute G_RMS
    uint32_t vin_uV = 0U;

    if (ctrl == NULL || ctrl->dma == NULL) {
        return GSR_STATUS_INVALID_ARGUMENT;
    }

    while (!ctrl->dma->window_ready) {
        wait_for_interrupt();
    }

    if (ctrl->dma->overrun) {
        ctrl->dma->overrun = false;
        return GSR_STATUS_MISSED_UPDATE;
    }

    ret = gsr_dma_start_window(ctrl);
    if (ret != GSR_STATUS_OK) return ret; 

    
    for (uint32_t i = 0; i < ctrl->dma->samples_per_window; i++) {
        conductance_nS = 0;
        vin_uV = 0U;
        // if (ctrl->dma->discard_samples > 0U) {
        //     ctrl->dma->discard_samples--;
        //     continue;
        // }
        uint32_t count = (uint32_t)ctrl->dma->completed_buf[i];

        ret = gsr_count_to_conductance_nS(count, &conductance_nS, &vin_uV);
        if (ret != GSR_STATUS_OK) continue; // skip invalid samples
        
        valid++;
        
        ctrl->sample.prev_G_nS = ctrl->sample.G_nS;
        ctrl->sample.G_nS = conductance_nS;
        ctrl->sample.vin_uV = vin_uV;
        ctrl->sample.current_nA = gsr_current_from_idac_code_nA(ctrl->config.idac_code);
        // update max current limit based on the new conductance measurement
        ctrl->max_current_nA = max_current_for_conductance_nS(conductance_nS);

        ctrl->sample.valid = true;

        if (!ctrl->initialized) { // populate the baseline with the first valid sample
            ctrl->sample.baseline_nS = conductance_nS;
            ctrl->sample.prev_G_nS = conductance_nS;
            ctrl->sample.slope_nS = 0;
            ctrl->sample.amplitude_nS = 0;
            ctrl->initialized = true;
        }

        // Slope is expressed in nS/s by multiplying the sample difference by fs.
        ctrl->sample.slope_nS = ((int32_t)ctrl->sample.G_nS - (int32_t)ctrl->sample.prev_G_nS) * (int32_t)ctrl->config.current_refresh_rate_Hz;
        
        // Only baseline mode is allowed to slowly adapt the tonic reference.
        if (ctrl->mode == GSR_CTRL_MODE_BASELINE) {
            ctrl->sample.baseline_nS = calculate_baseline(ctrl->sample.baseline_nS, ctrl->sample.G_nS);
        }
        // compute amplitude after updating the baseline.
        ctrl->sample.amplitude_nS = compute_amplitude_nS(ctrl);
        rms_conductance_nS += ctrl->sample.amplitude_nS;
    }

    if (valid == 0) {
        ctrl->sample.valid = false;
        ctrl->valid_samples = 0;
        return GSR_STATUS_NO_NEW_SAMPLE; // or MISSED_UPDATE
    }
    rms_conductance_nS = rms_conductance_nS/valid;
    ctrl->valid_samples = valid;

    return GSR_STATUS_OK;
}

uint8_t get_valid_samples(gsr_controller_t *ctrl) {
    if (ctrl == NULL) return 0;

    return ctrl->valid_samples;
}

static gsr_status_t gsr_read_sample_now(gsr_controller_t *ctrl) {
    uint32_t new_vin_uV = 0U;
    uint32_t new_conductance_nS = 0U;
    gsr_status_t ret;

    if (ctrl == NULL || ctrl->config.M == 0U) return GSR_STATUS_INVALID_ARGUMENT;

    if (ctrl->config.M > 1) {
        ret = gsr_get_conductance_oversampled(&new_conductance_nS, &new_vin_uV, ctrl->config.M);
    } else {
        ret = gsr_get_conductance_nS(&new_conductance_nS, &new_vin_uV);
    }
    if (ret != GSR_STATUS_OK) { // UNDERFLOW or OVERFLOW or NOT_INITIALIZED or MISSED_UPDATE or NO_NEW_SAMPLE
        // Need to implememt correct strategy in gcase UNDERFLOW or OVERFLOW or NO_NEW_SAMPLE or MISSED_UPDATE
        ctrl->sample.valid = false;
        return ret;
    }

    ctrl->sample.prev_G_nS = ctrl->sample.G_nS;
    ctrl->sample.G_nS = new_conductance_nS;
    ctrl->sample.vin_uV = new_vin_uV;
    ctrl->sample.current_nA = gsr_current_from_idac_code_nA(ctrl->config.idac_code);
    // update max current limit based on the new conductance measurement
    ctrl->max_current_nA = max_current_for_conductance_nS(new_conductance_nS);

    ctrl->sample.valid = true;
    
    if (!ctrl->initialized) {
        ctrl->sample.baseline_nS = ctrl->sample.G_nS;
        ctrl->sample.prev_G_nS = ctrl->sample.G_nS;
        ctrl->sample.slope_nS = 0;
        ctrl->sample.amplitude_nS = 0;
        ctrl->initialized = true;
        return GSR_STATUS_OK;
    }
    // Slope is expressed in nS/s by multiplying the sample difference by fs.
    ctrl->sample.slope_nS = ((int32_t)ctrl->sample.G_nS - (int32_t)ctrl->sample.prev_G_nS) * (int32_t)ctrl->config.current_refresh_rate_Hz;
    
    // Only baseline mode is allowed to slowly adapt the tonic reference.
    if (ctrl->mode == GSR_CTRL_MODE_BASELINE) {
        ctrl->sample.baseline_nS = calculate_baseline(ctrl->sample.baseline_nS, ctrl->sample.G_nS);
    }
    // compute amplitude after updating the baseline.
    ctrl->sample.amplitude_nS = compute_amplitude_nS(ctrl);

    return ret;
}

gsr_status_t gsr_read_sample(gsr_controller_t *ctrl)
{
    if (ctrl == NULL) return GSR_STATUS_INVALID_ARGUMENT;

    if (ctrl->dma_used) {
        return gsr_read_sample_dma(ctrl);
    }
    
    if (ctrl->config.duty_cycle_code == 1U || ctrl->dlc_used) { // no duty cycling or event based reading 
        return gsr_read_sample_now(ctrl);
    }

    while (1) {
        asm volatile("wfi");

        if (!vco_is_on()) {
            return gsr_read_sample_now(ctrl);
        }
    }
}

const gsr_sample_t *gsr_get_last_sample(const gsr_controller_t *ctrl) {
    if (ctrl == NULL) return NULL;

    return &ctrl->sample;
}

gsr_metrics_t get_metrics(gsr_controller_t *ctrl) {
    gsr_metrics_t metrics;
    metrics.conductance_sensitivity_nS = compute_conductance_sensitivity_nS(ctrl->sample.G_nS , ctrl->sample.vin_uV, 
                                                                            ctrl->sample.current_nA, ctrl->config.current_refresh_rate_Hz);
    metrics.resolution_dB = compute_conductance_resolution_dB(ctrl, metrics.conductance_sensitivity_nS);
    return metrics;
}


/*
Execute one control step.

Sequence:
1. read the latest conductance sample
2. update signal estimates (current value, slope, baseline)
3. evaluate event / settling conditions
4. switch mode and refresh rate if needed
*/
gsr_status_t gsr_controller_step(gsr_controller_t *ctrl) {

    uint32_t sample_nS = 0;
    uint32_t vin_uV;
    gsr_status_t st;

    if (ctrl == 0) {
        return GSR_STATUS_INVALID_ARGUMENT;
    }

    st = gsr_read_sample(ctrl);
    if (st != GSR_STATUS_OK) return st;

    switch (ctrl->mode) {

        case GSR_CTRL_MODE_INIT:
            ctrl->mode = GSR_CTRL_MODE_BASELINE;
            break;

        case GSR_CTRL_MODE_BASELINE:
            //This line was removed since Esmail will be working on this.
            // retune_current_for_baseline(ctrl);

            if (event_detected(ctrl)) {
                st = controller_set_vco(ctrl, GSR_CTRL_MODE_PHASIC, ctrl->config.duty_cycle_code);
                if (st != GSR_STATUS_OK) return st;

                ctrl->config.current_refresh_rate_Hz = ctrl->config.phasic_refresh_rate_Hz;
                ctrl->mode = GSR_CTRL_MODE_PHASIC;
                ctrl->recovery_counter = 0;
            }
            break;
        
        // Phasic mode increases sampling rate to better capture fast events.
        case GSR_CTRL_MODE_PHASIC:
            if (signal_settled(ctrl)) {
                st = controller_set_vco(ctrl, GSR_CTRL_MODE_RECOVERY, ctrl->config.duty_cycle_code);
                if (st != GSR_STATUS_OK) return st;

                ctrl->config.current_refresh_rate_Hz = ctrl->config.recovery_refresh_rate_Hz;
                ctrl->mode = GSR_CTRL_MODE_RECOVERY;
                ctrl->recovery_counter = 0;
            }
            break;

        // Recovery mode uses an intermediate sampling rate until the signal is stable again.
        case GSR_CTRL_MODE_RECOVERY:
            if (!signal_settled(ctrl)) {
                st = controller_set_vco(ctrl, GSR_CTRL_MODE_PHASIC, ctrl->config.duty_cycle_code);
                if (st != GSR_STATUS_OK) return st;

                ctrl->config.current_refresh_rate_Hz = ctrl->config.phasic_refresh_rate_Hz;
                ctrl->mode = GSR_CTRL_MODE_PHASIC;
                ctrl->recovery_counter = 0;
                break;
            }

            ctrl->recovery_counter++;

            if (ctrl->recovery_counter >= ctrl->recovery_count_required) {
                // retune_current_for_baseline(ctrl); // CHANGE: removed since Esmail will be working on this.

                st = controller_set_vco(ctrl, GSR_CTRL_MODE_BASELINE, ctrl->config.duty_cycle_code);
                if (st != GSR_STATUS_OK) return st;

                ctrl->config.current_refresh_rate_Hz = ctrl->config.baseline_refresh_rate_Hz;
                ctrl->mode = GSR_CTRL_MODE_BASELINE;
                ctrl->recovery_counter = 0U;
            }
            break;

        default:
            return GSR_STATUS_NOT_INITIALIZED;
    }

    return GSR_STATUS_OK;
}
