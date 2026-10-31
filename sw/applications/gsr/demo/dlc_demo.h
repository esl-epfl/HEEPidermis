// Copyright 2026 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
// Hardware dLC: DMA reads either the HW difference register or a SW input word.
#include "VCO_dlc_sdk.h"
#define DLC_EVENT_CAPACITY GSR_DLC_BUFFER_EVENTS
static uint16_t dlc_events[DLC_EVENT_CAPACITY + 1U]
    __attribute__((section(".gsr_dlc_buffer"), aligned(4)));
static uint8_t dlc_discard;
static uint32_t dlc_sequence;
static uint32_t dlc_log_width;
static uint32_t dlc_time_bits;
static bool dlc_pending, dlc_initialized;
#if GSR_DIFFERENTIAL_MODE == 1
static volatile int32_t dlc_input_count __attribute__((aligned(4)));
static uint32_t dlc_reference_p, dlc_reference_n;
static uint32_t dlc_sample_cycle;
#endif
static void dlc_clear(void) {
    for (uint32_t i = 0; i <= DLC_EVENT_CAPACITY; ++i) dlc_events[i] = 0xffffU;
}
static dlc_config_t dlc_configuration(uint32_t signal_rate, uint32_t log_width,
                                      uint32_t time_bits) {
    uint64_t maximum = (uint64_t)VCO_MAX_FREQUENCY_HZ * 62U / signal_rate;
    dlc_discard = 0;
    while (maximum > 32767U && dlc_discard < 15U) { maximum >>= 1; dlc_discard++; }
    dlc_config_t config = {
        .log_level_width = log_width, .dlvl_format = 0,
        .hysteresis_en = 0, .time_bits = time_bits,
        .discard_bits = dlc_discard, .single_shot = true, .halfword_output = true,
    };
    return config;
}
static void dlc_metadata(uint32_t rate, uint32_t supply, uint32_t current,
                         uint32_t p_hz, int32_t level) {
    _writestr("B"); field(SYS_FCLK_HZ); field(rate);
    field(dlc_log_width); field(dlc_time_bits); field(dlc_discard);
    signed_field(level); field(p_hz); field(current); field(idac_current_code());
    field(timer_get_cycles());
    field(GSR_DIFFERENTIAL_MODE); field(supply); field(DLC_EVENT_CAPACITY); _writestr("\n");
}
static int dlc_prepare(uint32_t rate, uint32_t supply, uint32_t current,
                       uint32_t p_hz, int32_t difference, uint32_t log_width,
                       uint32_t time_bits) {
    if (log_width > 15U || time_bits < 1U || time_bits > 6U) return 1;
    dlc_log_width = log_width; dlc_time_bits = time_bits;
    dlc_config_t config = dlc_configuration(rate < 1000U ? 1U : rate / 1000U,
                                             log_width, time_bits);
    dlc_clear();
#if GSR_DIFFERENTIAL_MODE == 0
    if (vco_dlc_initialize(VCO_CHANNEL_DIFFERENTIAL, rate < 1000U ? 1U : rate / 1000U,
            &config, (uint8_t *)dlc_events, sizeof(dlc_events), 1U) != VCO_STATUS_OK) return 1;
    int32_t level = vco_dlc_initial_level();
#else
    dlc_input_count = difference;
    int32_t level = difference >> (dlc_discard + log_width);
    dlc_set_initial_level((uint16_t)level);
    // Keep the word unchanged until this finite memory-to-dLC DMA ends.
    // RAM uses normal memory pacing. A one-cycle VCO pulse can be lost while
    // the CPU and DMA arbitrate SRAM0. Host timing uses this transaction's
    // chip timestamp, not the FIFO's input-read tick count.
    if (dlc_init(&config, (uint8_t *)&dlc_input_count, DMA_TRIG_MEMORY,
                 DMA_DATA_TYPE_WORD, (uint8_t *)dlc_events, sizeof(dlc_events), 1U) != DLC_STATUS_OK) return 1;
#endif
    dlc_initialized = dlc_pending = true; dlc_sequence = 0U;
    dlc_metadata(rate, supply, current, p_hz, level);
#if GSR_DIFFERENTIAL_MODE == 1
    dlc_sample_cycle = timer_get_cycles();
#endif
    return 0;
}
static int dlc_output(uint32_t current, uint32_t p_hz, uint32_t n_hz, bool transmit) {
    if (!dlc_pending || !dma_is_ready(0)) return 0;
    uint32_t count = 0;
    while (count < DLC_EVENT_CAPACITY && dlc_events[count] != 0xffffU) count++;
    if (dlc_events[DLC_EVENT_CAPACITY] != 0xffffU) {
        if (transmit) {
            _writestr("X");
#if GSR_DIFFERENTIAL_MODE == 1
            field(dlc_sample_cycle);
#else
            field(timer_get_cycles());
#endif
            field(DLC_EVENT_CAPACITY); _writestr("\n");
        }
        dlc_pending = dlc_initialized = false;
        return 2;
    }
    // Drain DMA even during lead-off, but never report disconnected data.
    if (count && transmit) {
#if GSR_DIFFERENTIAL_MODE == 1
        p_hz = dlc_reference_p; n_hz = dlc_reference_n;
#endif
        _writestr("D"); field(dlc_sequence++);
#if GSR_DIFFERENTIAL_MODE == 1
        field(dlc_sample_cycle);
#else
        field(timer_get_cycles());
#endif
        field(current);
        field(idac_current_code());
        field(p_hz); field(n_hz); _writestr(",");
        char hex[65];
        for (uint32_t offset = 0; offset < count; offset += 32U) {
            uint32_t size = count - offset < 32U ? count - offset : 32U;
            for (uint32_t i = 0; i < size; ++i) {
                static const char digits[] = "0123456789abcdef";
                uint8_t byte = (uint8_t)dlc_events[offset + i];
                hex[i * 2U] = digits[byte >> 4]; hex[i * 2U + 1U] = digits[byte & 15U];
            }
            hex[size * 2U] = '\0'; _writestr(hex);
        }
        _writestr("\n");
    }
    dlc_pending = false;
    return 0;
}
static int dlc_submit(int32_t difference, uint32_t p_hz, uint32_t n_hz, uint32_t sample_cycle) {
    if (!dlc_initialized || dlc_pending) return 1;
    dlc_clear();
#if GSR_DIFFERENTIAL_MODE == 1
    dlc_input_count = difference;
    dlc_reference_p = p_hz; dlc_reference_n = n_hz;
    dlc_sample_cycle = sample_cycle;
#else
    (void)sample_cycle;
#endif
    if (dlc_start_transaction() != DLC_STATUS_OK) return 1;
    dlc_pending = true;
    return 0;
}
