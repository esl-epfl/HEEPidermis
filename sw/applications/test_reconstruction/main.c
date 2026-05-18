#include "timer_sdk.h"
#include "soc_ctrl.h"
#include "REFs_ctrl.h"
#include "iDAC_ctrl.h"
#include "x-heep.h"
#include <stdio.h>

#include "VCO_sdk.h"
#include "GSR_sdk.h"
#include "GSR_controller.h"

#define PRINTF_IN_SIM  1
#define PRINTF_IN_FPGA 1

#if TARGET_SIM && PRINTF_IN_SIM
    #define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#elif PRINTF_IN_FPGA && !TARGET_SIM
    #define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#else
    #define PRINTF(...)
#endif

#define SYS_FCLK_HZ         10000000
#define IDAC_DEFAULT_CODE   45
#define IREF_DEFAULT_CAL    255
#define IDAC_DEFAULT_CAL    15

#define SAMPLING_FREQ     2
#define N_BASIC_SAMPLES   20000000
#define OVERSAMPLE_RATIO  3
#define N_CTRL_STEPS      20000000

volatile uint32_t debug __attribute__((section(".xheep_debug_mem")));

static void hw_init(void) {

    soc_ctrl_t soc_ctrl;
    soc_ctrl.base_addr = mmio_region_from_addr((uintptr_t)SOC_CTRL_START_ADDRESS);
    soc_ctrl_set_frequency(&soc_ctrl, SYS_FCLK_HZ);

    timer_cycles_init();

    REFs_calibrate(IREF_DEFAULT_CAL, IREF1);
    REFs_calibrate(0b1111111111, VREF);

    iDACs_enable(true, false);
    iDAC1_calibrate(IDAC_DEFAULT_CAL);

    enable_timer_interrupt();
    timer_irq_enable();
    timer_start();

    gpio_cfg_t pin_cfg = {
        .pin = 0,
        .mode = GpioModeOutPushPull
    };
    gpio_config(pin_cfg);
}

static int test_gsr_single(void) {

    //PRINTF("GSR single-sample conductance\n");

    gsr_status_t st = gsr_init(VCO_CHANNEL_P, SAMPLING_FREQ, IDAC_DEFAULT_CODE);
    if (st != GSR_STATUS_OK) {
        PRINTF("  FAIL: gsr_init returned %d\n", st);
        return -1;
    }

    int valid = 0;
    int attempts = 0;
    while (1) {
        uint32_t g_nS = 0;
        uint32_t vin_uV = 0;
        st = gsr_get_conductance_nS(&g_nS, &vin_uV);

        if (st == GSR_STATUS_OK) {
            PRINTF("%lu\n", g_nS);
            debug = g_nS;
            valid++;
        } else if (st == GSR_STATUS_NO_NEW_SAMPLE) {

        } else if (st == GSR_STATUS_MISSED_UPDATE) {
            //PRINTF("  WARN: missed update\n");
        } else {
            PRINTF("  FAIL: gsr_get_conductance_nS returned %d\n", st);
            return -1;
        }
    }

    //PRINTF("GSR single-sample PASS (%d samples)\n", valid);
    return 0;
}

static int test_gsr_oversampled(void)
{
    //PRINTF("GSR oversampled conductance (ratio=%d)\n", OVERSAMPLE_RATIO);

    gsr_status_t st = gsr_init(VCO_CHANNEL_P, SAMPLING_FREQ, IDAC_DEFAULT_CODE);
    if (st != GSR_STATUS_OK) {
        PRINTF("  FAIL: gsr_init returned %d\n", st);
        return -1;
    }

    while (1) {
        uint32_t g_avg_nS = 0;
        uint32_t vin_avg_uV = 0;
        st = gsr_get_conductance_oversampled(&g_avg_nS, &vin_avg_uV, OVERSAMPLE_RATIO);
        if (st != GSR_STATUS_OK) {
            PRINTF("  FAIL: gsr_get_conductance_oversampled returned %d\n", st);
            return -1;
        }
        if (st == GSR_STATUS_OK) {
            PRINTF("%lu\n", g_avg_nS);
            debug = g_avg_nS;
        }
    }
    return 0;
}

static int test_gsr_controller(void)
{
    PRINTF("GSR controller (%d steps)\n", N_CTRL_STEPS);

    gsr_controller_t ctrl;

    gsr_status_t st = gsr_set_default_settings(&ctrl);
    if (st != GSR_STATUS_OK) {
        PRINTF("  FAIL: gsr_set_default_settings returned %d\n", st);
        return -1;
    }

    st = gsr_controller_init(&ctrl);
    if (st != GSR_STATUS_OK) {
        PRINTF("  FAIL: gsr_controller_init returned %d\n", st);
        return -1;
    }

    int steps_done = 0;
    int attempts   = 0;
    while (steps_done < N_CTRL_STEPS) {
        st = gsr_controller_step(&ctrl);
        if (st == GSR_STATUS_OK) {
            PRINTF("  step %2d: mode=%d  G=%lu nS  base=%lu nS  slope=%ld nS/s\n",
                   steps_done,
                   (int)ctrl.mode,
                   ctrl.sample.G_nS,
                   ctrl.sample.baseline_nS,
                   (long)ctrl.sample.slope_nS);
            steps_done++;
        } else if (st == GSR_STATUS_NO_NEW_SAMPLE) {

        } else if (st == GSR_STATUS_MISSED_UPDATE) {
            PRINTF("  WARN: missed update at step %d\n", steps_done);
        } else {
            PRINTF("  FAIL: gsr_controller_step returned %d\n", st);
            return -1;
        }
        if (++attempts > 200000) {
            PRINTF("  FAIL: timed out waiting for controller steps\n");
            return -1;
        }
    }

    PRINTF("GSR controller PASS (%d steps)\n", steps_done);
    return 0;
}

static int test_gsr_latency(void)
{
    gsr_status_t st = gsr_init(VCO_CHANNEL_P, 2, IDAC_DEFAULT_CODE);
    if (st != GSR_STATUS_OK) {
        PRINTF("FAIL: gsr_init returned %d\n", st);
        return -1;
    }

    while (1) {
        uint32_t g_nS = 0;
        uint32_t vin_uV = 0;

        gpio_write(0, 1);
        st = gsr_get_conductance_nS(&g_nS, &vin_uV);
        gpio_write(0, 0);

        if (st == GSR_STATUS_OK) {
            PRINTF("%lu\n", g_nS);
        } else if (st == GSR_STATUS_NO_NEW_SAMPLE) {
        } else if (st == GSR_STATUS_MISSED_UPDATE) {
        } else {
            PRINTF("FAIL: gsr_get_conductance_nS returned %d\n", st);
            return -1;
        }
    }

    return 0;
}

int main(void)
{
    hw_init();

    //PRINTF("=== test_reconstruction ===\n");

    int failures = 0;

    failures += (test_gsr_single()      != 0) ? 1 : 0;
    //failures += (test_gsr_oversampled() != 0) ? 1 : 0;
    // failures += (test_gsr_controller()  != 0) ? 1 : 0;
    //failures += (test_gsr_latency() != 0) ? 1 : 0;

    if (failures == 0) {
        PRINTF("=== ALL TESTS PASSED ===\n");
    } else {
        PRINTF("=== %d TEST(S) FAILED ===\n", failures);
    }

    return failures;
}