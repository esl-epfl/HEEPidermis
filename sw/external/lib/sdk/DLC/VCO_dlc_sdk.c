#include "VCO_dlc_sdk.h"
#include "VCO_decoder.h"
#include "timer_sdk.h"
#include "VCO_sdk.h"

#define VCO_DECODER_PHASES 62u
#define VCO_DLC_SIGNED_INPUT_MAX 32767u
#define VCO_DLC_MAX_FOSC_HZ 1051710u
#define VCO_DLC_SYS_FCLK_HZ 10000000u
#define VCO_DLC_SIM_ACCEL_RATIO 100u
#define VCO_DLC_INIT_REFRESH_WAIT 4u

static vco_dlc_sdk_t s_state;

static uint32_t vco_dlc_refresh_cycles(uint32_t refresh_rate_Hz) {
    if (refresh_rate_Hz == 0) {
        return 0;
    }

#if TARGET_SIM
    return VCO_DLC_SYS_FCLK_HZ / (VCO_DLC_SIM_ACCEL_RATIO * refresh_rate_Hz);
#else
    return VCO_DLC_SYS_FCLK_HZ / refresh_rate_Hz;
#endif
}

static void vco_dlc_busy_wait(uint32_t cycles) {
    uint32_t start = timer_get_cycles();

    while ((uint32_t)(timer_get_cycles() - start) < cycles) {
        asm volatile("nop");
    }
}

static uint8_t vco_dlc_required_discard(uint32_t refresh_rate_Hz) {
    uint64_t max_count;
    uint8_t discard = 0;

    if (refresh_rate_Hz == 0) {
        return 0;
    }

    max_count = ((uint64_t)VCO_DLC_MAX_FOSC_HZ * VCO_DECODER_PHASES) /
                refresh_rate_Hz;
    while ((max_count >> discard) > VCO_DLC_SIGNED_INPUT_MAX && discard < 15u) {
        discard++;
    }

    return discard;
}

vco_status_t vco_dlc_initialize(
    vco_channel_t       channel,
    uint32_t            refresh_rate_Hz,
    const dlc_config_t *dlc_cfg,
    uint8_t            *results_buf,
    uint16_t            buf_size,
    uint32_t            input_samples
) {
    if (!dlc_cfg || !results_buf || buf_size == 0 || input_samples == 0) {
        return VCO_STATUS_INVALID_ARGUMENT;
    }

    // VCO side: enable channels and set refresh rate.
    vco_status_t st = vco_initialize(channel, refresh_rate_Hz);
    if (st != VCO_STATUS_OK) return st;

    uint8_t input_discard = vco_dlc_required_discard(refresh_rate_Hz);
    if (dlc_cfg->discard_bits > input_discard) {
        input_discard = dlc_cfg->discard_bits;
    }
    if (input_discard > dlc_cfg->log_level_width) {
        return VCO_STATUS_INVALID_ARGUMENT;
    }

    /*
     * The dLC level extractor truncates to signed 16-bit after discard_bits.
     * VCO counts are unsigned, so pre-discard enough bits to avoid wrapping
     * past 0x7fff while keeping the requested effective level width.
     */
    dlc_config_t hw_dlc_cfg = *dlc_cfg;
    hw_dlc_cfg.discard_bits = input_discard;
    hw_dlc_cfg.log_level_width = (uint8_t)(dlc_cfg->log_level_width - input_discard);

    /*
     * Let the VCO decoder produce at least one fresh count before seeding the
     * dLC level. If the dLC starts from zero, the first capture is dominated
     * by a synthetic overflow burst.
     */
    uint32_t refresh_cycles = vco_dlc_refresh_cycles(refresh_rate_Hz);
    vco_dlc_busy_wait(refresh_cycles * VCO_DLC_INIT_REFRESH_WAIT);

    int32_t initial_count = (int32_t)VCO_get_count();
    s_state.current_level = initial_count >> dlc_cfg->log_level_width;
    hw_dlc_cfg.initial_level = (uint32_t)s_state.current_level;

    // dLC + DMA side: source is the VCO counter register, paced by EXT_RX.
    dlc_status_t dlc_st = dlc_init(
        &hw_dlc_cfg,
        (uint8_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_VCO_DECODER_CNT_REG_OFFSET),
        DMA_TRIG_SLOT_EXT_RX,
        DMA_DATA_TYPE_WORD,
        results_buf, buf_size, input_samples
    );
    if (dlc_st != DLC_STATUS_OK) return VCO_STATUS_NOT_INITIALIZED;

    s_state.level_width     = (1u << dlc_cfg->log_level_width);
    s_state.refresh_rate_Hz = refresh_rate_Hz;
    s_state.input_discard_bits = input_discard;
    s_state.channel         = channel;
    s_state.initialized     = true;

    return VCO_STATUS_OK;
}

vco_status_t vco_dlc_process_event_with_dt(uint8_t packed_event, uint32_t *vin_uV, uint16_t *dt_ret) {
    if (!s_state.initialized) return VCO_STATUS_NOT_INITIALIZED;
    if (!vin_uV)               return VCO_STATUS_INVALID_ARGUMENT;

    int16_t  dlvl;
    uint16_t dt;    // dt = periods since last crossing
    dlc_status_t st = dlc_decode_event(packed_event, &dlvl, &dt);
    if (dt_ret != 0) {
        *dt_ret = dt;
    }

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

vco_status_t vco_dlc_process_event(uint8_t packed_event, uint32_t *vin_uV) {
    return vco_dlc_process_event_with_dt(packed_event, vin_uV, 0);
}

int32_t vco_dlc_get_current_level(void) {
    return s_state.current_level;
}

uint8_t vco_dlc_get_input_discard_bits(void) {
    return s_state.input_discard_bits;
}
