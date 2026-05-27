#include "VCO_dlc_sdk.h"
#include "VCO_decoder.h"
#include "timer_sdk.h"
#include "VCO_sdk.h"

#define VCO_DECODER_PHASES 62u

static vco_dlc_sdk_t s_state;

vco_status_t vco_dlc_initialize(
    vco_channel_t       channel,
    uint32_t            refresh_rate_Hz,
    const dlc_config_t *dlc_cfg,
    uint8_t            *results_buf,
    uint16_t            buf_size,
    uint32_t            input_samples
) {
    // VCO side: enable channels and set refresh rate.
    vco_status_t st = vco_initialize(channel, refresh_rate_Hz);
    if (st != VCO_STATUS_OK) return st;

    // dLC + DMA side: source is the VCO counter register, paced by EXT_RX.
    dlc_status_t dlc_st = dlc_init(
        dlc_cfg,
        (uint8_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_VCO_DECODER_CNT_REG_OFFSET),
        DMA_TRIG_SLOT_EXT_RX,
        DMA_DATA_TYPE_HALF_WORD,
        results_buf, buf_size, input_samples
    );
    if (dlc_st != DLC_STATUS_OK) return VCO_STATUS_NOT_INITIALIZED;

    // We add a wait for the VCO to settle before setting initial level
    // timer_wait_us(100);
    // This timer was removed as it would stall and not allow the rest of the app go ahead. @Omar

    int32_t initial_count = (int32_t)VCO_get_count();

    s_state.current_level = initial_count >> dlc_cfg->log_level_width;
    dlc_set_initial_level((uint32_t)s_state.current_level);

    s_state.level_width     = (1u << dlc_cfg->log_level_width);
    s_state.refresh_rate_Hz = refresh_rate_Hz;
    s_state.channel         = channel;
    s_state.initialized     = true;

    return VCO_STATUS_OK;
}

vco_status_t vco_dlc_config(vco_channel_t channel, uint32_t refresh_rate_Hz) {
    vco_status_t st = vco_config(channel, refresh_rate_Hz, 1U);
    if (st != VCO_STATUS_OK) return st;
    s_state.refresh_rate_Hz = refresh_rate_Hz;
    s_state.channel         = channel;
    return VCO_STATUS_OK;
}

vco_status_t vco_dlc_process_event(uint8_t packed_event, uint32_t *vin_uV) {
    if (!s_state.initialized) return VCO_STATUS_NOT_INITIALIZED;
    if (!vin_uV)               return VCO_STATUS_INVALID_ARGUMENT;

    int16_t  dlvl;
    uint16_t dt;    // dt = periods since last crossing
    dlc_status_t st = dlc_decode_event(packed_event, &dlvl, &dt);

    switch (st) {
    case DLC_STATUS_OK:           break;
    case DLC_STATUS_NO_EVENT:     return VCO_STATUS_NO_NEW_SAMPLE;
    case DLC_STATUS_INVALID_EVENT: return VCO_STATUS_MISSED_UPDATE;
    default:                      return VCO_STATUS_NOT_INITIALIZED;
    }

    s_state.current_level += dlvl;

    if (s_state.current_level < 0) {
        s_state.current_level = 0;
        return VCO_STATUS_MISSED_UPDATE;
    }

    // VCO_DECODER_CNT is expressed in phase-count units. Convert back to
    // oscillator cycles before multiplying by the sampling rate.
    uint64_t phase_counts_per_sample =
        (uint64_t)(uint32_t)s_state.current_level * s_state.level_width;
    uint32_t freq_Hz =
        (uint32_t)((phase_counts_per_sample * s_state.refresh_rate_Hz) / VCO_DECODER_PHASES);

    *vin_uV = interpolate_Vin_uV(freq_Hz);
    return VCO_STATUS_OK;
}
