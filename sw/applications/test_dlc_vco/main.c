// Copyright 2024 EPFL and Politecnico di Torino
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: example_dlc_vco/main.c
// Author: Juan Sapriza
// Date: 14/05/2025
// Description: Example application to test the digital Level Crossing (dLC) IP
//              along while the DMA reads from the iDAC.

#include <stdio.h>
#include <stdlib.h>

#include "dma.h"
#include "core_v_mini_mcu.h"
#include "x-heep.h"
#include "cheep.h"
#include "csr.h"
#include "rv_plic.h"

#include "hart.h"
#include "timer_sdk.h"

#include "fast_intr_ctrl.h"

#include "dlc.h"
#include "test_sine.h"
#include "VCO_decoder.h"
#include "iDAC_ctrl.h"

#define PRINTF_IN_SIM 0
#define PRINTF_IN_FPGA 1

#if TARGET_SIM && PRINTF_IN_SIM
        #define PRINTF(fmt, ...)    printf(fmt, ## __VA_ARGS__)
#elif PRINTF_IN_FPGA && !TARGET_SIM
    #define PRINTF(fmt, ...)    printf(fmt, ## __VA_ARGS__)
#else
    #define PRINTF(...)
#endif


#define LC_PARAMS_LC_LSBS_TO_USE_AS_HYSTERESIS 0
#define LC_PARAMS_LC_LEVEL_WIDTH_BY_BITS 7
#define LC_PARAMS_DATA_IN_TWOS_COMPLEMENT 0
#define LC_PARAMS_LC_ACQUISITION_WORD_SIZE_OF_AMPLITUDE 2
#define LC_PARAMS_LC_ACQUISITION_WORD_SIZE_OF_TIME 6
#define LC_PARAMS_LC_LEVEL_WIDTH_BY_FRACTION 256
#define LC_PARAMS_SIZE_PER_SAMPLE_BITS 8
#define LC_STATS_CROSSINGS sizeof(lc_data_for_storage_data)
#define LC_STATS_D_LVL_OVERFLOW_WORDS 49
#define LC_STATS_D_T_OVERFLOW_WORDS 14
#define FORM_STATS_WORD_SIZE_BITS 8

#define INTR_TIMER (1 << 7)
#define INTR_DMA_TRANS_DONE (1 << 19)
#define INTR_DMA_WINDOW_DONE (1 << 30)
#define INTR_EXTERNAL (1<<31)

#define SOURCE_DATA test_sines
#define DATA_LENGTH_B   sizeof(SOURCE_DATA)

#define ADC_DMA 0
#define DAC_DMA 1

dma_target_t adc_tgt_src;
dma_target_t adc_tgt_dst;
dma_trans_t adc_trans;

dma_target_t dac_tgt_src;
dma_target_t dac_tgt_dst;
dma_trans_t dac_trans;

int32_t window_intr_flag = 0;
int32_t transactions_intr_flag = 0;
volatile int32_t xing_intr_flag = 0;

// dLC results buffer
int16_t dlc_results[500];

volatile uint32_t debug __attribute__((section(".xheep_debug_mem")));

void dma_intr_handler_window_done(uint8_t channel){
     if(channel == ADC_DMA ) window_intr_flag ++;
}

void dma_intr_handler_trans_done(uint8_t channel){
    if(channel == ADC_DMA ) transactions_intr_flag ++;
}

void fic_irq_ext_peripheral(){
    xing_intr_flag++;
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

    debug = 'Init';

/*############################################################
################## CONFIGURE THE IDACs ######################*/

    // We enable the two iDACs, each will receive a different signal from the
    // DMA, but will be updated simultaneously.
    iDACs_enable(true, true);
    // Set the calibration values
    iDAC1_calibrate(16);
    iDAC2_calibrate(16);

    // We will be injecting sines with DATA_LENGTH_B/2 samples.
    // The refresh rate we set here will determine the frequency of the sines.
    // Make sure that this is at least double the sampling rate of the ADCs (i dunno, ask Nyquist)
    // Let's say you want 20 ADC samples per period, and that one period has 100 DAC samples,
    // then you want the DAC to be 5 times faster than the ADC, so you set the
    // refresh rate 5 times lower.
    uint16_t dac_refresh_rate_cc = 200;
    iDACs_set_refresh_rate(dac_refresh_rate_cc);
    debug = 'dac';
    debug = (dac_refresh_rate_cc<<16) & 'cc';

/*############################################################
####### CONFIGURE THE iDACs' DMA (1)  ##########################*/

    // Init the DMA (NULL because we will use the internal dma #0)
    // Both DMAs are initialized
    dma_init(NULL);

    // Set the source target (where data is taken from) to the array with the stimulation data
    dac_tgt_src.ptr = (uint8_t*) SOURCE_DATA;

    debug = '1dma';
    debug = dac_tgt_src.ptr;

    // From the memory side there is no restriction on how to fethc data
    dac_tgt_src.trig = DMA_TRIG_MEMORY;
    // These data we will taken from different places in memory, so the increment should be 1 data unit (du)
    dac_tgt_src.inc_d1_du = 1;
    // We will copy data in chunks of 16 bits.
    // Each iDAC takes 8 bits as data, but both their registers are together, with iDAC 0 taking the 8 LSBs and
    // iDAC1 taking the 8 MSBs.
    // Our DMA will take the 16 bit word containing both pieces of data and store them in the common register.
    dac_tgt_src.type = DMA_DATA_TYPE_HALF_WORD;

    // After passing through the dLC, the data will be stored in a separate buffer.
    dac_tgt_dst.ptr = (uint8_t *)(IDAC_CTRL_START_ADDRESS + IDAC_CTRL_CURRENT_REG_OFFSET);
    // Because the data is always stored in the same register, there should be no increment
    dac_tgt_dst.inc_d1_du = 0;
    // Select the appropriate slot -- the iDACs controller is connected to the Tx external trigger
    dac_tgt_dst.trig = DMA_TRIG_SLOT_EXT_TX;
    // We copy the 16-bits containing both iDAC's values
    dac_tgt_dst.type = DMA_DATA_TYPE_HALF_WORD;

    // Set the transaction
    dac_trans.src   = &dac_tgt_src;
    dac_trans.dst   = &dac_tgt_dst;
    // Set that this will be a 1-Dimensional data transfer
    dac_trans.dim   = DMA_DIM_CONF_1D;

    // We will use the DMA channel 1 as th DAC DMA. This could not be in any other way as the
    // transmission interrupts are connected to channel 1's interrupt
    dac_trans.channel = DAC_DMA;

    // No window or transaction end interrupts
    dac_trans.win_du = 0;
    dac_trans.end = DMA_TRANS_END_POLLING;

    // Set the size of the transaction. This should be the size of a period of the signal
    dac_trans.size_d1_du = DATA_LENGTH_B/DMA_DATA_TYPE_2_SIZE(dac_tgt_dst.type);

    // The DMA will restart the same transaction again once it finishes.
    // I will forever be injecting the two sines until it is stopped.
    dac_trans.mode = DMA_TRANS_MODE_CIRCULAR;

    // Specify that we will use the HW FIFO mode: all data read will be forwarded to the
    // stream peripheral that is connected to the hw fifo.
    dac_trans.hw_fifo_en = false;

    debug = 'dma1';

/*############################################################
################## ENABLE DMA INTERRUPTS ######################*/

CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);
CSR_SET_BITS(CSR_REG_MIE, INTR_TIMER | INTR_DMA_TRANS_DONE | INTR_DMA_WINDOW_DONE );


/*############################################################
############## LOAD AND LAUNCH THE DACs' DMA (1)  ############*/

    debug = '>dac';

    dma_config_flags_t res;
    res = dma_validate_transaction(&dac_trans, DMA_ENABLE_REALIGN, DMA_PERFORM_CHECKS_INTEGRITY);
    if( res != DMA_CONFIG_OK ){
        debug = res | (1<<24);
        PRINTF("Error: dma_validate_transaction: %d\n",res );
        return EXIT_FAILURE;
    }

    debug = 'val';

    // Load the values into the DMA registers.
    res = dma_load_transaction(&dac_trans);
    if( res != DMA_CONFIG_OK ) {
        debug = res | (2<<24);;
        PRINTF("Error: dma_load_transaction: %d\n", res);
        return EXIT_FAILURE;
    }

    if(dma_launch(&dac_trans) != DMA_CONFIG_OK){
        PRINTF("Error: Failed to launch the DAC DMA\n");
        return EXIT_FAILURE;
    }

    debug = 'dac>';

/*############################################################
################### CONFIGURE THE VCOs ######################*/

    // Enable both VCO-ADCs. This will mean that their outputs
    // will be subtracted, obtaining a pseudo-differential VCO reading
    VCOp_enable(true);
    VCOn_enable(false);
    debug = 'vcoE';

    // Set the VCO refresh rate. This is a divider of the system clock.
    // e.g. If the system clock is 50 MHz, a divider of 1000 will request
    // a reading from the VCO at 50 kHz
    //
    // but... wait a moment? Isn't that too fast for the VCO?
    // juan you have no idea what you are doing go read: docs/source/AFE/VCO.md
    // shut up, i know! But in the behavioral model we added a "frequency gain"
    // that allows us to "fake" a slower acquisition.
    // If we were to sample the VCO at 500 Hz the simulation would take forever,
    // so we use this vco_pkg::VcoBhvFreqGain to simulate a faster oscillation of the
    // VCO, (greater V->f gain) which allows us to take samples at a higher frequency
    // without compromising the rest of the behavior.
    // Given that VcoBhvFreqGain=100, we will target a "real sampling frequency" of 500 Hz,
    // which after the gain would be equivalent to 50 kHz
    // So we divide our 50 MHz by 1000 to reach that :)
    // Oh i see. Amazing!
    uint16_t adc_refresh_rate_cc = 50; //1000;
    VCO_set_refresh_rate(adc_refresh_rate_cc);
    debug = 'adc';
    debug = (adc_refresh_rate_cc<<16) & 'cc';

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
    uint32_t* dlc_init_level          = DLC_START_ADDRESS + DLC_CURR_LVL_REG_OFFSET;
    uint32_t* dlc_bypass_en           = DLC_START_ADDRESS + DLC_BYPASS_REG_OFFSET;
    uint32_t* dlc_discard_bits        = DLC_START_ADDRESS + DLC_DISCARD_BITS_REG_OFFSET;

/*############################################################
####### SET THE DIGITAL LC PARAMETERS ######################*/

    debug = '>dLC';

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
    *dlc_hysteresis_en = 1;
    // Do not discard any bits from the input signal
    *dlc_discard_bits = 0;


    /*
    * Set the initial value of the dLC to avoid a big data rate at the beginning.
    * We do this after a few micro seconds to allow the VCOs to set-up.
    */
    debug ='wait';
    timer_wait_us(100);
    debug = 'clvl';
    uint32_t initial_value = VCO_get_count();
    *dlc_init_level = (initial_value >> *dlvl_log_level_width);

    debug = 'dLC>';

/*############################################################
####### CONFIGURE THE ADC DMA (0) #################################*/

    // Set the source target (where data is taken from) to the count register of the VCO decoder
    adc_tgt_src.ptr = (uint8_t *) (volatile uint32_t *)(VCO_DECODER_START_ADDRESS + VCO_DECODER_VCO_DECODER_CNT_REG_OFFSET);
    // Select the appropriate slot -- the VCO decoder is connected to the external trigger of the DMA 0
    adc_tgt_src.trig = DMA_TRIG_SLOT_EXT_RX;
    // Because the data is always taken from the same register, there should be no increment
    adc_tgt_src.inc_d1_du = 0;
    // We will copy data in chunks of 16 bits. Despite the decoder's output width can be vco_pkg::VcoDataWidth=32 bits,
    // the dLC will not take more than 16 bits, so all the 16 MSB will be discarded.
    // Because we will take the difference between both channels, we anyways dont expect the output to be more than 16 bits.
    adc_tgt_src.type = DMA_DATA_TYPE_HALF_WORD;

    // After passing through the dLC, the data will be stored in a separate buffer.
    adc_tgt_dst.ptr = (uint8_t *) dlc_results;
    // These data we will store in different places in memory, so the increment should be 1 data unit (du)
    adc_tgt_dst.inc_d1_du = 1;
    // We have nothing to mark the pace for the acquisition, so the slot will be simply the memory grants
    adc_tgt_dst.trig = DMA_TRIG_MEMORY;
    // We will still copy in chuncks of 16-bits for debugging
    adc_tgt_dst.type = DMA_DATA_TYPE_HALF_WORD;

    // Set the transaction
    adc_trans.src   = &adc_tgt_src;
    adc_trans.dst   = &adc_tgt_dst;
    // Set that this will be a 1-Dimensional data transfer
    adc_trans.dim   = DMA_DIM_CONF_1D;

    // We will use the DMA channel 0 as th ADC DMA. This could not be in any other way as the
    // Reception interrupts are connected to channel 0's interrupt
    adc_trans.channel = ADC_DMA;

    /*############################################################
    ####### CONFIGURE THE WINDOW INTERRUPT ######################*/

    // Prepare the window interrupt

    window_intr_flag = 0;
    transactions_intr_flag = 0;

    // The dLC will the one monitoring the end of the transactions.
    // We want to restart the DMA transaction every time the DMA has READ the whole buffer, so that it can send it again
    // Until we have processed enough data
    // We will split the whole data buffer in 4 so the SPI has enough data to fetch
    *dlc_size = (DATA_LENGTH_B/DMA_DATA_TYPE_2_SIZE(adc_tgt_src.type));

    debug = 0xFFFF;
    debug = *dlc_size;

    // Request an interrupt when the DMA reaches a certain amount of transfers
    // IMPORTANT: the window interrupt always work with the amount of packets written.
    // How many transfers? Depends on what you want... but make sure that the
    // CPU will be able to execute all it's code before the next interrupt
    adc_trans.win_du = 50;

    // Set the size of the transaction. This HAS to be the same value as the dLC will be monitoring.
    // Whether this refers to read or written words, depends on the dlc_rnw variable.
    adc_trans.size_d1_du = *dlc_size;


    // We do not set an interrupt for the transaction finish, as it would be given by the
    // window interrupt anyways.
    adc_trans.end = DMA_TRANS_END_INTR;

    // The DMA will restart the same transaction again once it finishes.
    // It will finish when the dLC tells it to do so, because it has already written dlc_size packets.
    adc_trans.mode = DMA_TRANS_MODE_CIRCULAR;

    // Specify that we will use the HW FIFO mode: all data read will be forwarded to the
    // stream peripheral that is connected to the hw fifo.
    adc_trans.hw_fifo_en = true;


/*############################################################
############## LOAD AND LAUNCH THE ADCs' DMA (0)  ############*/

    debug = 'adc';

    // Do some sanity checks to make sure that the entered values are valid
    res = dma_validate_transaction(&adc_trans, DMA_ENABLE_REALIGN, DMA_PERFORM_CHECKS_INTEGRITY);
    if( res != DMA_CONFIG_OK ){
        debug = res | (1<<24);
        PRINTF("Error: dma_validate_transaction: %d\n",res );
        return EXIT_FAILURE;
    }

    debug = 'val';

    // Load the values into the DMA registers.
    res = dma_load_transaction(&adc_trans);
    if( res != DMA_CONFIG_OK ) {
        debug = res | (2<<24);;
        PRINTF("Error: dma_load_transaction: %d\n", res);
        return EXIT_FAILURE;
    }

/*############################################################
####### LAUNCH THE ADC's DMA #################################*/

    // Launch the DMA transactions.
    // The DAC DMA will start sending data to the iDACs when the allow it.
    // The ADC DMA will wait for the ADC to inform that there is data to receive

    if(dma_launch(&adc_trans) != DMA_CONFIG_OK){
        PRINTF("Error: Failed to launch the ADC DMA\n");
        return EXIT_FAILURE;
    }

    debug='dma!';

    #if !TARGET_SIM
    enable_timer_interrupt();
    // Wait for a while just for the lols
    timer_wait_us(1000000);
    #endif


/*############################################################
####### WAIT FOR THE DMA TO FINISH ########################*/

    // This is an arbitrary number I chose from seeing more or less how many windows will be
    // triggered during the recording of ECG that we have, considering the transactions finishing.
    uint8_t windows_to_process = 7;

    debug = 'win:';

    while( window_intr_flag + transactions_intr_flag < windows_to_process ) {
        CSR_CLEAR_BITS(CSR_REG_MSTATUS, 0x8);
        if ( window_intr_flag + transactions_intr_flag < windows_to_process  ) {
                wait_for_interrupt();
        }
        debug = window_intr_flag + transactions_intr_flag;
        CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);
    }

    debug = 'wkup';

/*############################################################
################## ENABLE dLC INTERRUPTS ######################*/
debug = 'dLC!';

CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);
CSR_SET_BITS(CSR_REG_MIE, INTR_EXTERNAL );
enable_fast_interrupt(15,1);


/*############################################################
####### SWITCH TO BYPASS MODE ###############################*/
debug = 'byps';

    /*
    * Increase the width of the levels so that the events are less frequent
    */
    *dlvl_log_level_width = *dlvl_log_level_width+2;

    /*
    * Set the bypass mode
    */
    *dlc_bypass_en = 1;

    while( xing_intr_flag < 1 ) {
        CSR_CLEAR_BITS(CSR_REG_MSTATUS, 0x8);
        if ( xing_intr_flag < 1  ) {
            wait_for_interrupt();
            break;
            debug = '!!';
        }
        debug = xing_intr_flag;
        CSR_SET_BITS(CSR_REG_MSTATUS, 0x8);
    }

    debug = 'wkup';


/*############################################################
####### STOP THE SYSTEM ###################################*/

    dma_stop_circular(0);
    dma_stop_circular(1);

    VCOn_enable(0);
    VCOp_enable(0);

    iDACs_enable(0,0);

    _writestr("done");

    debug = 'stop';

    return EXIT_SUCCESS;

}
