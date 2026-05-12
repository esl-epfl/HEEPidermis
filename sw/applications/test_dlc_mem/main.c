// Copyright 2024 EPFL and Politecnico di Torino
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: example_dlc_dma/main.c
// Author: Juan Sapriza
// Date: 12/05/2025
// Description: Example application to test the digital Level Crossing (dLC) IP

#include <stdio.h>
#include <stdlib.h>

#include "dma.h"
#include "core_v_mini_mcu.h"
#include "x-heep.h"
#include "cheep.h"
#include "csr.h"
#include "dlc.h"
#include "rv_plic.h"
#include "test_ecg.h"

#include "hart.h"
#include "timer_sdk.h"

#include "fast_intr_ctrl.h"

#define PRINTF_ENABLE 0

#define DLC_ENABLED 1

#if PRINTF_ENABLE
    #define PRINTF(fmt, ...)    printf(fmt, ## __VA_ARGS__)
#else
    #define PRINTF(...)
#endif

#define FULL_ECG 1

#if FULL_ECG
    #define DATA_LENGTH_B   sizeof(SOURCE_DATA)
#else
    #define DATA_LENGTH_B   256
#endif

#define DATA_CHUNK_W    1
#define DATA_CHUNK_B    1
#define CHUNKS_NW       (DATA_LENGTH_B/(DATA_CHUNK_W*4)) + ((DATA_LENGTH_B%(DATA_CHUNK_W*4))!=0)
#define CHUNKS_NB       (DATA_LENGTH_B/DATA_CHUNK_B)

#define DMA_CSR_REG_MIE_MASK (( 1 << 30 ) |( 1 << 19 ) | (1 << 11 ))

#define SOURCE_DATA ecg_data

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
    int8_t dlc_results[500];

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

    uint32_t* dlc_bypass              = DLC_START_ADDRESS + DLC_BYPASS_REG_OFFSET;

    *dlc_bypass = 0;

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
    *dlc_hysteresis_en = 0;
    // Do not discard any bits from the input signal
    *dlc_discard_bits = 0;

    PRINTF("Set the dLC to: \n\r2sComp:\t%d\n\rLVLw:\t%d bits\n\r",*dlvl_format, *dlvl_log_level_width );

/*############################################################
####### CONFIGURE THE DMA #################################*/

    // Set the source target (where data is taken from)
    tgt_src.ptr = (uint8_t *) SOURCE_DATA;
    // Select the appropriate slot
    tgt_src.trig = DMA_TRIG_MEMORY;
    tgt_src.inc_d1_du = 1;
    // We will copy data in chunks of 32-bits, the width of the ECG data used
    tgt_src.type = DMA_DATA_TYPE_HALF_WORD;

    // After passing through the dLC, the data will be stored in a separate buffer.
    tgt_dst.ptr = (uint8_t *) dlc_results;
    // These data we will store in different places in memory, so the increment should be 1 data unit (du)
    tgt_dst.inc_d1_du = 1;
    // We have nothing to mark the pace for the acquisition, so the slot will be simply the memory grants
    tgt_dst.trig = DMA_TRIG_MEMORY;
    // We will still copy in chuncks of 16-bits for debugging
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
    // Until we have processed enough data
    // We will split the whole data buffer in 4
    *dlc_size = (DATA_LENGTH_B/DMA_DATA_TYPE_2_SIZE(DMA_DATA_TYPE_WORD));
    // *dlc_size = sizeof(dlc_results);

    #if DLC_ENABLED
    // Request an interrupt when the DMA reaches a certain amount of transfers
    // IMPORTANT: the window interrupt always work with the amount of packets written.
    // How many transfers? Depends on what you want... but make sure that the
    // CPU will be able to execute all it's code before the next interrupt
        trans.win_du = 0;
        // Set the size of the transaction. This HAS to be the same value as the dLC will be monitoring.
        // Whether this refers to read or written words, depends on the dlc_rnw variable.
        trans.size_d1_du = *dlc_size;
    #else
        trans.win_du =  0;
        // trans.size_d1_du = *dlc_size;
        trans.size_d1_du = sizeof(dlc_results);
    #endif




    // We do not set an interrupt for the transaction finish, as it would be given by the
    // window interrupt anyways.
    trans.end = DMA_TRANS_END_POLLING;

    // The DMA will restart the same transaction again once it finishes.
    // It will finish when the dLC tells it to do so, because it has already written dlc_size packets.
    trans.mode = DMA_TRANS_MODE_CIRCULAR;

    // Specify that we will use the HW FIFO mode: all data read will be forwarded to the
    // stream peripheral that is connected to the hw fifo.
    trans.hw_fifo_en = DLC_ENABLED;

/*############################################################
####### LOAD THE CONFIGURATION ON THE DMA ###################*/

    // Init the DMA (NULL because we will use the internal dma #0)
    dma_init(NULL);

    // Do some sanity checks to make sure that the entered values are valid
    dma_config_flags_t res;

    /*
    CANNOT VALIDATE THE TRANSACTION, SINCE WE ARE GOING TO USE A CIRCULAR MODE ON THE MEMORY, WHICH IS NOT ACCEPTED.
    res = dma_validate_transaction(&trans, DMA_ENABLE_REALIGN, DMA_PERFORM_CHECKS_INTEGRITY);
    if( res != DMA_CONFIG_OK ){
        PRINTF("Error: dma_validate_transaction: %d\n",res );
        return EXIT_FAILURE;
    }
    */

    // Load the values into the DMA registers.
    res = dma_load_transaction(&trans);
    if( res != DMA_CONFIG_OK ) {
        PRINTF("Error: dma_load_transaction: %d\n", res);
        return EXIT_FAILURE;
    }

    PRINTF("Configured DMA\n\r");

/*############################################################
####### LAUNCH THE DMA #####################################*/

    // Temporarily disable the interrupts so we have time to go to sleep
    CSR_CLEAR_BITS(CSR_REG_MSTATUS, 0x8);

    // Launch the DMA transaction
    if(dma_launch(&trans) != DMA_CONFIG_OK){
        PRINTF("Error: dma_launch\n");
        return EXIT_FAILURE;
    }
    // PRINTF("Launched DMA\n\r");

/*############################################################
####### WAIT FOR THE DMA TO FINISH ########################*/


    CSR_CLEAR_BITS(CSR_REG_MIE, DMA_CSR_REG_MIE_MASK );
    CSR_CLEAR_BITS(CSR_REG_MSTATUS, 0x8);
    CSR_CLEAR_BITS(CSR_REG_MIE, 1<<19 );
    CSR_CLEAR_BITS(CSR_REG_MIE, 1<<30 );
    wait_for_interrupt();


    // // This is an arbitrary number I chose from seeing more or less how many windows will be
    // // triggered during the recording of ECG that we have, considering the transactions finishing.
    // uint8_t windows_to_process = 100;

    // while( window_intr_flag + transactions_intr_flag < windows_to_process ) {
    //     CSR_CLEAR_BITS(CSR_REG_MSTATUS, 0x8);
    //     if ( window_intr_flag + transactions_intr_flag < windows_to_process  ) {
    //             // wait_for_interrupt();
    //             // asm volatile ("nop");
    //     }
    //     // printf("\n%d | %d\n", window_intr_flag, transactions_intr_flag);
    //     CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);
    // }

    // // OPTIONAL: Stop the circular mode
    // // dma_stop_circular(trans.channel);

    // // Celebrate in a fairly lame way
    // PRINTF("DMA done! Did %d windows and %d transactions which finished\n\r", window_intr_flag, transactions_intr_flag);

    // // There is nothing to check, as the results depend on the chosen configuration. Additionally, the circular mode will override whatever we try to check.
    // return EXIT_SUCCESS;


}
