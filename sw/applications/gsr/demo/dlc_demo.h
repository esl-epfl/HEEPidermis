// Copyright 2026 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
// Hardware dLC: DMA reads either the HW difference register or a SW input word.
#include "VCO_dlc_sdk.h"
#if GSR_DLC_TIME_BITS < 1 || GSR_DLC_TIME_BITS > 6 || GSR_DLC_LOG_WIDTH < 0 || GSR_DLC_LOG_WIDTH > 15
#error "dLC: time bits must be 1..6 and log2(level width) 0..15"
#endif
#define DLC_MAG_MAX ((1U << (7U - GSR_DLC_TIME_BITS)) - 1U)
#define DLC_EVENT_LIMIT (((65535U >> GSR_DLC_LOG_WIDTH) + DLC_MAG_MAX - 1U) / DLC_MAG_MAX + 2U)
#if DLC_EVENT_LIMIT > 512
#error "dLC: increase level width or reduce time bits (maximum 512 events/input)"
#endif
static uint16_t dlc_events[DLC_EVENT_LIMIT + 1];
static uint8_t dlc_discard;
static uint32_t dlc_sequence;
static bool dlc_pending, dlc_initialized;
#if GSR_DIFFERENTIAL_MODE == 1
static volatile int32_t dlc_input_count __attribute__((aligned(4)));
static uint32_t dlc_reference_p, dlc_reference_n;
#endif
static void dlc_clear(void) {
    for (uint32_t i = 0; i <= DLC_EVENT_LIMIT; ++i) dlc_events[i] = 0xffffU;
}
static dlc_config_t dlc_configuration(uint32_t signal_rate) {
    uint64_t maximum = (uint64_t)VCO_MAX_FREQUENCY_HZ * 62U / signal_rate;
    dlc_discard = 0;
    while (maximum > 32767U && dlc_discard < 15U) { maximum >>= 1; dlc_discard++; }
    dlc_config_t config = {
        .log_level_width = GSR_DLC_LOG_WIDTH, .dlvl_format = 0,
        .hysteresis_en = 0, .time_bits = GSR_DLC_TIME_BITS,
        .discard_bits = dlc_discard, .single_shot = true, .halfword_output = true,
    };
    return config;
}
static void dlc_metadata(uint32_t rate, uint32_t supply, uint32_t current,
                         uint32_t p_hz, int32_t level) {
    _writestr("B"); field(SYS_FCLK_HZ); field(rate);
    field(GSR_DLC_LOG_WIDTH); field(GSR_DLC_TIME_BITS); field(dlc_discard);
    signed_field(level); field(p_hz); field(current); field(timer_get_cycles());
    field(GSR_DIFFERENTIAL_MODE); field(supply); _writestr("\n");
}
static int dlc_prepare(uint32_t rate, uint32_t supply, uint32_t current,
                       uint32_t p_hz, int32_t difference) {
    dlc_config_t config = dlc_configuration(rate < 1000U ? 1U : rate / 1000U);
    dlc_clear();
#if GSR_DIFFERENTIAL_MODE == 0
    if (vco_dlc_initialize(VCO_CHANNEL_DIFFERENTIAL, rate < 1000U ? 1U : rate / 1000U,
            &config, (uint8_t *)dlc_events, sizeof(dlc_events), 1U) != VCO_STATUS_OK) return 1;
    int32_t level = vco_dlc_initial_level();
#else
    dlc_input_count = difference;
    int32_t level = difference >> (dlc_discard + GSR_DLC_LOG_WIDTH);
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
    return 0;
}
static int dlc_output(uint32_t current, uint32_t p_hz, uint32_t n_hz, bool transmit) {
    if (!dlc_pending || !dma_is_ready(0)) return 0;
    uint32_t count = 0;
    while (count < DLC_EVENT_LIMIT && dlc_events[count] != 0xffffU) count++;
    if (dlc_events[DLC_EVENT_LIMIT] != 0xffffU) {
        _writestr("[i] dLC buffer full: increase level width or reduce time bits.\n"); return 1;
    }
    // Drain DMA even during lead-off, but never report disconnected data.
    if (count && transmit) {
#if GSR_DIFFERENTIAL_MODE == 1
        p_hz = dlc_reference_p; n_hz = dlc_reference_n;
#endif
        _writestr("D"); field(dlc_sequence++); field(timer_get_cycles()); field(current);
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
static int dlc_submit(int32_t difference, uint32_t p_hz, uint32_t n_hz) {
    if (!dlc_initialized || dlc_pending) return 1;
    dlc_clear();
#if GSR_DIFFERENTIAL_MODE == 1
    dlc_input_count = difference;
    dlc_reference_p = p_hz; dlc_reference_n = n_hz;
#endif
    if (dlc_start_transaction() != DLC_STATUS_OK) return 1;
    dlc_pending = true;
    return 0;
}
