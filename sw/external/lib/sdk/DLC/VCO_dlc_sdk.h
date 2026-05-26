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
    uint8_t         input_discard_bits;
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


//Decode one dLC event and return the reconstructed Vin.
vco_status_t vco_dlc_process_event(uint8_t packed_event, uint32_t *vin_uV);

//Decode one dLC event and return reconstructed Vin plus elapsed dLC sample ticks.
vco_status_t vco_dlc_process_event_with_dt(uint8_t packed_event, uint32_t *vin_uV, uint16_t *dt_ret);

// Return the current absolute DLC level (used to emit INITIAL_LEVEL in the output header).
int32_t vco_dlc_get_current_level(void);

// Return the VCO-input discard applied before the dLC level shift.
uint8_t vco_dlc_get_input_discard_bits(void);

#endif /* VCO_DLC_SDK_H_ */
