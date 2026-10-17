#ifndef GSR_SDK_H_
#define GSR_SDK_H_

#include <stdint.h>
#include <stdbool.h>
#include "VCO_sdk.h"
#include "DLC_sdk.h"
#include "VCO_dlc_sdk.h"

/*
This layer builds on top of the VCO SDK and converts reconstructed Vin
into skin conductance using the front-end current setting.
*/

/* Status codes returned by the GSR SDK functions. */
typedef enum {
    GSR_STATUS_OK = 0,
    GSR_STATUS_NO_NEW_SAMPLE,
    GSR_STATUS_MISSED_UPDATE,
    GSR_STATUS_NOT_INITIALIZED,
    GSR_STATUS_INVALID_ARGUMENT,
    GSR_STATUS_UNDERFLOW,
    GSR_STATUS_OVERFLOW
} gsr_status_t;


typedef struct {
    const dlc_config_t *dlc_cfg;
    uint8_t            *results_buf;
    uint16_t            buf_size;
    uint32_t            input_samples;
} gsr_dlc_config_t;


// status translation from the VCO layer to the GSR SDK status codes
gsr_status_t gsr_status_from_vco(vco_status_t status);

//Initialize the GSR front-end with the selected VCO channel, sampling rate, and current.
gsr_status_t gsr_init(vco_channel_t channel, uint32_t refresh_rate_Hz, uint8_t idac_val);

//Initialize the GSR front-end with the selected VCO channel, sampling rate, current, and dLC config.
gsr_status_t gsr_init_dlc(vco_channel_t channel, uint32_t refresh_rate_Hz, uint8_t idac_val, const gsr_dlc_config_t *dlc_cfg);

/* Convert an iDAC code to injected current using the present front-end model. */
uint32_t gsr_current_from_idac_code_nA(uint8_t idac_code);

//Update the current used to calculate conductacnce based on the programming of the iDAC.
void gsr_update_current(uint8_t idac_val);

//Read one conductance sample in nS and optionally return the corresponding Vin.
gsr_status_t gsr_get_conductance_nS(uint32_t *conductance_nS, uint32_t *vin_uV_ret);

gsr_status_t gsr_count_to_conductance_nS(uint32_t decoder_count, uint32_t *conductance_nS, uint32_t *vin_uV_ret);

//Average multiple valid conductance samples to reduce noise.
gsr_status_t gsr_get_conductance_oversampled(uint32_t *conductance_nS, uint32_t *vin_uV_ret, int M);

#endif /* GSR_SDK_H_ */
