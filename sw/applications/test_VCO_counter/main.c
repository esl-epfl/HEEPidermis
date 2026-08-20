// Copyright 2025 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// Author: David Mallasen
// Description: Test application for the VCO counter in the VCO decoder

#include "VCO_decoder.h"
#include "timer_sdk.h"
#include "soc_ctrl.h"

#define VCO_FS_HZ 1
#define SYS_FCLK_HZ 1000000
#define VCO_UPDATE_CC (SYS_FCLK_HZ/VCO_FS_HZ)

#define VCO_SUPPLY_FROM_LDO 1
#define VCO_CAL_FROM_LDO_ADD_HZ    20
#define VCO_CAL_FROM_LDO_ADD_uV    5700

#define COMPUTE_AVG         0
#define MOVING_AVG_WINDOW   10

#define PSEUDO_DIFF_MODE    1

uint32_t window[MOVING_AVG_WINDOW];

#define TABLE_SIZE 25
uint32_t table_Vin_uV[TABLE_SIZE] ={340000, 360000, 380000, 400000, 420000, 440000, 460000, 480000, 500000, 520000, 540000, 560000, 580000, 600000, 620000, 640000, 660000, 680000, 700000, 720000, 740000, 760000, 780000, 800000, 820000};
uint32_t table_fosc_Hz[TABLE_SIZE] = {26130, 31330, 37320, 45270, 55150, 67270, 82680, 99870, 121190, 146020, 175270, 208990, 247770, 291780, 341260, 396650, 457900, 525140, 598560, 677660, 762750, 853760, 950200, 1051710, 1158000};
void __attribute__((aligned(4), interrupt)) handler_irq_timer(void) {
    timer_arm_stop();
    timer_irq_clear();
    return;
}


uint32_t compute_freq_Hz( diff_p ){
    uint32_t freq_p_Hz;
    freq_p_Hz = diff_p*VCO_FS_HZ;
    #if VCO_SUPPLY_FROM_LDO
        freq_p_Hz += VCO_CAL_FROM_LDO_ADD_HZ;
    #endif
    return freq_p_Hz;
}

// uint32_t interpolate_Vin_uV(uint32_t f_target) {
//     // 1. Handle Out-of-Bounds
//     if (f_target <= table_fosc_Hz[0]) return table_Vin_uV[0];
//     if (f_target >= table_fosc_Hz[TABLE_SIZE - 1]) return table_Vin_uV[TABLE_SIZE - 1];

//     // 2. Binary Search to find the interval [low, high]
//     int low = 0;
//     int high = TABLE_SIZE - 1;
//     while (low <= high) {
//         int mid = low + (high - low) / 2;
//         if (table_fosc_Hz[mid] < f_target) {
//             low = mid + 1;
//         } else {
//             high = mid - 1;
//         }
//     }

//     // After search, table_fosc_Hz[high] < f_target < table_fosc_Hz[low]
//     uint32_t f0 = table_fosc_Hz[high];
//     uint32_t f1 = table_fosc_Hz[low];
//     uint32_t v0 = table_Vin_uV[high];
//     uint32_t v1 = table_Vin_uV[low];

//     // 3. Linear Interpolation Formula
//     // V = v0 + (f_target - f0) * (v1 - v0) / (f1 - f0)

//     uint32_t delta_f_target = f_target - f0;
//     uint32_t delta_v_table = v1 - v0;
//     uint32_t delta_f_table = f1 - f0;

//     // We multiply before dividing to keep precision.
//     // Result fits in uint32_t because 20,000 * ~106,000 < 2^32
//     uint32_t result_uV = v0 + ((delta_f_target * delta_v_table) / delta_f_table);
//     #if VCO_SUPPLY_FROM_LDO
//         result_uV += VCO_CAL_FROM_LDO_ADD_uV;
//     #endif

//     return result_uV;
// }

int main() {

    soc_ctrl_t soc_ctrl;
    soc_ctrl.base_addr = mmio_region_from_addr((uintptr_t)SOC_CTRL_START_ADDRESS);
    soc_ctrl_set_frequency(&soc_ctrl,SYS_FCLK_HZ);

    timer_cycles_init();         // Init the timer SDK for clock cycles

    // Enable the VCOp (and VCOn)
    VCOp_enable(true);
    #if PSEUDO_DIFF_MODE
    VCOn_enable(true);
    #endif

    // Set the VCO refresh rate to 1000 cycles
    VCO_set_refresh_rate(VCO_UPDATE_CC);

    uint32_t i=0;
    uint32_t coarse_p, last_coarse_p, diff_p, last_diff_p = 0;
    uint32_t diff, avg, sum, dist, var, count = 0;
    uint32_t freq_p_Hz, vin_p_uV;
    #if PSEUDO_DIFF_MODE
    uint32_t coarse_n, last_coarse_n, diff_n, last_diff_n = 0;
    uint32_t freq_n_Hz, vin_n_uV = 0;
    uint32_t vin_d_uV = 0;
    #endif


    VCO_set_counter_limit(VCO_UPDATE_CC);


    enable_timer_interrupt();   // Enable the timer machine-level interrupt
    timer_irq_enable();

    printf("=== Test VCO counter ===\n");

    timer_cycles_init();
    timer_start();

    while(1){
        coarse_p    = VCOp_get_coarse();
        diff_p      =  coarse_p - last_coarse_p;
        #if PSEUDO_DIFF_MODE
        coarse_n    = VCOn_get_coarse();
        diff_n      =  coarse_n - last_coarse_n;
        count       = VCO_get_count();

        diff        = diff_n - diff_p;
        #endif

        if(  diff_p < (15*last_diff_p)/10 && diff_p > (5*last_diff_p)/10 ){
            #if COMPUTE_AVG
                window[i%MOVING_AVG_WINDOW] = diff_p;
                sum = 0;
                for(int j=0; j<MOVING_AVG_WINDOW; j++){
                    sum += window[j];
                    dist = window[j] - avg;
                    var += dist*dist;
                }
                avg = sum/MOVING_AVG_WINDOW;
                var /= MOVING_AVG_WINDOW;

                printf("\n%d:\t%d.%d kHz\t(µ:%d.%d, σ²:%d)", i, diff_p/1000, diff_p%1000, avg/1000, avg%1000, var);
            #endif

            freq_p_Hz = compute_freq_Hz(diff_p);
            vin_p_uV  = interpolate_Vin_uV( freq_p_Hz );
            printf("\n%d:\t%d\tHz |\t%d\tuV", i, freq_p_Hz, vin_p_uV);

            #if PSEUDO_DIFF_MODE
            freq_n_Hz = compute_freq_Hz(diff_n);
            vin_n_uV  = interpolate_Vin_uV( freq_n_Hz );
            vin_d_uV  = vin_n_uV - vin_p_uV;
            printf("|\t%d:\t%d\tHz |\t%d\tuV =\t%d\tuV", i, freq_n_Hz, vin_n_uV,vin_d_uV);

            #endif


            i++;
        }else{
            printf("\nSkipped");
        }

        last_coarse_p   = coarse_p;
        last_diff_p     = diff_p;
        #if PSEUDO_DIFF_MODE
        last_coarse_n   = coarse_n;
        last_diff_n     = diff_n;
        #endif

        timer_cycles_init();
        timer_irq_enable();
        timer_arm_start(VCO_UPDATE_CC); // 50 cycles for taking into account initialization
        asm volatile ("wfi");
        timer_irq_clear();

    }

    return 0;
}