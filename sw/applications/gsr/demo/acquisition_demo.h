// Copyright 2026 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
// Both raw counters remain available; only one difference is budgeted in SRAM1.
#include "VCO_decoder.h"
#ifdef GSR_USE_DLC
#include "dlc_demo.h"
#endif

static bool valid_rate(uint32_t rate) {
    return rate >= 100U && rate <= 10000000U &&
        (rate < 1000U || rate % 1000U == 0U) &&
        (rate < 1000U || rate / 1000U <= SYS_FCLK_HZ / 100U) &&
        (uint64_t)SYS_FCLK_HZ * 1000U / rate <= UINT32_MAX;
}
static uint32_t phase_frequency(uint32_t phases, uint32_t cycles) {
    return (uint32_t)((uint64_t)phases * SYS_FCLK_HZ / ((uint64_t)cycles * 62U));
}
static void raw_metadata(uint32_t rate, uint32_t supply, uint32_t current) {
    _writestr("A"); field(GSR_DIFFERENTIAL_MODE); field(SYS_FCLK_HZ);
    field(rate); field(supply); field(current); field(timer_get_cycles());
    field(GSR_DLC_ENABLED);
    _writestr("\n");
}
static void raw_sample(uint32_t index, const vco_pair_sample_t *pair,
                       uint32_t current, int32_t difference, uint32_t difference_rate) {
    _writestr("R"); field(index); field(timer_get_cycles()); field(current);
    field(pair->p_Hz); field(pair->n_Hz); field(pair->p_count); field(pair->n_count);
    field(pair->p_fine); field(pair->n_fine);
#if GSR_DIFFERENTIAL_MODE != 2
    signed_field(difference);
#else
    field(0); // GUI computes the difference from the two reported readings.
#endif
    field(difference_rate);
    field(pair->p_Hz >= VCO_MIN_FREQUENCY_HZ && pair->p_Hz <= VCO_MAX_FREQUENCY_HZ &&
          pair->n_Hz >= VCO_MIN_FREQUENCY_HZ && pair->n_Hz <= VCO_MAX_FREQUENCY_HZ);
    _writestr("\n");
}

static int run_acquisition(const uart_t *uart) {
    uint32_t rate = gsr_sample_rate_millihz, supply_setting = gsr_supply_rate_millihz;
    uint32_t current = gsr_injected_current_nA;
    uint32_t supply, hardware_rate, refresh_cycles, period, supply_period;
    uint32_t p_sum = 0, n_sum = 0, p_cycles = 0, n_cycles = 0;
    uint32_t p_schedule = 0, n_schedule = 0;
    uint32_t p_hz = 0, n_hz = 0, index = 0;
    uint32_t last_feedback = timer_get_cycles(), last_heartbeat = last_feedback;
    uint32_t last_signal = last_feedback;
    uint32_t last_counter = last_feedback;
    bool warning = false;
    bool lead_off = true;
#ifdef GSR_USE_DLC
    bool dlc_reseed = false;
#endif
    bool p_ready = false;
    uint32_t last_dlc_launch = last_signal;
    current_command_t command = {0};

configure:
    if (!valid_rate(rate) || (supply_setting && !valid_rate(supply_setting))) {
        _writestr("[i] Sampling configuration invalid: check timer/rate limits.\n"); return 1;
    }
#if GSR_DIFFERENTIAL_MODE == 0
    supply = rate;
#else
    supply = supply_setting ? supply_setting : rate;
#endif
    hardware_rate = (rate > supply ? rate : supply) / 1000U;
    if (!hardware_rate) hardware_rate = 1U;
    refresh_cycles = SYS_FCLK_HZ / hardware_rate;
    period = (uint32_t)((uint64_t)SYS_FCLK_HZ * 1000U / rate);
    supply_period = (uint32_t)((uint64_t)SYS_FCLK_HZ * 1000U / supply);
    p_sum = n_sum = p_cycles = n_cycles = p_hz = n_hz = 0;
    p_schedule = n_schedule = 0;
    p_ready = false;
    lead_off = true;
#ifdef GSR_USE_DLC
    dlc_reseed = false;
#endif
    iDACs_set_currents(current / IDAC_LSB_NA, 0);
    raw_metadata(rate, supply, current);
#if defined(GSR_USE_DLC) && GSR_DIFFERENTIAL_MODE == 0
    if (dlc_prepare(rate, supply, current, 0U, 0)) return 1;
#else
    if (vco_initialize(VCO_CHANNEL_DIFFERENTIAL, hardware_rate) != VCO_STATUS_OK) return 1;
#ifdef GSR_USE_DLC
    dlc_initialized = dlc_pending = false;
#endif
#endif
    last_signal = timer_get_cycles();
    last_counter = last_signal;
    last_dlc_launch = last_signal;
    print_setting("Current set: ", current, " nA\n");
    print_setting("Sampling set: ", rate, " mHz\n");
    print_setting("Supply sampling set: ", supply_setting, " mHz\n");
    _writestr("[i] GSR demo ready.\n");

    while (1) {
        poll_current_command(uart, &command);
        uint32_t now = timer_get_cycles();
        vco_pair_sample_t pair = {0};
        vco_status_t status = vco_get_pair(&pair);
        bool fresh = status == VCO_STATUS_OK || status == VCO_STATUS_UNDERFLOW ||
                     status == VCO_STATUS_OVERFLOW;
        if (fresh) last_counter = now;
        // Both stopped oscillators can leave the latch completely unchanged.
        // Still drain DMA and accept live configuration in that case.
        bool stalled = (uint32_t)(now-last_counter) > refresh_cycles * 2U;
        if (stalled) {
            lead_off = true;
#ifdef GSR_USE_DLC
            dlc_reseed = true;
#endif
            if (!warning) {
                _writestr("[i] Lead-off: counters stopped; sample transmission suspended. Check contact.\n");
                warning = true;
            }
        }
        if (fresh) {
            if (pair.p_Hz < VCO_MIN_FREQUENCY_HZ || pair.n_Hz < VCO_MIN_FREQUENCY_HZ) {
                lead_off = true;
#ifdef GSR_USE_DLC
                dlc_reseed = true;
#endif
                if (!warning) {
                    _writestr("[i] Lead-off: no usable signal on P or N; sample transmission suspended. Check contact.\n");
                    warning = true;
                }
            } else {
                lead_off = false;
            }
        }
        if (lead_off) {
            // Do not integrate across a gap, including two stopped counters.
            p_sum = n_sum = p_cycles = n_cycles = p_schedule = n_schedule = 0;
            p_ready = false;
        }
#if GSR_DIFFERENTIAL_MODE == 0
        if (status == VCO_STATUS_OK || status == VCO_STATUS_UNDERFLOW || status == VCO_STATUS_OVERFLOW) {
            p_hz = pair.p_Hz; n_hz = pair.n_Hz;
        }
#endif
#ifdef GSR_USE_DLC
        if ((fresh || lead_off) && dlc_output(current, p_hz, n_hz, !lead_off && !dlc_reseed)) return 1;
        // Discard crossings accumulated during disconnection, then establish
        // a new baseline before transmitting the recovered signal.
        if (!lead_off && dlc_reseed && !dlc_pending) goto configure;
        now = timer_get_cycles();
        // Finish the transaction before touching its source or reinitializing DMA.
        if (fresh && !lead_off && !dlc_reseed && (uint32_t)(now - last_heartbeat) >= SYS_FCLK_HZ) {
            _writestr("H"); field(now); field(p_hz); field(n_hz); field(current); _writestr("\n");
            last_heartbeat = now;
        }
#endif
        uint32_t request = gsr_injected_current_nA;
        if (request != current) {
#ifdef GSR_USE_DLC
            if (dlc_pending) continue;
#endif
            if (request < IDAC_LSB_NA || request > IDAC_LSB_NA * IDAC_MAX_CODE || request % IDAC_LSB_NA) {
                gsr_injected_current_nA = current;
                _writestr("[i] Current change failed: use 40 nA steps.\n");
            } else {
                iDACs_set_currents(request / IDAC_LSB_NA, 0);
                uint32_t code = mmio_region_read32(mmio_region_from_addr((uintptr_t)IDAC_CTRL_START_ADDRESS),
                    IDAC_CTRL_CURRENT_REG_OFFSET) & IDAC_CTRL_CURRENT_CURRENT_1_MASK;
                if (code == request / IDAC_LSB_NA) {
                    // Re-seed integration after the current step rather than
                    // attributing a pre-change frame to the newly applied current.
                    current = request; goto configure;
                } else {
                    gsr_injected_current_nA = current; iDACs_set_currents(current / IDAC_LSB_NA, 0);
                    _writestr("[i] Current change failed: iDAC readback mismatch.\n");
                }
            }
        }
        uint32_t requested_rate = gsr_sample_rate_millihz, requested_supply = gsr_supply_rate_millihz;
        if (requested_rate != rate || requested_supply != supply_setting) {
#ifdef GSR_USE_DLC
            if (dlc_pending) continue;
#endif
            if (!valid_rate(requested_rate) || (requested_supply && !valid_rate(requested_supply))) {
                gsr_sample_rate_millihz = rate; gsr_supply_rate_millihz = supply_setting;
                _writestr("[i] Sampling change failed: outside timer/rate limits.\n");
            } else {
                rate = requested_rate; supply_setting = requested_supply; goto configure;
            }
        }
#if defined(GSR_USE_DLC) && GSR_DIFFERENTIAL_MODE == 0
        if (!lead_off && !dlc_pending && (rate >= 1000U || (uint32_t)(now-last_dlc_launch) >= period)) {
            if (dlc_submit(0, p_hz, n_hz)) return 1;
            last_dlc_launch = now;
        }
#endif
        if (status == VCO_STATUS_NO_NEW_SAMPLE) {
            if ((uint32_t)(now-last_signal) >= FEEDBACK_PERIOD_CC &&
                (uint32_t)(now-last_feedback) >= FEEDBACK_PERIOD_CC) {
                _writestr("[i] No fresh counters: check contact; lower sample rate if counts are sparse.\n");
                last_feedback = now;
            }
            continue;
        }
        if (lead_off) continue;
        if (status == VCO_STATUS_MISSED_UPDATE) {
            p_sum = n_sum = p_cycles = n_cycles = 0;
            p_schedule = n_schedule = 0;
        } else if (status == VCO_STATUS_OK || status == VCO_STATUS_UNDERFLOW || status == VCO_STATUS_OVERFLOW) {
            int32_t difference = 0;
#if GSR_DIFFERENTIAL_MODE == 0
            p_hz = pair.p_Hz; n_hz = pair.n_Hz;
            if (rate < 1000U && (uint32_t)(now - last_signal) < period) continue;
            difference = pair.differential_count;
#else
            p_sum += pair.p_phase_counts; n_sum += pair.n_phase_counts;
            p_cycles += refresh_cycles; n_cycles += refresh_cycles;
            p_schedule += refresh_cycles; n_schedule += refresh_cycles;
            if (p_schedule >= supply_period) {
                p_hz = phase_frequency(p_sum, p_cycles); p_sum = p_cycles = 0;
                p_schedule %= supply_period;
                p_ready = true;
            }
            if (n_schedule < period || !p_ready) continue;
            n_hz = phase_frequency(n_sum, n_cycles); n_sum = n_cycles = 0;
            n_schedule %= period;
            pair.p_Hz = p_hz; pair.n_Hz = n_hz;
#if GSR_DIFFERENTIAL_MODE == 1
            // Calibrated frequencies differ by at most 1.03 MHz: x62 fits
            // int32. Unsigned arithmetic defines wrap even on invalid inputs.
            difference = (int32_t)((p_hz - n_hz) * 62U) / (int32_t)(rate < 1000U ? 1U : rate / 1000U);
#endif
#endif
            last_signal = now;
#ifdef GSR_USE_DLC
#if GSR_DIFFERENTIAL_MODE == 1
            uint32_t dma_wait_start = timer_get_cycles();
            while (dlc_pending) {
                if (dlc_output(current, p_hz, n_hz, true)) return 1;
                if (dlc_pending && (uint32_t)(timer_get_cycles()-dma_wait_start) > refresh_cycles * 2U) {
                    _writestr("[i] dLC DMA timeout: reset board.\n"); return 1;
                }
            }
            if (!dlc_initialized) {
                dlc_reference_p = p_hz; dlc_reference_n = n_hz;
                if (dlc_prepare(rate, supply, current, p_hz, difference)) return 1;
            }
#endif
#endif
            raw_sample(index++, &pair, current, difference, rate < 1000U ? 1U : rate / 1000U);
#if defined(GSR_USE_DLC) && GSR_DIFFERENTIAL_MODE == 1
            if (!dlc_pending && dlc_submit(difference, p_hz, n_hz)) return 1;
#endif
            if (status == VCO_STATUS_OK && warning) { _writestr("[i] Signal recovered.\n"); warning = false; }
        }
        if (status != VCO_STATUS_OK && (uint32_t)(now-last_feedback) >= FEEDBACK_PERIOD_CC) {
            _writestr("[i] VCO input out of range/missed refresh: check contact; adjust current or lower sample rate.\n");
            last_feedback = now; warning = true;
        }
    }
}
