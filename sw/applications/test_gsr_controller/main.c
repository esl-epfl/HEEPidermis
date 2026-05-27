#include "timer_sdk.h"
#include "soc_ctrl.h"
#include "REFs_ctrl.h"
#include "iDAC_ctrl.h"
#include "x-heep.h"
#include <stdio.h>

#include "VCO_sdk.h"
#include "GSR_sdk.h"
#include "GSR_controller.h"

#define PRINTF_IN_SIM  0
#define PRINTF_IN_FPGA 0
#define TARGET_SIM     1

#if TARGET_SIM && PRINTF_IN_SIM
    #define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#elif PRINTF_IN_FPGA && !TARGET_SIM
    #define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#else
    #define PRINTF(...)
#endif

#define SYS_FCLK_HZ         10000000U
#define IREF_DEFAULT_CAL    255U
#define IDAC_DEFAULT_CAL    15U
#define VREF_DEFAULT_CAL    0b1111111111U

#define SAMPLE_ATTEMPT_LIMIT  10U
#define N_READ_STEPS          2U

#define GSR_VCO_SUPPLY_VOLTAGE_UV 800000U
#define GSR_VIN_MIN_UV            330000U
#define TEST_MEASUREMENT        0

#define VCO_ACCEL_RATIO 100 // The ratio by which the VCO is accelerated in simulation to allow faster testing. The refresh rate and integration rate are divided by this factor in simulation mode.
volatile uint32_t debug __attribute__((section(".xheep_debug_mem")));

void __attribute__((aligned(4), interrupt)) handler_irq_timer(void) {
    vco_handle_timer_irq();
    debug = 'wake';
    return;
}

static void hw_init(void) {
    soc_ctrl_t soc_ctrl;
    soc_ctrl.base_addr = mmio_region_from_addr((uintptr_t)SOC_CTRL_START_ADDRESS);
    soc_ctrl_set_frequency(&soc_ctrl, SYS_FCLK_HZ);

    REFs_calibrate(IREF_DEFAULT_CAL, IREF1);
    REFs_calibrate(VREF_DEFAULT_CAL, VREF);

    iDACs_enable(true, false);
    iDAC1_calibrate(IDAC_DEFAULT_CAL);

    enable_timer_interrupt();   // Enable the timer machine-level interrupt
    timer_irq_enable();
    timer_cycles_init();
    timer_start();
}

static uint32_t max_current_for_conductance_nS_test(uint32_t conductance_nS) {
    const uint32_t delta_v_min_uV = GSR_VCO_SUPPLY_VOLTAGE_UV - GSR_VIN_MIN_UV;
    return (uint32_t)(((uint64_t)conductance_nS * delta_v_min_uV) / 1000000ULL);
}

static int init_default_controller(gsr_controller_t *ctrl) {
    gsr_status_t st;

    st = gsr_set_default_settings(ctrl);
    if (st != GSR_STATUS_OK) {
        PRINTF("  FAIL: gsr_set_default_settings returned %d\n", st);
        return -1;
    }

    st = gsr_controller_init(ctrl);
    if (st != GSR_STATUS_OK) {
        PRINTF("  FAIL: gsr_controller_init returned %d\n", st);
        return -1;
    }

    return 0;
}

static uint32_t refresh_wait_cycles(uint32_t refresh_rate_Hz) {
#if TARGET_SIM
    return SYS_FCLK_HZ / (VCO_ACCEL_RATIO * refresh_rate_Hz);
#else
    return SYS_FCLK_HZ / refresh_rate_Hz;
#endif
}
static void wait_for_next_refresh(uint32_t refresh_rate_Hz) {
    uint32_t now;

    if (refresh_rate_Hz == 0U) return;
    now = timer_get_cycles();
    uint32_t wake_at = now + refresh_wait_cycles(refresh_rate_Hz);// - 5700U; // Subtract some cycles to account for the overhead of the timer operations and ensure we wake up shortly after the next refresh starts.
                                                                            // 5700 should be computed after profiling gsr_read_sample
    timer_irq_clear();
    timer_arm_set(wake_at);
    timer_irq_enable();
    debug = 'slep';
    asm volatile ("wfi");
}

static int wait_for_read_sample_status(gsr_controller_t *ctrl,
                                       gsr_status_t *final_status) {
    uint32_t attempts = 0U;

    while (attempts < SAMPLE_ATTEMPT_LIMIT) {
        uint32_t refresh_rate_Hz = ctrl->config.current_refresh_rate_Hz;
        gsr_status_t st = gsr_read_sample(ctrl);
        debug = st;
        debug = ctrl->sample.G_nS;

        if (st == GSR_STATUS_OK) {
            *final_status = st;
            if (refresh_rate_Hz == 0U) {
                refresh_rate_Hz = ctrl->config.baseline_refresh_rate_Hz;
            }
            wait_for_next_refresh(refresh_rate_Hz);
            return 0;
        }

        if (st == GSR_STATUS_NOT_INITIALIZED ||
            st == GSR_STATUS_NO_NEW_SAMPLE || st == GSR_STATUS_OVERFLOW || st ==GSR_STATUS_UNDERFLOW) {
            if (refresh_rate_Hz == 0U) {
                refresh_rate_Hz = ctrl->config.baseline_refresh_rate_Hz;
            }
            wait_for_next_refresh(refresh_rate_Hz);
            attempts++;
            continue;
        }

        if (st == GSR_STATUS_MISSED_UPDATE) { // poll again immediately 
            attempts++;
            continue;
        }

        PRINTF("  FAIL: gsr_read_sample returned %d\n", (int)st);
        return -1;
    }

    PRINTF("  FAIL: timed out waiting for gsr_read_sample\n");
    return -1;
}

// Stupid waiting loop (should be timer based for real application)
static void wait_cycles_busy(uint32_t cycles)
{
    uint32_t start = timer_get_cycles();
    while ((uint32_t)(timer_get_cycles() - start) < cycles) {
    }
}

/*
This test function tests:
- reading samples with the default controller configuration
- changing the controller configuration (current, refresh rate)
- timer based sampling
- i_dc out of range 
- VCO duty cycling
*/
static int test_all()
{
    gsr_controller_t ctrl;
    const gsr_sample_t *sample;
    gsr_status_t st = GSR_STATUS_NOT_INITIALIZED;
    uint8_t steps_done = 0;
    PRINTF("GSR controller set_config\n");
    debug = 'set';
    if (init_default_controller(&ctrl) != 0) {
        return -1;
    }
    
    debug ='wait';
    timer_wait_us(1000); 
    
    /* 
    * ----------------------------Test 1: basic config + sample reading (timer based)-----------------------------
    */
    debug = 'tst1';
    while (steps_done<N_READ_STEPS) {
    
        if (wait_for_read_sample_status(&ctrl, &st) != 0) {
            return -1;
        }
        sample = gsr_get_last_sample(&ctrl);
        if (sample == NULL || !sample->valid) {
            PRINTF("  FAIL: no valid sample stored after gsr_read_sample(M=1)\n");
            return -1;
        }
        steps_done++;
    }
    /* 
    * ----------------------------Test 2: updated config (refresh rate + current) + sample reading (timer based)-----------------------------
    */
    ctrl.config.idac_code = 15U; // Set to a different value than default to test the update
    ctrl.mode = GSR_CTRL_MODE_PHASIC; // Also update refresh rate
    PRINTF("new: idac_code=%d, max_uidc%d\n", ctrl.config.idac_code, ctrl.max_current_nA);
    st = gsr_controller_set_config(&ctrl);
    if (st != GSR_STATUS_OK) {
        PRINTF("  FAIL: gsr_set_config returned %d\n", st);
        return -1;
    }
    debug = 'tst2';
    steps_done = 0;
    // After setting new config, read a sample to verify the new current is applied
    while (steps_done<N_READ_STEPS) {
    
        if (wait_for_read_sample_status(&ctrl, &st) != 0) {
            return -1;
        }
        sample = gsr_get_last_sample(&ctrl);
        if (sample == NULL || !sample->valid) {
            PRINTF("  FAIL: no valid sample stored after gsr_read_sample(M=1)\n");
            return -1;
        }
        steps_done++;
    }
    /* 
    * ----------------------------Test 3: test idc out of range guard + measurements -----------------------------
    */
    debug = 'tst3';
    // 1. try setting i_dc to max allowed, which should trigger idc out of range 
    debug = ctrl.max_current_nA;
    ctrl.mode = GSR_CTRL_MODE_BASELINE;
    ctrl.config.idac_code = ctrl.max_current_nA/40 ; // idac(max) > max - guard ==> it will trigger out of range
    st = gsr_controller_set_config(&ctrl);
    if (st != GSR_STATUS_UNDERFLOW) {
        debug = (0xF1 << 24 | st); 
        PRINTF("  FAIL: gsr_set_config returned %d\n", st);
        return -1;
    }
    // 2. change i_dc to pass the guard  
    if (st == GSR_STATUS_UNDERFLOW) {
        ctrl.config.idac_code = ctrl.max_current_nA/40 - 2; // idac(max - 80) < max - guard ==> should not trigger out of range
        st = gsr_controller_set_config(&ctrl);
    }
    if (st != GSR_STATUS_OK) {
        debug = (0xF2 << 24 | st); 
        PRINTF("  FAIL: gsr_set_config returned %d\n", st);
        return -1;
    }
    // 3. test that measurements work (tested on a sample)
    steps_done = 0;
    while (steps_done<2) {
    
        if (wait_for_read_sample_status(&ctrl, &st) != 0) {
            debug = (0xF3 << 24 | st); 
            return -1;
        }
        if (steps_done == 0 && TEST_MEASUREMENT) {
            sample = gsr_get_last_sample(&ctrl);
            debug = (1 << 24 | sample->current_nA);
            debug = (2 << 24 | sample->valid); 
            debug = (3 << 24 | sample->baseline_nS); 
            debug = (4 << 24 | sample->amplitude_nS); 
            gsr_metrics_t metrics = get_metrics(&ctrl);
            debug = (5 << 24 | metrics.conductance_sensitivity_nS);
            debug = (6 << 24 | metrics.resolution_dB);

            if (sample->current_nA != 2240 || // these values are not valid anymore (need to find another data point for the test)
                sample->valid != 1 ||
                sample->baseline_nS != 4901 ||
                sample->amplitude_nS!= 80 ||
                metrics.conductance_sensitivity_nS!=3 ||
                metrics.resolution_dB!=2700) 
            {    
                debug = (0xF4 << 24); 
                return -1;
            }
            debug='m_ok'; //measurements ok
        }
        steps_done++;
    }

    /*
    * ----------------------------Test 4: duty cycling through gsr_controller -----------------------------
    */

    debug = 'tst4';

    ctrl.mode = GSR_CTRL_MODE_BASELINE;
    ctrl.config.idac_code = 20; // get back to current further from the limit
    
    /* LOW power: D = 25% */
    ctrl.config.duty_cycle_code = 4U;

    st = gsr_controller_set_config(&ctrl);
    if (st != GSR_STATUS_OK) {
        debug = (0xF5 << 24) | st;
        return -1;
    }
    debug = 'L25';
    steps_done = 0;
    uint32_t attempts = 0U;
    while (steps_done<N_READ_STEPS) {
        if (attempts++ >= SAMPLE_ATTEMPT_LIMIT) {
            debug = (0xF6 << 24) | st;
            return -1;
        }
        st = gsr_read_sample(&ctrl);
        debug = st;
        if (st != GSR_STATUS_OK) {
            if (st == GSR_STATUS_NO_NEW_SAMPLE || st == GSR_STATUS_MISSED_UPDATE) {
                continue;
            }
            debug = (0xF6 << 24) | st;
            return -1;
        }
        sample = gsr_get_last_sample(&ctrl);
        if (sample == NULL || !sample->valid) {
            debug = (0xF7 << 24);
            return -1;
        }
        debug = sample->G_nS;
        steps_done++;
    }

    /* MID power: D = 50% */
    ctrl.config.duty_cycle_code = 2U;
    debug = 'M50';
    st = gsr_controller_set_config(&ctrl);
    if (st != GSR_STATUS_OK) {
        debug = (0xF8 << 24) | st;
        return -1;
    }
    steps_done = 0;
    attempts = 0U;
    while (steps_done<N_READ_STEPS) {
        if (attempts++ >= SAMPLE_ATTEMPT_LIMIT) {
            debug = (0xF9 << 24) | st;
            return -1;
        }
        st = gsr_read_sample(&ctrl);
        debug = st;
        if (st != GSR_STATUS_OK) {
            if (st == GSR_STATUS_NO_NEW_SAMPLE || st == GSR_STATUS_MISSED_UPDATE) {
                continue;
            }
            debug = (0xF9 << 24) | st;
            return -1;
        }
        sample = gsr_get_last_sample(&ctrl);
        if (sample == NULL || !sample->valid) {
            debug = (0xFA << 24);
            return -1;
        }
        debug = ctrl.sample.G_nS;
        steps_done++;
    }
    /* HIGH power: D = 100% */
    ctrl.config.duty_cycle_code = 1U;
    debug = 'H10';
    st = gsr_controller_set_config(&ctrl);
    if (st != GSR_STATUS_OK) {
        debug = (0xFB << 24) | st;
        return -1;
    }
    steps_done = 0;
    while (steps_done<N_READ_STEPS) {
        if (wait_for_read_sample_status(&ctrl, &st) != 0) {
            debug = (0xFC << 24) | st;
            return -1;
        }
        sample = gsr_get_last_sample(&ctrl);
        if (sample == NULL || !sample->valid) {
            debug = (0xFD << 24);
            return -1;
        }
        debug = st;
        debug = ctrl.sample.G_nS;
        steps_done++;
    }

    return 0;
}

int main(void)
{
    debug = 'Init';

    hw_init();

    int failures = 0;

    failures += (test_all() != 0) ? 1 : 0;

    if (failures == 0) {
        debug = 'pass';
        PRINTF("=== ALL TESTS PASSED ===\n");
    } else {
        PRINTF("=== %d TEST(S) FAILED ===\n", failures);
    }
    debug = 'stop';

    return failures;
}
