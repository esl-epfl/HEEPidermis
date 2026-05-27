#include "GSR_sdk.h"
#include "iDAC_ctrl.h"

#define VCO_SUPPLY_VOLTAGE_UV 800000
#define GSR_IDAC_LSB_NA              40U

static uint32_t current_nA = 0;

static bool      dlc_used       = false;
static uint8_t  *s_dlc_buf      = 0;
static uint16_t  s_dlc_buf_size = 0;
static uint16_t  s_dlc_read_idx = 0;

/*
Initialize the GSR measurement chain.

The selected iDAC code sets the injected current, and the VCO SDK is then
initialized with the requested channel and refresh rate.
*/
gsr_status_t gsr_init_dlc(vco_channel_t channel, uint32_t refresh_rate_Hz, uint8_t idac_val, const gsr_dlc_config_t *dlc_cfg){

    dlc_used = true;
    current_nA = gsr_current_from_idac_code_nA(idac_val);
    iDACs_set_currents(idac_val, 0);
    s_dlc_buf      = dlc_cfg->results_buf;
    s_dlc_buf_size = dlc_cfg->buf_size;
    s_dlc_read_idx = 0;
    vco_status_t st = vco_dlc_initialize(channel, refresh_rate_Hz, dlc_cfg->dlc_cfg, dlc_cfg->results_buf, dlc_cfg->buf_size, dlc_cfg->input_samples);
    if (st == VCO_STATUS_OK) return GSR_STATUS_OK;
    if (st == VCO_STATUS_INVALID_ARGUMENT) return GSR_STATUS_INVALID_ARGUMENT;
    return GSR_STATUS_NOT_INITIALIZED;

}

/*
Initialize the GSR measurement chain.

The selected iDAC code sets the injected current, and the VCO SDK is then
initialized with the requested channel and refresh rate.
*/

gsr_status_t gsr_status_from_vco(vco_status_t status) {
    if (status == VCO_STATUS_OK) return GSR_STATUS_OK;
    if (status == VCO_STATUS_NO_NEW_SAMPLE) return GSR_STATUS_NO_NEW_SAMPLE;
    if (status == VCO_STATUS_MISSED_UPDATE) return GSR_STATUS_MISSED_UPDATE;
    if (status == VCO_STATUS_INVALID_ARGUMENT) return GSR_STATUS_INVALID_ARGUMENT;
    if (status == VCO_STATUS_UNDERFLOW) return GSR_STATUS_UNDERFLOW;
    if (status == VCO_STATUS_OVERFLOW) return GSR_STATUS_OVERFLOW;
    return GSR_STATUS_NOT_INITIALIZED;
}

gsr_status_t gsr_init(vco_channel_t channel, uint32_t refresh_rate_Hz, uint8_t idac_val){

    current_nA = gsr_current_from_idac_code_nA(idac_val);
    iDACs_set_currents(idac_val, 0);
    vco_status_t st = vco_initialize(channel, refresh_rate_Hz);
    if (st == VCO_STATUS_OK) return GSR_STATUS_OK;
    if (st == VCO_STATUS_INVALID_ARGUMENT) return GSR_STATUS_INVALID_ARGUMENT;
    return GSR_STATUS_NOT_INITIALIZED;

}

uint32_t gsr_current_from_idac_code_nA(uint8_t idac_code) {
    return (uint32_t)idac_code * GSR_IDAC_LSB_NA;
}

// Update the injected current used by the analog front-end
void gsr_update_current(uint8_t idac_val){

    current_nA = gsr_current_from_idac_code_nA(idac_val);
    iDACs_set_currents(idac_val, 0);
}

/*
Read one GSR sample in nS.
The function first reconstructs Vin from the VCO measurement, then computes
conductance.
*/
gsr_status_t gsr_get_conductance_nS(uint32_t *conductance_nS, uint32_t* vin_uV_ret) {

    if (conductance_nS == 0) {
        return GSR_STATUS_INVALID_ARGUMENT;
    }

    // Get the latest reconstructed front-end voltage from the VCO layer.
    uint32_t vin_uV = 0;

    if (dlc_used) {
        uint8_t packed_event = s_dlc_buf[s_dlc_read_idx];
        s_dlc_read_idx = (s_dlc_read_idx + 1) % s_dlc_buf_size;
        gsr_status_t st = gsr_status_from_vco(vco_dlc_process_event(packed_event, &vin_uV));
        if (st != GSR_STATUS_OK) return st;
    } else { // Read from VCO directly if dLC is not used
        gsr_status_t st = gsr_status_from_vco(vco_get_Vin_uV(&vin_uV));
        if (st != GSR_STATUS_OK) return st;
    }

    // Optionally expose Vin to the caller for monitoring or controller use.
    if (vin_uV_ret != 0){
        *vin_uV_ret = vin_uV;
    }

    uint32_t dv_uV = VCO_SUPPLY_VOLTAGE_UV - vin_uV;
    *conductance_nS = (uint32_t)(((uint64_t)current_nA * 1000000ULL) / dv_uV);
    return GSR_STATUS_OK;

}

/*

Compute an averaged conductance estimate from multiple valid samples.

This improves robustness to sample-to-sample noise at the cost of latency.
Only valid samples are accumulated; NO_NEW_SAMPLE results are ignored.

*/
gsr_status_t gsr_get_conductance_oversampled(uint32_t *conductance_nS, uint32_t *vin_uV_ret, int M) {

    if (conductance_nS == 0 ||vin_uV_ret == 0 || M <= 0) {
        return GSR_STATUS_INVALID_ARGUMENT;
    }

    uint64_t conductance_acc = 0 ;
    uint64_t vin_acc = 0;
    int valid_samples = 0;

    while (valid_samples < M) {
        uint32_t conductance_nS = 0, vin_uV = 0;
        gsr_status_t ret = gsr_get_conductance_nS(&conductance_nS, &vin_uV);

        if (ret == GSR_STATUS_OK) {
            conductance_acc += conductance_nS;
            vin_acc += vin_uV;
            valid_samples++;
        }
        else if (ret == GSR_STATUS_NO_NEW_SAMPLE) {
            // No new refresh yet, keep waiting
            continue;
        }
        else {
            return ret;
        }
    }

    *conductance_nS = (uint32_t)(conductance_acc / (uint64_t)M);
    *vin_uV_ret = (uint32_t)(vin_acc / (uint64_t)M);
    return GSR_STATUS_OK;

}