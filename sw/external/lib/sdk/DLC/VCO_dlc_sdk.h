#ifndef VCO_DLC_SDK_H_
#define VCO_DLC_SDK_H_

#include <stdint.h>
#include <stdbool.h>
#include "VCO_sdk.h"
#include "DLC_sdk.h"

/*
VCO adapter on top of DLC_sdk.\

DLC_sdk handles register programming, DMA wiring, raw event decoding.
This layer handles everything that knows about VCOs.

Reuses vco_status_t:
  VCO_STATUS_NO_NEW_SAMPLE – ΔLvl = 0, no crossing
  VCO_STATUS_MISSED_UPDATE – ΔT = 0, malformed event
*/

// State maintained across events to reconstruct Vin.
typedef struct {
    int32_t         current_level;   // absolute quantized level (signed)
    uint32_t        level_width;     // counts per level = 2^log_level_width
    uint32_t        refresh_rate_Hz;
    vco_channel_t   channel;
    bool            initialized;
} vco_dlc_sdk_t;


// Initialize the VCO + dLC + DMA pipeline.
vco_status_t vco_dlc_initialize(
    vco_channel_t   channel,
    uint32_t        refresh_rate_Hz,
    const dlc_config_t *dlc_cfg,
    uint8_t        *results_buf,
    uint16_t        buf_size,
    uint32_t        input_samples
);

vco_status_t vco_dlc_config(vco_channel_t channel, uint32_t refresh_rate_Hz);

//Decode one dLC event and return the reconstructed Vin.
vco_status_t vco_dlc_process_event(uint8_t packed_event, uint32_t *vin_uV);

#endif /* VCO_DLC_SDK_H_ */
