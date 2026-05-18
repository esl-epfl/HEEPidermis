#include "timer_sdk.h"
#include "soc_ctrl.h"
#include "REFs_ctrl.h"
#include "iDAC_ctrl.h"
#include "x-heep.h"

#include "VCO_sdk.h"

#define SYS_FCLK_HZ      10000000U
#define IREF_DEFAULT_CAL 255U
#define IDAC_DEFAULT_CAL 15U
#define VREF_DEFAULT_CAL 0b1111111111U
#define TARGET_SIM       1
#define VCO_ACCEL_RATIO  100U

volatile uint32_t debug __attribute__((section(".xheep_debug_mem")));

void __attribute__((aligned(4), interrupt)) handler_irq_timer(void) {
    vco_handle_timer_irq();
    debug = 'wake';
}

static void hw_init(void) {
    soc_ctrl_t soc_ctrl;
    soc_ctrl.base_addr = mmio_region_from_addr((uintptr_t)SOC_CTRL_START_ADDRESS);
    soc_ctrl_set_frequency(&soc_ctrl, SYS_FCLK_HZ);

    REFs_calibrate(IREF_DEFAULT_CAL, IREF1);
    REFs_calibrate(VREF_DEFAULT_CAL, VREF);

    iDACs_enable(true, false);
    iDAC1_calibrate(IDAC_DEFAULT_CAL);

    enable_timer_interrupt();
    timer_irq_enable();
    timer_cycles_init();
    timer_start();
}

static uint32_t refresh_wait_cycles(uint32_t refresh_rate_Hz) {
#if TARGET_SIM
    return SYS_FCLK_HZ / (VCO_ACCEL_RATIO * refresh_rate_Hz);
#else
    return SYS_FCLK_HZ / refresh_rate_Hz;
#endif
}

static void wait_cycles_busy(uint32_t cycles) {
    uint32_t start = timer_get_cycles();
    while ((uint32_t)(timer_get_cycles() - start) < cycles) {
    }
}

static uint32_t pack_cycles(uint32_t on_cycles, uint32_t off_cycles) {
    return ((on_cycles & 0xFFFFU) << 16) | (off_cycles & 0xFFFFU);
}

int main(void) {
    vco_status_t st;
    uint32_t vin_uV;
    uint32_t refresh_rate_Hz = 10U;
    uint32_t total_cycles;
    uint32_t on_cycles;
    uint32_t off_cycles;
    uint32_t first_tap;
    uint32_t second_tap;
    debug = 'Init';
    hw_init();

    st = vco_initialize(VCO_CHANNEL_P, refresh_rate_Hz);
    if (st != VCO_STATUS_OK) {
        debug = (0xE1U << 24) | (uint32_t)st;
        return -1;
    }
    // wait for the VCO to start oscillating
    // timer_wait_us(57000U); // I don't have to wait more for the VCO to start anymore with the trigger

    total_cycles = refresh_wait_cycles(refresh_rate_Hz);

    /* Low duty cycle: D = 64 (~25%). */
    debug = 'L25';
    st = vco_duty_cycle(VCO_CHANNEL_P, 64U);
    if (st != VCO_STATUS_OK) {
        debug = (0xE2U << 24) | (uint32_t)st;
        return -1;
    }
    on_cycles = (total_cycles * 64U) / 255U;
    off_cycles = total_cycles - on_cycles;
    debug = pack_cycles(on_cycles, off_cycles);
    first_tap = off_cycles - on_cycles/5;
    second_tap = on_cycles;
    wait_cycles_busy(first_tap);
    st = vco_get_Vin_uV(&vin_uV); // at f_s = 10Hz, the issue is that after off_cycles the vco doesn't start counting (even though it wakes up and runs the command to enable ) 
    debug = (0x10U << 24) | (uint32_t)st;
    wait_cycles_busy(second_tap);
    st = vco_get_Vin_uV(&vin_uV);
    if (st != VCO_STATUS_OK) {
        debug = (0xF1U << 24) | (uint32_t)st;
        return -1;
    }
    debug = vin_uV;

    /* Mid duty cycle: D = 128 (~50%). */
    debug = 'M50';
    st = vco_duty_cycle(VCO_CHANNEL_P, 128U);
    if (st != VCO_STATUS_OK) {
        debug = (0xE3U << 24) | (uint32_t)st;
        return -1;
    }
    on_cycles = (total_cycles * 128U) / 255U;
    off_cycles = total_cycles - on_cycles;
    debug = pack_cycles(on_cycles, off_cycles);
    first_tap = off_cycles - on_cycles/5;
    second_tap = on_cycles;
    wait_cycles_busy(first_tap);
    st = vco_get_Vin_uV(&vin_uV);
    debug = (0x20U << 24) | (uint32_t)st;
    wait_cycles_busy(second_tap);
    st = vco_get_Vin_uV(&vin_uV);
    if (st != VCO_STATUS_OK) {
        debug = (0xF2U << 24) | (uint32_t)st;
        return -1;
    }
    debug = vin_uV;

    /* High duty cycle: D = 255 (always ON). */
    debug = 'H10';
    st = vco_duty_cycle(VCO_CHANNEL_P, 255U);
    if (st != VCO_STATUS_OK) {
        debug = (0xE4U << 24) | (uint32_t)st;
        return -1;
    }
    on_cycles = total_cycles;
    off_cycles = 0U;
    debug = pack_cycles(on_cycles, off_cycles);
    st = vco_get_Vin_uV(&vin_uV);
    debug = (0x30U << 24) | (uint32_t)st;
    wait_cycles_busy(total_cycles);
    st = vco_get_Vin_uV(&vin_uV);
    if (st != VCO_STATUS_OK) {
        debug = (0xF3U << 24) | (uint32_t)st;
        return -1;
    }
    debug = vin_uV;

    debug = 'pass';
    return 0;
}
