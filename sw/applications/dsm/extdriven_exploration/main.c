// Copyright EPFL contributors.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include "core_v_mini_mcu.h"
#include "gpio.h"
#include "x-heep.h"
#include "soc_ctrl.h"
#include "SES_filter.h"
#include "pdm2pcm_regs.h"


/*
make board_freq PLL_FREQ=8000000
make jtag_open UART_TERMINAL=gnome UART_BAUD=20480
make jtag_build PROJECT=dsm/extdriven_exploration
make jtag_run
*/

// Uncomment to use SES filter. Comment to use CIC filter.
#define SES
// #define EAP

#ifdef EAP
  #define SYS_FCLK_HZ 8000000 // 8 MHz needed to coordinate with DSM
  #define DSM_F_S_kHz 1000
  #define DSM_CLK_DIV_CC 8
#else
// LFP mode
  // #define SYS_FCLK_HZ 16000000 // watch out!
  // #define DSM_F_S_kHz 50 //watch out!
  // #define DSM_CLK_DIV_CC 320 // watch out!
// EAP mode for testing
  #define SYS_FCLK_HZ 32000000 // watch out!
  #define DSM_F_S_kHz 1000 //watch out!
  #define DSM_CLK_DIV_CC (SYS_FCLK_HZ/DSM_F_S_kHz*1000) // watch out!
#endif

#define DELAY_BETWEEN_RUNS_cc (SYS_FCLK_HZ*1)
#define RUN_LENGHT_N 1025

#define GPIO_LED 0

#ifdef SES
  #define FILTER_NAME "SES"
#else
  #define FILTER_NAME "CIC"
#endif

#define HEADER(g, w, f, a) printf("\n\n%s\tfclk:%d kHz, Wg:%d,Ww:%d,DF:%d,AS:%d",FILTER_NAME, DSM_F_S_kHz,g,w,f,a );


int main(int argc, char *argv[])
{
    static uint32_t output [RUN_LENGHT_N];
    uint32_t        sample_idx  = 0;
    uint32_t        status, fed, read;
    mmio_region_t   pdm2pcm_base_addr = mmio_region_from_addr((uintptr_t)CIC_START_ADDRESS);

    /* ====================================
    CONFIGURE THE GPIOs
    ==================================== */
    gpio_cfg_t pin_led = { .pin = GPIO_LED, .mode = GpioModeOutPushPull };
    if (gpio_config (pin_led) != GpioOk) {
        printf("GPIO initialization failed!\n");
        return 1;
    }

    #ifdef SES
      uint32_t dfs[] = { 1, 16, 32 };  // Decimation factor
      uint32_t wgs[] = { 8, 16 };             // Gain of the first stage
      uint32_t ass[] = { 15, 31 };           // Mask of activated stages
      uint32_t wws[] = { 3, 4, 5 };               // Window lenght (2^x) samples
      #else
      uint32_t dfs[] = { 1, 2, 16, 32 };     // Decimation factor
      uint32_t wgs[] = { DSM_CLK_DIV_CC };   // Clock division (virtual)
      uint32_t ass[] = { 63 };               // Mask of activated stages
      uint32_t wws[] = { 2, 3, 4,5,6 }; // Delay comb
    #endif

    uint32_t sim_len_n = sizeof(wgs)*sizeof(ass)*sizeof(wws)*sizeof(dfs)/256;
    uint32_t sim_run;
    // stop the decimation filters
    SES_set_control_reg(0);
    mmio_region_write32(pdm2pcm_base_addr, PDM2PCM_CONTROL_REG_OFFSET, 0);

    SES_set_gain(1, 0);
    SES_set_gain(2, 0);
    SES_set_gain(3, 0);
    SES_set_gain(4, 0);
    SES_set_gain(5, 0);

    printf("\n\n==== Starting loop for %s (%d, %d, %d)====\n\n", FILTER_NAME, SYS_FCLK_HZ, DSM_CLK_DIV_CC, DSM_F_S_kHz);

    for( uint8_t g=0; g<sizeof(wgs)/4; g++ ){
        for( uint8_t a=0; a<sizeof(ass)/4; a++ ){
            for( uint8_t w=0; w<sizeof(wws)/4; w++ ){
                for( uint8_t f=0; f<sizeof(dfs)/4; f++ ){

                    sim_run = 1+f + w*sizeof(dfs)/4 + a*sizeof(dfs)*sizeof(wws)/16 + g*sizeof(dfs)*sizeof(wws)*sizeof(ass)/64;
                    /* ====================================
                    CONFIGURE THE SES FILTER
                    ==================================== */

                    #ifdef SES
                      SES_set_control_reg(0);                   // stop the decimation filter
                      SES_set_sysclk_division(DSM_CLK_DIV_CC);  // Set the decimator to output a clock at the DSM's sampling frequency
                      SES_set_decim_factor(dfs[f]);             // Set the decimation factor
                      SES_set_activated_stages(ass[a]);         // Set the number of activated stages
                      SES_set_gain(0, wgs[g]);                  // Set gain of the first stage
                      SES_set_window_size(wws[w]);              // Set window size
                    #else
                      mmio_region_write32(pdm2pcm_base_addr, PDM2PCM_CONTROL_REG_OFFSET, 0);                    // stop the decimation filter
                      mmio_region_write32(pdm2pcm_base_addr, PDM2PCM_CLKDIVIDX_REG_OFFSET, wgs[g]);                  // Set the decimator to output a clock at the DSM's sampling frequency
                      mmio_region_write32(pdm2pcm_base_addr, PDM2PCM_DECIMCIC_REG_OFFSET, dfs[f]);              // Set the decimation factor
                      mmio_region_write32(pdm2pcm_base_addr, PDM2PCM_CIC_ACTIVATED_STAGES_REG_OFFSET, ass[a]);  // Set the number of activated stages
                      mmio_region_write32(pdm2pcm_base_addr, PDM2PCM_CIC_DELAY_COMB_REG_OFFSET, wws[w]);        // Delay comb
                    #endif

                    // Indicate the start of a recording using a GPIO
                    gpio_write(GPIO_LED, 1);

                    // START the decimation filter
                    #ifdef SES
                      SES_set_control_reg(1); // START the decimation filter
                      do{ status = SES_get_status(); } while (status != 0b11); // Wait for the filter to be ready
                    #else
                      mmio_region_write32(pdm2pcm_base_addr, PDM2PCM_CONTROL_REG_OFFSET, 1);
                      do{ status = mmio_region_read32(pdm2pcm_base_addr, PDM2PCM_STATUS_REG_OFFSET); } while ( status & 1 ); // Not empty
                    #endif

                    /* ====================================
                    ACQUIRE DATA
                    ==================================== */

                    sample_idx = 0;
                    while(1){
                      #ifdef SES
                        if( SES_get_status() == 3 ){
                            output[sample_idx] = SES_get_filtered_output();
                            if(sample_idx++ == RUN_LENGHT_N){
                                SES_set_control_reg(0);
                                break;
                            }
                        }
                      #else
                        status = mmio_region_read32(pdm2pcm_base_addr, PDM2PCM_STATUS_REG_OFFSET);
                        if (!(status & 1)) {
                            output[sample_idx] = mmio_region_read32(pdm2pcm_base_addr, PDM2PCM_RXDATA_REG_OFFSET);
                            if(sample_idx++ == RUN_LENGHT_N){
                              mmio_region_write32(pdm2pcm_base_addr, PDM2PCM_CONTROL_REG_OFFSET, 0);
                              break;
                            }
                        }
                      #endif
                    }

                    if(0){
                        // Indicate the end of a recording using a GPIO
                        gpio_write(GPIO_LED, 0);

                        HEADER(wgs[g], wws[w], dfs[f], ass[a]);
                        for( sample_idx =0; sample_idx < RUN_LENGHT_N; sample_idx++ ){
                        printf("\n%d\t%d", sample_idx, output[sample_idx]);
                        }
                        printf("\n# %d/%d\n", sim_run, sim_len_n);

                        for (int i = 0 ; i < DELAY_BETWEEN_RUNS_cc ; i++) { asm volatile ("nop");}
                    }
                }
            }
        }
    }

    printf("\n\n==== Loop finished ====\n\n");


    return EXIT_SUCCESS;
}
