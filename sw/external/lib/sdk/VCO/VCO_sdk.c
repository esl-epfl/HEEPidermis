// Copyright 2026 EPFL contributors
// SPDX-License-Identifier: Apache-2.0
//
// File: VCO_sdk.c
// Author: Omar Shibli & Ismail Essaidi
// Date: 08/04/2026
// Description: Implementation of the VCO SDK functions

#include "VCO_sdk.h"
#define TARGET_SIM 1

#define TABLE_SIZE 25
#define VCO_GAIN 1
#define VCO_ACCEL_RATIO 100 // The ratio by which the VCO is accelerated in simulation to allow faster testing. The refresh rate and integration rate are divided by this factor in simulation mode.
#define VCO_DECODER_PHASES 62u
#define VCO_READOUT_DELAY_CC 3u

// clock cycles compensations 
#define OFF_COMPENSATION_CC                216U  // +10 +28
#define ON_COMPENSATION_CC                 146U  // -10   
#define FIRST_CONF_CHANGE_COMPENSATION_CC  217U  // +30
#define CONF_CHANGE_COMPENSATION_CC        174U  // +30

// TODO: check if 320mV is accurate + we can discard 820 mV point
const uint32_t _table_Vin_uV[TABLE_SIZE] ={
    330000, 340000, 360000, 380000, 400000, 
    420000, 440000, 460000, 480000, 500000, 
    520000, 540000, 560000, 580000, 600000, 
    620000, 640000, 660000, 680000, 700000, 
    720000, 740000, 760000, 780000, 800000
};
const uint32_t _table_fosc_Hz[TABLE_SIZE] = {
    24000, 26130, 31330, 37320, 45270,
    55150, 67270, 82680, 99870, 121190,
    146020, 175270, 208990, 247770, 291780,
    341260, 396650, 457900, 525140, 598560, 
    677660, 762750, 853760, 950200, 1051710
};

const uint32_t _table_kvco_Hz_per_V[TABLE_SIZE] = {
    200000, 233333,   275000,  350000, 450000, 
    550000,   650000,  800000, 975000, 1175000,
    1350000, 1575000, 1825000, 2075000, 2400000, 
    2625000, 2800000, 3075000, 3600000, 3900000,
    4025000, 4475000, 4750000, 4875000, 5500000
};

#define SYS_FCLK_HZ 10000000
#define VCO_SUPPLY_VOLTAGE_UV    800000U

#define INTERPOLATE_FROM_LUT // comment if you want to read directly from LUT without interpolation (it gives less smooth results)

#define VCO_FLAG_HAS_PREV        (1U << 0)
#define VCO_FLAG_CONFIG_CHANGED  (1U << 1)
#define VCO_FLAG_ENABLED         (1U << 2)
#define VCO_FLAG_UNDERFLOW       (1U << 3)
#define VCO_FLAG_OVERFLOW        (1U << 4)

static uint32_t g_refresh_rate_Hz = 0;
static vco_sdk_t vco_data;

static bool vco_flag_is_set(uint8_t flag) {
    return (vco_data.flags & flag) != 0U;
}

static void vco_flag_set(uint8_t flag) {
    vco_data.flags |= flag;
}

static void vco_flag_clear(uint8_t flag) {
    vco_data.flags &= (uint8_t)~flag;
}

static uint32_t vco_get_refresh_cycles(void) {
    return vco_data.on_cycles + vco_data.off_cycles;
}

static uint32_t freq_to_cc(uint32_t frequency_Hz) {
#if TARGET_SIM
    return SYS_FCLK_HZ / (VCO_ACCEL_RATIO * frequency_Hz);
#else
    return SYS_FCLK_HZ / frequency_Hz;
#endif
}

static void timer_irq_disable_local(void) {
    rv_timer_irq_enable(&timer, 0, 0, kRvTimerDisabled);
}

static void vco_arm_toggle_after(uint32_t delay_cycles) {
    uint32_t now;

    if (delay_cycles == 0U) return;

    now = timer_get_cycles();
    timer_irq_disable_local();
    timer_irq_clear();
    timer_arm_set(now + delay_cycles);
    timer_irq_enable();
}

static void vco_update_duty_windows(uint8_t duty_cycle_code) {
    vco_data.duty_cycle_code = duty_cycle_code;
    uint32_t refresh_cycles = freq_to_cc(g_refresh_rate_Hz);

    if (vco_data.duty_cycle_code == 1U) {
        vco_data.on_cycles = refresh_cycles;
        vco_data.off_cycles = 0U;
        vco_data.integration_rate_Hz = g_refresh_rate_Hz;
        return;
    }

    vco_data.on_cycles = (refresh_cycles / vco_data.duty_cycle_code);
    vco_data.off_cycles = refresh_cycles - vco_data.on_cycles;
    vco_data.integration_rate_Hz = (g_refresh_rate_Hz * vco_data.duty_cycle_code);
}

/*
This function enables/disables the VCO and handles vco_data
*/
static vco_status_t vco_enable(vco_channel_t channel, bool enable)
{
    // VCO_trigger();
    switch (channel)
    {
    case VCO_CHANNEL_NONE:
        break;
    case VCO_CHANNEL_P:
        VCOp_enable(enable);
        break;
    case VCO_CHANNEL_N:
        VCOn_enable(enable);
        break;
    case VCO_CHANNEL_DIFFERENTIAL:
        VCOp_enable(enable);
        VCOn_enable(enable);
        break;
    default:
        return VCO_STATUS_INVALID_CONFIGURATION;
    }
    // also duty cycle the iDACs
    iDACs_enable(enable, false);
    iDACs_trigger();
    if (enable) {
        vco_flag_set(VCO_FLAG_ENABLED);
    } else {
        vco_flag_clear(VCO_FLAG_ENABLED);
    }
    return VCO_STATUS_OK;
}

/*  
This function initializes the VCO, it uses an enum to set the channel
used as either NONE, P Channel, N channel, or Pseudo Differential mode.
*/
vco_status_t vco_initialize(vco_channel_t channel, uint32_t refresh_rate_Hz){
    
    //Check if valid refresh rate (avoid division by 0)
    if (refresh_rate_Hz == 0) {
        return VCO_STATUS_INVALID_ARGUMENT;
    }
    // clean start
    VCOp_enable(false);
    VCOn_enable(false);
    
    //Enable the used channel based on the specified input
    vco_status_t status = vco_enable(channel, true); // updates vco_data.vco_enabled
    if (status != VCO_STATUS_OK) return status;
    
    // set the VCO timing
    g_refresh_rate_Hz = refresh_rate_Hz;
    vco_update_duty_windows(1U);

    VCO_set_refresh_rate(vco_data.on_cycles);

    //initialize the VCO data 
    vco_data.last_timestamp = 0;
    vco_data.flags &= (uint8_t)~(VCO_FLAG_HAS_PREV | VCO_FLAG_CONFIG_CHANGED);
    vco_data.channel = (uint8_t)channel;

    return VCO_STATUS_OK;
}

/*
This function sets the refresh rate of the VCO. It updates both the global variable and the hardware VCO register.
*/
vco_status_t vco_config(vco_channel_t channel, uint32_t refresh_rate_Hz, uint8_t duty_cycle_code) {
    
    if (channel == VCO_CHANNEL_NONE || channel != (vco_channel_t)vco_data.channel) return VCO_STATUS_INVALID_CONFIGURATION;

    if (refresh_rate_Hz == 0  || duty_cycle_code == 0U) return VCO_STATUS_INVALID_ARGUMENT;
    
    /* 
        1. Refresh rate setup
    */
    // update the global variables
    g_refresh_rate_Hz = refresh_rate_Hz;
    vco_update_duty_windows(duty_cycle_code);

    // update the hardware register with the full refresh period
    VCO_set_refresh_rate(vco_data.on_cycles);
    VCO_trigger();
    vco_flag_set(VCO_FLAG_CONFIG_CHANGED); // set the configuration changed flag since changing config biases timestamp that we use in vco_get_Vin_uV.
    
    /* 
        2. Duty cycling setup
    */
    if (vco_data.duty_cycle_code == 1U) { // if 1/D=1, keep VCO ON (no duty cycling)
        timer_irq_disable_local();
        timer_irq_clear();
        return vco_enable(channel, true);
    }

    if (vco_flag_is_set(VCO_FLAG_ENABLED)) {
        vco_status_t status = vco_enable(channel, false);
        if (status != VCO_STATUS_OK) return status;
        vco_arm_toggle_after(vco_data.off_cycles-FIRST_CONF_CHANGE_COMPENSATION_CC);
        return VCO_STATUS_OK;
    }

    vco_arm_toggle_after(vco_data.off_cycles-CONF_CHANGE_COMPENSATION_CC); // 154 for normal compensation + 10 compensation for different sampling frequencies
    return VCO_STATUS_OK;
}

/*
In this function we search for the value x based on fp(xp) LUT. 
The xp and fp arrays represent the known points of the function, while left and right are the values to return if x is out of bounds.
*/
static uint32_t search_LUT(uint32_t x,
    uint32_t *xp, uint32_t *fp,
    uint32_t left, uint32_t right)
{
    // 1. Handle Out-of-Bound
    if (x < xp[0]) return left;
    if (x >= xp[TABLE_SIZE - 1]) return right;
    
    // 2. Binary Search to find the interval [low, high]
    uint8_t low = 0, high = TABLE_SIZE - 1;
    while (low < high - 1) {
        uint8_t mid = low + (high - low) / 2;
        if (xp[mid] < x) low = mid;
        else high = mid;
    }
    // 3. Return lower bound value. We could return high or low. // TODO check which one gives better results.
    return fp[low];
}

/*
In this function we perform a linear interpolation of the value x based on fp(xp) LUT. 
The xp and fp arrays represent the known points of the function, while left and right are the values to return if x is out of bounds.
*/
static uint32_t linear_interp(uint32_t x,
                        uint32_t *xp,
                        uint32_t *fp,
                        uint32_t left, uint32_t right)
{
    // 1. Handle Out-of-Bound
    if (x <= xp[0]) return left;
    if (x >= xp[TABLE_SIZE - 1]) return right;
    
    // 2. Binary Search to find the interval [low, high]
    uint8_t low = 0, high = TABLE_SIZE - 1;
    while (low < high - 1) {
        uint8_t mid = low + (high - low) / 2;
        if (xp[mid] < x) low = mid;
        else high = mid;
    }
        
    // 3. Linear Interpolation Formula
    // result = fp0 + (x_target - x0) * (fp1 - fp0) / (x1 - x0)
    uint32_t x0 = xp[low];
    uint32_t x1 = xp[high];
    // We multiply before dividing to keep precision.
    return fp[low] + (((fp[high] - fp[low]) * (x - x0)) / (x1 - x0));
}

// Interpolate Vin from a VCO oscillation frequency using the calibration table.
uint32_t interpolate_Vin_uV(uint32_t f_target){
    if (f_target < _table_fosc_Hz[0]) vco_flag_set(VCO_FLAG_UNDERFLOW);
    else if((f_target > _table_fosc_Hz[TABLE_SIZE - 1])) vco_flag_set(VCO_FLAG_OVERFLOW);
    else vco_flag_clear(VCO_FLAG_UNDERFLOW | VCO_FLAG_OVERFLOW);
    return linear_interp(f_target, _table_fosc_Hz, _table_Vin_uV, _table_Vin_uV[0], _table_Vin_uV[TABLE_SIZE - 1]);
}

// Estimate local VCO sensitivity K_VCO = df/dV around a given Vin.
uint32_t vco_get_kvco_Hz_per_V(uint32_t vin_uV) {

    // left value is 0 because VCO stops oscillating below 330mV
    #ifdef INTERPOLATE_FROM_LUT
        return linear_interp(vin_uV, _table_Vin_uV, _table_kvco_Hz_per_V, 0, _table_kvco_Hz_per_V[TABLE_SIZE - 1]);
    #else 
        return search_LUT(vin_uV, _table_Vin_uV, _table_kvco_Hz_per_V, 0, _table_kvco_Hz_per_V[TABLE_SIZE - 1]);
    #endif
}

/*
This function return the frequency read from the counter of the VCO based 
on the setup that was initialized.
*/
vco_status_t vco_get_Vin_uV(uint32_t* vin_uV){

    //make sure the VCO is properly initialized
    if (vin_uV == 0){
        return VCO_STATUS_INVALID_ARGUMENT;
    }

    if (g_refresh_rate_Hz == 0 || vco_data.channel == VCO_CHANNEL_NONE) {
        return VCO_STATUS_NOT_INITIALIZED;
    }

    uint32_t now = timer_get_cycles();
    uint32_t elapsed_cycles = now - vco_data.last_timestamp;
    uint32_t refresh_cycles = vco_get_refresh_cycles();

    uint32_t readout_delay = (vco_data.on_cycles > VCO_READOUT_DELAY_CC)
                        ? VCO_READOUT_DELAY_CC
                        : 0u;
    uint64_t sample_ready_cycles = (uint64_t)refresh_cycles + readout_delay;
    uint64_t missed_ready_cycles =
        ((uint64_t)refresh_cycles * 2u) + readout_delay;

    /* If it is our first measurement, or the first one after a config change:
        1. We reset the timestamp (take into account config change latency)
        2. We return No_New_SAMPLE to indicate that we need a 2nd measurement for 1 sample 
    */
    bool discard_next = vco_flag_is_set(VCO_FLAG_CONFIG_CHANGED) || !vco_flag_is_set(VCO_FLAG_HAS_PREV); // either config just changed or it is the first read
    if (discard_next) {
        vco_data.last_timestamp = now; // reset timestamp because it was biased by config change
        vco_flag_clear(VCO_FLAG_CONFIG_CHANGED);
        vco_flag_set(VCO_FLAG_HAS_PREV);
        return VCO_STATUS_NO_NEW_SAMPLE;
    }

    if (vco_data.duty_cycle_code==1U) { // check timings because if no duty cycling reading is done via Polling otherwise confident that the interrupt based reading will result in valid samples 
        // Make sure the delayed refresh train has latched the decoder count.
        if ((uint64_t)elapsed_cycles < sample_ready_cycles) {
            return VCO_STATUS_NO_NEW_SAMPLE;   // no new refresh yet
        }

        if ((uint64_t)elapsed_cycles >= missed_ready_cycles) {
            vco_data.last_timestamp = now;
            vco_flag_set(VCO_FLAG_HAS_PREV);
            return VCO_STATUS_MISSED_UPDATE;   // missed one or more updates
        }
    }

    uint32_t decoder_count = VCO_get_count();
    uint32_t frequency_Hz = (uint32_t)(((uint64_t)decoder_count * vco_data.integration_rate_Hz) / VCO_DECODER_PHASES);

    *vin_uV  = interpolate_Vin_uV(frequency_Hz);

    vco_data.last_timestamp += refresh_cycles;

    if (vco_flag_is_set(VCO_FLAG_UNDERFLOW)) { // flag the out of bounds cases
        return VCO_STATUS_UNDERFLOW;
    } 
    if (vco_flag_is_set(VCO_FLAG_OVERFLOW)) { // flag the out of bounds cases
        return VCO_STATUS_OVERFLOW;
    }
    
    return VCO_STATUS_OK;
}


bool vco_is_on(void) {
    return vco_flag_is_set(VCO_FLAG_ENABLED);
}

void vco_handle_timer_irq(void) {
    if (vco_data.duty_cycle_code == 1U || vco_data.channel == VCO_CHANNEL_NONE) { 
        timer_irq_clear();
        timer_irq_disable_local();
        return;
    }

    timer_irq_clear();
    timer_irq_disable_local();

    if (vco_flag_is_set(VCO_FLAG_ENABLED)) {
        (void)vco_enable((vco_channel_t)vco_data.channel, false);
        /*
        OFF_COMPENSATION_CC was computed by profiling the actual SW to handle the timer IRQ (independant of frequency and duty cycle): check scripts/plotter/vcd_timing_rms.py 
        (D-1) because HW refresh time is always +1cc so need to compensate for the 1cc * OFF_ratio =  (1/D)-1 cc  
        */
        vco_arm_toggle_after(vco_data.off_cycles+(vco_data.duty_cycle_code-1) - OFF_COMPENSATION_CC); 
    } else {
        (void)vco_enable((vco_channel_t)vco_data.channel, true);
        /*
        ON_COMPENSATION_CC explained above: check scripts/plotter/vcd_timing_rms.py 
        (-1) because HW refresh time is always +1cc so need to compensate for the 1cc * ON_RATIO = 1cc  
        */
        vco_arm_toggle_after(vco_data.on_cycles + 1 - ON_COMPENSATION_CC);
    }
}