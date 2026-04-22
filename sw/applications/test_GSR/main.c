// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: David Mallasen
// Description: Test application for the VCO counter in the VCO decoder

#include "VCO_decoder.h"
#include "timer_sdk.h"
#include "soc_ctrl.h"
#include "REFs_ctrl.h"
#include "iDAC_ctrl.h"
#include "x-heep.h"

#define PRINTF_IN_SIM 0

#define VCO_FS_HZ 10
#define SYS_FCLK_HZ 10000000
#if TARGET_SIM
#define VCO_UPDATE_CC (SYS_FCLK_HZ/(100*VCO_FS_HZ))
#else
#define VCO_UPDATE_CC (SYS_FCLK_HZ/VCO_FS_HZ)
#endif

#define VCO_SUPPLY_VOLTAGE_UV 800000

#define IDAC_DEFAULT_CAL 0
#define IREF_DEFAULT_CAL 255

#define VCO_SUPPLY_FROM_LDO 1
#define VCO_CAL_FROM_LDO_ADD_HZ    20
#define VCO_CAL_FROM_LDO_ADD_uV    5700

#define COMPUTE_AVG         0
#define MOVING_AVG_WINDOW   10

#if TARGET_SIM && PRINTF_IN_SIM
        #define PRINTF(fmt, ...)    printf(fmt, ## __VA_ARGS__)
#elif PRINTF_IN_FPGA && !TARGET_SIM
    #define PRINTF(fmt, ...)    printf(fmt, ## __VA_ARGS__)
#else
    #define PRINTF(...)
#endif

uint32_t window[MOVING_AVG_WINDOW];

#define TABLE_SIZE 25
uint32_t table_Vin_uV[TABLE_SIZE] ={340000, 360000, 380000, 400000, 420000, 440000, 460000, 480000, 500000, 520000, 540000, 560000, 580000, 600000, 620000, 640000, 660000, 680000, 700000, 720000, 740000, 760000, 780000, 800000, 820000};
uint32_t table_fosc_Hz[TABLE_SIZE] = {26130, 31330, 37320, 45270, 55150, 67270, 82680, 99870, 121190, 146020, 175270, 208990, 247770, 291780, 341260, 396650, 457900, 525140, 598560, 677660, 762750, 853760, 950200, 1051710, 1158000};
void __attribute__((aligned(4), interrupt)) handler_irq_timer(void) {
    timer_arm_stop();
    timer_irq_clear();
    return;
}


uint32_t compute_freq_Hz( diff ){
    uint32_t freq_Hz;
    freq_Hz = diff*VCO_FS_HZ;
    #if VCO_SUPPLY_FROM_LDO
        freq_Hz += VCO_CAL_FROM_LDO_ADD_HZ;
    #endif
    return freq_Hz;
}

uint32_t interpolate_Vin_uV(uint32_t f_target) {
    // 1. Handle Out-of-Bounds
    if (f_target <= table_fosc_Hz[0]) return table_Vin_uV[0];
    if (f_target >= table_fosc_Hz[TABLE_SIZE - 1]) return table_Vin_uV[TABLE_SIZE - 1];

    // 2. Binary Search to find the interval [low, high]
    int low = 0;
    int high = TABLE_SIZE - 1;
    while (low <= high) {
        int mid = low + (high - low) / 2;
        if (table_fosc_Hz[mid] < f_target) {
            low = mid + 1;
        } else {
            high = mid - 1;
        }
    }

    // After search, table_fosc_Hz[high] < f_target < table_fosc_Hz[low]
    uint32_t f0 = table_fosc_Hz[high];
    uint32_t f1 = table_fosc_Hz[low];
    uint32_t v0 = table_Vin_uV[high];
    uint32_t v1 = table_Vin_uV[low];

    // 3. Linear Interpolation Formula
    // V = v0 + (f_target - f0) * (v1 - v0) / (f1 - f0)

    uint32_t delta_f_target = f_target - f0;
    uint32_t delta_v_table = v1 - v0;
    uint32_t delta_f_table = f1 - f0;

    // We multiply before dividing to keep precision.
    // Result fits in uint32_t because 20,000 * ~106,000 < 2^32
    uint32_t result_uV = v0 + ((delta_f_target * delta_v_table) / delta_f_table);
    #if VCO_SUPPLY_FROM_LDO
        result_uV += VCO_CAL_FROM_LDO_ADD_uV;
    #endif
}

uint32_t update_dac1(val){
    uint32_t current_nA = 40*val;
    iDACs_set_currents( val, 0);
    PRINTF("Injecting %d.%d uA (code %d)\n", current_nA/1000, current_nA%1000,val);
    return current_nA;
}

uint32_t compute_res_kO( iin_nA, vin_uV ){
    return (VCO_SUPPLY_VOLTAGE_UV - vin_uV)/iin_nA;
}

int main() {
    uint32_t i=0;
    uint32_t count, last_count, diff, last_diff, avg, sum, dist, var = 0;
    uint32_t freq_Hz, vin_uV, iin_nA, res_kO;
    uint8_t idac_val = 0;

    #if TARGET_SIM
    uint32_t loop_count = 0;
    #endif

    soc_ctrl_t soc_ctrl;
    soc_ctrl.base_addr = mmio_region_from_addr((uintptr_t)SOC_CTRL_START_ADDRESS);
    soc_ctrl_set_frequency(&soc_ctrl,SYS_FCLK_HZ);

    timer_cycles_init();         // Init the timer SDK for clock cycles

    // Enable the VCOp and VCOn
    VCOp_enable(true);

    // Set the VCO refresh rate to 1000 cycles
    VCO_set_refresh_rate(VCO_UPDATE_CC);

    REFs_calibrate( IREF_DEFAULT_CAL, IREF1 );
    REFs_calibrate( 0b1111111111, VREF );

    iDACs_enable(true, false);
    iDAC1_calibrate(IDAC_DEFAULT_CAL);
    idac_val = 50;
    iin_nA = update_dac1(idac_val);

    PRINTF("=== Test VCO overflow ===\n");

    enable_timer_interrupt();   // Enable the timer machine-level interrupt
    timer_irq_enable();
    timer_cycles_init();
    timer_start();

    while(1){
        count = VCOp_get_coarse();
        diff =  count - last_count;
        // Ignore samples that, because of the mismatch between the sampling and refresh frequency, should be discarded
        if(  diff < (15*last_diff)/10 && diff > (5*last_diff)/10 ){

            freq_Hz = compute_freq_Hz(diff);
            vin_uV  = interpolate_Vin_uV( freq_Hz );
            res_kO  = compute_res_kO( iin_nA, vin_uV );


            PRINTF("\n%d: %d Hz\t| %d uV | %d kΩ", i, freq_Hz, vin_uV, res_kO);

            i++;
        }else{
            PRINTF("\nSkipped");
        }

        last_count = count;
        last_diff = diff;
        timer_cycles_init();
        timer_irq_enable();
        timer_arm_start(VCO_UPDATE_CC);
        asm volatile ("wfi");
        timer_irq_clear();

        #if TARGET_SIM
        if(loop_count++ >= 500) break;
        #endif
    }

    return 0;
}