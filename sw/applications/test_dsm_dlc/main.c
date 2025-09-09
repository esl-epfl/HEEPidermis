// Copyright 2024 EPFL and Politecnico di Torino
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: example_dsm_dlc.c
// Author: Juan Sapriza & Jérémie Moullet
// Date: 06.2025
// Description: Example application to test the digital Level Crossing (dLC) IP
//              along with the DSM block, and DMA Hardware Fifo Mode.

#include <stdio.h>
#include <stdlib.h>

#include "core_v_mini_mcu.h"
#include "x-heep.h"
#include "cheep.h"
#include "dma.h"
#include "dlc.h"
#include "csr.h"
#include "rv_plic.h"
#include "hart.h"
#include "timer_sdk.h"
#include "fast_intr_ctrl.h"

#include "SES_filter_regs.h"
#include "SES_filter.h"

#include "pdm2pcm_regs.h"

#include "util.h"

//Do not change the following defines, check in util.h
#if TARGET_SIM && PRINTF_IN_SIM
        #define PRINTF(fmt, ...)    printf(fmt, ## __VA_ARGS__)
#elif PRINTF_IN_FPGA && !TARGET_SIM
    #define PRINTF(fmt, ...)    printf(fmt, ## __VA_ARGS__)
#else
    #define PRINTF(...)
#endif

#ifdef FULL_TEST
    #define DATA_LENGTH_B   NUMBER_OUTPUT
#else
    #define DATA_LENGTH_B   256
#endif

#define DMA_CSR_REG_MIE_MASK (( 1 << 30 ) |( 1 << 19 ) | (1 << 11 ))

uint8_t src_slot = DMA_TRIG_SLOT_EXT_RX;

dma_target_t tgt_src;
dma_target_t tgt_dst;
dma_trans_t trans;

volatile int32_t window_intr_flag = 0;
volatile int32_t transactions_intr_flag = 0;


void dma_intr_handler_window_done(uint8_t channel){
    window_intr_flag ++;
}

void dma_intr_handler_trans_done(uint8_t channel){
    transactions_intr_flag ++;
}


// The DMA transaction validation checks that the window is not too small. If it
// is too small it will assume you are not going to be able to attend the interrupt
// before the next interrupt. Because our interrupts will be very sparse, we override
// this check.
uint8_t dma_window_ratio_warning_threshold(){
    return 0;
}

void __attribute__((aligned(4), interrupt)) handler_irq_timer(void) {
    timer_arm_stop();
    timer_irq_clear();
    return;
}

int main() {
    CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);
    CSR_SET_BITS(CSR_REG_MIE, DMA_CSR_REG_MIE_MASK );

    // dLC results buffer
    int16_t dlc_results[NUMBER_OUTPUT/10]; //The 10 is an estimation based on test data, can be adapted

/*############################################################
####### SET THE DIGITAL LC POINTERS #######################*/

    // dLC programming registers
    uint32_t* dlvl_log_level_width    = DLC_START_ADDRESS + DLC_DLVL_LOG_LEVEL_WIDTH_REG_OFFSET;
    uint32_t* dlvl_n_bits             = DLC_START_ADDRESS + DLC_DLVL_N_BITS_REG_OFFSET;
    uint32_t* dlvl_format             = DLC_START_ADDRESS + DLC_DLVL_FORMAT_REG_OFFSET;
    uint32_t* dlvl_mask               = DLC_START_ADDRESS + DLC_DLVL_MASK_REG_OFFSET;
    uint32_t* dt_mask                 = DLC_START_ADDRESS + DLC_DT_MASK_REG_OFFSET;
    uint32_t* dlc_size                = DLC_START_ADDRESS + DLC_TRANS_SIZE_REG_OFFSET;
    uint32_t* dlc_hysteresis_en       = DLC_START_ADDRESS + DLC_HYSTERESIS_EN_REG_OFFSET;
    uint32_t* dlc_discard_bits        = DLC_START_ADDRESS + DLC_DISCARD_BITS_REG_OFFSET;

/*############################################################
####### SET THE DIGITAL LC PARAMETERS ######################*/

    // dLC programming
    // dlvl_format: if set to '1' the result data for delta-levels are in two's complement format
    //              if set to '0' the result data for delta-levels are in sign and modulo format
    *dlvl_format = LC_PARAMS_DATA_IN_TWOS_COMPLEMENT;
    // dlvl_log_level_width: log2 of the delta-levels width
    *dlvl_log_level_width = LC_PARAMS_LC_LEVEL_WIDTH_BY_BITS;
    // dlvl_n_bits: number of bits for the delta-levels field
    //              if dlvl_format is set to '1' the number of bits for the delta-levels is dlvl_n_bits
    //              if dlvl_format is set to '0' the number of bits for the delta-levels is dlvl_n_bits - 1 to account for the sign bit
    *dlvl_n_bits = (LC_PARAMS_DATA_IN_TWOS_COMPLEMENT) ? LC_PARAMS_LC_ACQUISITION_WORD_SIZE_OF_AMPLITUDE:
                        LC_PARAMS_LC_ACQUISITION_WORD_SIZE_OF_AMPLITUDE - 1;
    // dlvl_mask: mask for the delta-levels field (it has as many bits set to 1 as the number of bits for the delta-levels field)
    *dlvl_mask = (1 << (*dlvl_n_bits)) - 1;
    // dt_mask: mask for the delta-time field (it has as many bits set to 1 as the number of bits for the delta-time field)
    *dt_mask = (1 << (LC_PARAMS_LC_ACQUISITION_WORD_SIZE_OF_TIME)) - 1;
    // Enable a 1-level hsytersis to avoid excessive crossings
    *dlc_hysteresis_en = LC_PARAMS_LC_HYSTERESIS_ENABLE;
    // Do not discard any bits from the input signal
    *dlc_discard_bits = LC_PARAMS_LC_DISCARD_BITS;

    PRINTF("Set the dLC to: \n\r2sComp:\t%d\n\rLVLw:\t%d bits\n\r",*dlvl_format, *dlvl_log_level_width );

/*############################################################
####### CONFIGURE THE DMA ####################################*/

    // Set the source target (where data is taken from) to the Rx fifo of the SPI
#ifdef USE_SES_NOT_CIC
    tgt_src.ptr = (uint8_t *) (uint32_t *)(SES_FILTER_START_ADDRESS + SES_FILTER_RX_DATA_REG_OFFSET);
#else
    tgt_src.ptr = (uint8_t *) (uint32_t *)(CIC_START_ADDRESS + PDM2PCM_RXDATA_REG_OFFSET);
#endif
    // Select the appropriate slot
    tgt_src.trig = src_slot;
    // Because the data is always taken from the same register, there should be no increment
    tgt_src.inc_d1_du = 0;
    // We will copy data in chunks of 32-bits, the width of the output of the DMS block
    tgt_src.type = DMA_DATA_TYPE_WORD;

    // After passing through the dLC, the data will be stored in a separate buffer.
    tgt_dst.ptr = (uint8_t *) dlc_results;
    // These data we will store in different places in memory, so the increment should be 1 data unit (du)
    tgt_dst.inc_d1_du = 1;
    // We have nothing to mark the pace for the acquisition, so the slot will be simply the memory grants
    tgt_dst.trig = DMA_TRIG_MEMORY;
    // We will copy in chuncks of 8-bits as it is the size of LC_PARAMS_LC_ACQUISITION_WORD_SIZE_OF_AMPLITUDE + LC_PARAMS_LC_ACQUISITION_WORD_SIZE_OF_TIME
    tgt_dst.type = DMA_DATA_TYPE_BYTE;

    // Set the transaction
    trans.src        = &tgt_src;
    trans.dst        = &tgt_dst;
    // Set that this will be a 1-Dimensional data transfer
    trans.dim        = DMA_DIM_CONF_1D;


    /*############################################################
    ####### CONFIGURE THE WINDOW INTERRUPT ######################*/

    // Prepare the window interrupt

    window_intr_flag = 0;
    transactions_intr_flag = 0;

    // The dLC will the one monitoring the end of the transactions.
    // We want to restart the DMA transaction every time the DMA has read the whole buffer, so that it can send it again
    // Until we have processed enough data. We will split the whole data buffer in 4
    *dlc_size = (DATA_LENGTH_B/DMA_DATA_TYPE_2_SIZE(DMA_DATA_TYPE_WORD)) / 4;

    // Request an interrupt when the DMA reaches a certain amount of transfers
    // IMPORTANT: the window interrupt always work with the amount of packets written.
    // How many transfers? Depends on what you want... but make sure that the
    // CPU will be able to execute all it's code before the next interrupt
    trans.win_du = 50;

    // Set the size of the transaction. This HAS to be the same value as the dLC will be monitoring.
    // Whether this refers to read or written words, depends on the dlc_rnw variable.
    trans.size_d1_du = *dlc_size;


    // We do not set an interrupt for the transaction finish, as it would be given by the
    // window interrupt anyways.
    trans.end = DMA_TRANS_END_INTR;

    // The DMA will restart the same transaction again once it finishes.
    // It will finish when the dLC tells it to do so, because it has already written dlc_size packets.
    trans.mode = DMA_TRANS_MODE_CIRCULAR;

    // Specify that we will use the HW FIFO mode: all data read will be forwarded to the
    // stream peripheral that is connected to the hw fifo.
    trans.hw_fifo_en = true;

/*############################################################
####### LOAD THE CONFIGURATION ON THE DMA ###################*/

    // Init the DMA (NULL because we will use the internal dma #0)
    dma_init(NULL);

    // Do some sanity checks to make sure that the entered values are valid
    dma_config_flags_t res;
    res = dma_validate_transaction(&trans, DMA_ENABLE_REALIGN, DMA_PERFORM_CHECKS_INTEGRITY);
    if( res != DMA_CONFIG_OK ){
        PRINTF("Error: dma_validate_transaction: %d\n",res );
        return EXIT_FAILURE;
    }
    // Load the values into the DMA registers.
    res = dma_load_transaction(&trans);
    if( res != DMA_CONFIG_OK ) {
        PRINTF("Error: dma_load_transaction: %d\n", res);
        return EXIT_FAILURE;
    }

    PRINTF("Configured DMA\n\r");

/*############################################################
####### LAUNCH THE DMA #####################################*/

    // Launch the DMA transaction. As the DMA will be waiting at the DSM trigger notification, no transaction will be done yet
    if(dma_launch(&trans) != DMA_CONFIG_OK){
        PRINTF("Error: dma_launch\n");
        return EXIT_FAILURE;
    }
    PRINTF("Launched DMA\n\r");

    #if !TARGET_SIM
    // Enable the timer interrupts to go to sleep between packets.
    enable_timer_interrupt();
    // Wait for a while just for the lols
    timer_wait_us(1000000);
    #endif

/*############################################################
####### CONFIGURE THE FILTERS ##########################*/ 
#ifdef USE_SES_NOT_CIC
    // Set SES filter parameters
    SES_set_window_size(SES_WINDOW_SIZE);
    SES_set_decim_factor(SES_DECIM_FACTOR);
    SES_set_sysclk_division(SES_SYSCLK_DIVISION);
    SES_set_activated_stages(SES_ACTIVATED_STAGES);

    SES_set_gain(0, SES_GAIN_STAGE_0);
    SES_set_gain(1, SES_GAIN_STAGE_1);
    SES_set_gain(2, SES_GAIN_STAGE_2);
    SES_set_gain(3, SES_GAIN_STAGE_3);
    SES_set_gain(4, SES_GAIN_STAGE_4);
    SES_set_gain(5, SES_GAIN_STAGE_5);

    // Start the SES filter
    SES_set_control_reg(true);
    PRINTF("SES filter started \n\r");

#else
    mmio_region_t pdm2pcm_base_addr = mmio_region_from_addr((uintptr_t)CIC_START_ADDRESS);

    mmio_region_write32(pdm2pcm_base_addr, PDM2PCM_CLKDIVIDX_REG_OFFSET, CIC_SYSCLK_DIVISION);
    mmio_region_write32(pdm2pcm_base_addr, PDM2PCM_DECIMCIC_REG_OFFSET, CIC_DECIM_FACTOR);
    mmio_region_write32(pdm2pcm_base_addr, PDM2PCM_CIC_ACTIVATED_STAGES_REG_OFFSET, CIC_ACTIVATED_STAGES);
    mmio_region_write32(pdm2pcm_base_addr, PDM2PCM_CIC_DELAY_COMB_REG_OFFSET, CIC_DELAY_COMB);

    // Start the CIC filter
    mmio_region_write32(pdm2pcm_base_addr, PDM2PCM_CONTROL_REG_OFFSET  , 1);
    PRINTF("CIC filter started \n\r");
#endif

/*############################################################
####### WAIT FOR THE DMA TO FINISH ########################*/

    // This is an arbitrary number I chose from seeing more or less how many windows will be
    // triggered during the reading of the sample data, considering the transactions finishing.
    uint8_t windows_to_process = 5;

    while( window_intr_flag + transactions_intr_flag < windows_to_process ) {
        CSR_CLEAR_BITS(CSR_REG_MSTATUS, 0x8);
        if ( window_intr_flag + transactions_intr_flag < windows_to_process  ) {
                wait_for_interrupt();
        }
        CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);
    }

    // Celebrate in a fairly lame way
    PRINTF("DMA done! Did %d windows and %d transactions which finished\n\r", window_intr_flag, transactions_intr_flag);

/*############################################################
####### CHECK THE RESULTS ###################################*/

    // Checking  the results
    PRINTF("\n\rRES\t| dLC\t| Golden");
    uint16_t errors = 0;
#ifdef USE_SES_NOT_CIC
    for (int i = 0; i < goldenTruthSizeSES; i++)
    {
        if(dlc_results[i] != goldenTruthSES[i])
        {
            PRINTF("\n\rX %d\t| %d\t| %d", i, dlc_results[i], goldenTruthSES[i]);
            errors++;
        }
    }
#else
    for (int i = 0; i < goldenTruthSizeCIC; i++)
    {
        if(dlc_results[i] != goldenTruthCIC[i])
        {
            PRINTF("\n\rX %d\t| %d\t| %d", i, dlc_results[i], goldenTruthCIC[i]);
            errors++;
        }
    }
#endif
    if( errors ){
        PRINTF("\n\r=====================\n ERRORS: %d\n", errors);
        return EXIT_FAILURE;
    } else {
        PRINTF("\n\r ALL GOOD!\n");
        return EXIT_SUCCESS;
    }
}
