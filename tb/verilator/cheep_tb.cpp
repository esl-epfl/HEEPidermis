// Copyright 2022 EPFL and Politecnico di Torino.
// Solderpad Hardware License, Version 2.1, see LICENSE.md for details.
// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
//
// File: cheep_tb.cpp
// Author: Michele Caon
// Date: 08/06/2023
// Description: Verilator C++ testbench for cheep

// System libraries
#include <cstdlib>
#include <cstdio>
#include <getopt.h>
#include <stdint.h>
#include <errno.h>

// Verilator libraries
#include <verilated.h>
#include <verilated_fst_c.h>
#include <svdpi.h>

// User libraries
#include "tb_macros.hh"
#include "Vtb_system.h"

// Defines
// -------
#define FST_FILENAME "logs/waves.fst"
#define PRE_RESET_CYCLES 200
#define RESET_CYCLES 200
#define POST_RESET_CYCLES 50
#define MAX_SIM_CYCLES 2e6
#define BOOT_SEL 0 // 0: JTAG boot
#define EXEC_FROM_FLASH 0 // 0: do not execute from flash
#define RUN_CYCLES 5000
#define TB_HIER_NAME "TOP.tb_system"

// Data types
// ----------
enum boot_mode_e {
    BOOT_MODE_JTAG = 0,
    BOOT_MODE_FLASH = 1,
    BOOT_MODE_FORCE = 2
};

// Function prototypes
// -------------------
// Process runtime parameters
std::string getCmdOption(int argc, char* argv[], const std::string& option);

// DUT initialization
void initDut(Vtb_system *dut, uint8_t boot_mode, uint8_t exec_from_flash);

// Generate clock and reset
void clkGen(Vtb_system *dut);
void rstDut(Vtb_system *dut, uint8_t gen_waves, VerilatedFstC *trace);

// Run simulation for the specififed number of cycles
void runCycles(unsigned int ncycles, Vtb_system *dut, uint8_t gen_waves, VerilatedFstC *trace);

// Global variables
// ----------------
// Testbench logger
TbLogger logger;
vluint64_t sim_cycles = 0;

int main(int argc, char *argv[])
{
    // Exit value
    int exit_val = EXIT_SUCCESS;

    // COMMAND-LINE OPTIONS
    // --------------------
    // Define command-line options
    bool gen_waves = false;
    bool no_err = false;
    const option longopts[] = {
        {"help", no_argument, NULL, 'h'},
        {"log_level", required_argument, NULL, 'l'},
        {"trace", required_argument, NULL, 't'},
        {"no_err", required_argument, NULL, 'q'},
        {NULL, 0, NULL, 0}
    };

    // Parse command-line options
    int opt;
    while ((opt = getopt_long(argc, argv, "hl:t:q:", longopts, NULL)) >= 0) {
        switch (opt) {
        case 'h':
            printf("Usage: %s [OPTIONS]\n", argv[0]);
            printf("Options:\n");
            printf("  -h, --help\t\t\tPrint this help message\n");
            printf("  -l, --log_level=LOG_LEVEL\tSet the log level\n");
            printf("  -t, --trace=[true/false]\t\tGenerate waveforms\n");
            printf("  -q, --no_err=[true/false]\t\t\tAlways return 0\n");
            exit(0);
            break;
        case 'l':
            logger.setLogLvl(optarg);
            break;
        case 't':
            if (strcmp(optarg, "1") == 0 || strcmp(optarg, "true") == 0) {
                gen_waves = true;
            }
            break;
        case 'q':
            if (strcmp(optarg, "1") == 0 || strcmp(optarg, "true") == 0) {
                no_err = true;
            }
            break;
        default:
            printf("Usage: %s [OPTIONS]\n", argv[0]);
            printf("Try '%s --help' for more information.\n", argv[0]);
            exit(1);
            break;
        }
    }

    // Parse the remaining command-line arguments
    // ------------------------------------------
    std::string boot_mode_str;
    unsigned int boot_mode = 0;
    std::string firmware_file;
    std::string max_cycles_str;
    unsigned long max_cycles = MAX_SIM_CYCLES;

    // Boot mode
    boot_mode_str = getCmdOption(argc, argv, "+boot_mode=");
    if (boot_mode_str == "jtag" || boot_mode_str == "0") {
        boot_mode = BOOT_MODE_JTAG;
    } else if (boot_mode_str == "flash" || boot_mode_str == "1") {
        boot_mode = BOOT_MODE_FLASH;
    } else if (boot_mode_str == "force" || boot_mode_str == "2") {
        boot_mode = BOOT_MODE_FORCE;
    } else {
        TB_WARN("Invalid boot mode '%s'. Defaulting to JTAG", boot_mode_str.c_str());
        boot_mode_str = "jtag";
        boot_mode = BOOT_MODE_JTAG;
    }

    // Firmware HEX file
    firmware_file = getCmdOption(argc, argv, "+firmware=");
    if (firmware_file.empty()) {
        TB_ERR("No firmware file specified");
        exit(EXIT_FAILURE);
    } else {
        // Check if file exists
        FILE *fp = fopen(firmware_file.c_str(), "r");
        if (fp == NULL) {
            TB_ERR("Cannot open firmware file '%s': %s", firmware_file.c_str(), strerror(errno));
            exit(EXIT_FAILURE);
        }
    }

    // Max simulation cycles
    max_cycles_str = getCmdOption(argc, argv, "+max_cycles=");
    if (!max_cycles_str.empty()) {
        max_cycles = std::stoul(max_cycles_str);
    }

    // Testbench initialization
    // ------------------------
    // Create log directory
    if (gen_waves) Verilated::mkdir("logs");

    // Create Verilator simulation context
    VerilatedContext *cntx = new VerilatedContext;
    cntx->commandArgs(argc, argv);
    if (gen_waves) cntx->traceEverOn(true);

    // Pass the simulation context to the logger
    logger.setSimContext(cntx);

    // Instantiate the DUT
    Vtb_system *dut = new Vtb_system(cntx);

    // Set the file to store the waveforms in
    VerilatedFstC *trace = NULL;
    if (gen_waves) {
        trace = new VerilatedFstC;
        dut->trace(trace, 10);
        trace->open(FST_FILENAME);
    }

    // Set scope for DPI functions
    svSetScope(svGetScopeFromName(TB_HIER_NAME));
    svScope scope = svGetScope();
    if (scope == 0) {
        TB_ERR("svSetScope(): failed to set scope for DPI functions to %s", TB_HIER_NAME);
        exit(EXIT_FAILURE);
    }

    // Print testbench configuration
    // -----------------------------
    TB_CONFIG("Log level set to %u", logger.getLogLvl());
    TB_CONFIG("Waveform tracing %s", gen_waves ? "enabled" : "disabled");
    TB_CONFIG("Max simulation cycles set to %lu", max_cycles);
    TB_CONFIG("Boot mode: %s", boot_mode_str.c_str());
    TB_CONFIG("Firmware: %s", firmware_file.c_str());
    TB_CONFIG("Executing from %s", EXEC_FROM_FLASH ? "flash" : "RAM");

    // RUN SIMULATION
    // --------------
    TB_LOG(LOG_MEDIUM, "Starting simulation");

    // Initialize the DUT
    initDut(dut, boot_mode, EXEC_FROM_FLASH);

    // Reset the DUT
    rstDut(dut, gen_waves, trace);

    // Load firmware to SRAM
    switch (boot_mode)
    {
    case BOOT_MODE_JTAG:
        TB_LOG(LOG_LOW, "Waiting for JTAG (e.g., OpenOCD) to load firmware...");
        break;

    case BOOT_MODE_FORCE:
        TB_LOG(LOG_LOW, "Loading firmware...");
        TB_LOG(LOG_MEDIUM, "- writing firmware to SRAM...");
        dut->tb_loadHEX(firmware_file.c_str());
        runCycles(1, dut, gen_waves, trace);
        TB_LOG(LOG_MEDIUM, "- triggering boot loop exit...");
        dut->tb_set_exit_loop();
        runCycles(1, dut, gen_waves, trace);
        TB_LOG(LOG_LOW, "Firmware loaded. Running app...");
        break;

    case BOOT_MODE_FLASH:
        TB_LOG(LOG_LOW, "Waiting for boot code to load firmware from flash...");
        break;

    default:
        TB_ERR("Invalid boot mode: %d", boot_mode);
        exit(EXIT_FAILURE);
    }

    // Run until the end of simulation is reached
    while (!cntx->gotFinish() && cntx->time() < (max_cycles << 1) && dut->exit_valid_o == 0) {
        TB_LOG(LOG_FULL, "Running %lu cycles...", RUN_CYCLES);
        runCycles(RUN_CYCLES, dut, gen_waves, trace);
    }
    if (cntx->time() >= (max_cycles << 1)) {
        TB_WARN("Max simulation cycles reached");
    }

    // Print simulation status
    TB_LOG(LOG_LOW, "Simulation complete");

    // Check exit value
    if (dut->exit_valid_o) {
        TB_LOG(LOG_LOW, "Exit value: %d", dut->exit_value_o);
        exit_val = dut->exit_value_o;
        runCycles(10, dut, gen_waves, trace);
    } else {
        TB_ERR("No exit value detected");
        exit_val = EXIT_FAILURE;
    }

    // CLEAN UP
    // --------
    // Simulation complete
    dut->final();

    // Clean up and exit
    if (gen_waves) trace->close();
    delete dut;
    delete cntx;
    if (no_err) exit(EXIT_SUCCESS);
    exit(exit_val);
}

void initDut(Vtb_system *dut, uint8_t boot_mode, uint8_t exec_from_flash) {
    // Clock and reset
    dut->ref_clk_i = 0;
    dut->rst_ni = 1;

    // Static configuration
    dut->boot_select_i = boot_mode == BOOT_MODE_FLASH;
    dut->execute_from_flash_i = exec_from_flash;
    dut->eval();
}

void clkGen(Vtb_system *dut) {
    dut->ref_clk_i ^= 1;
}

void rstDut(Vtb_system *dut, uint8_t gen_waves, VerilatedFstC *trace) {
    dut->rst_ni = 1;
    TB_LOG(LOG_MEDIUM, "Resetting DUT...");
    runCycles(PRE_RESET_CYCLES, dut, gen_waves, trace);
    dut->rst_ni = 0;
    TB_LOG(LOG_MEDIUM, "- reset asserted");
    runCycles(RESET_CYCLES, dut, gen_waves, trace);
    TB_LOG(LOG_MEDIUM, "- reset released");
    dut->rst_ni = 1;
    runCycles(POST_RESET_CYCLES, dut, gen_waves, trace);
}

void runCycles(unsigned int ncycles, Vtb_system *dut, uint8_t gen_waves, VerilatedFstC *trace) {
    VerilatedContext *cntx = dut->contextp();
    for (unsigned int i = 0; i < (2*ncycles); i++) {
        // Generate clock
        clkGen(dut);

        // Evaluate the DUT
        dut->eval();

        // Save waveforms
        if (gen_waves) trace->dump(cntx->time());
        if (dut->ref_clk_i == 1) sim_cycles++;
        cntx->timeInc(1);
    }
}

std::string getCmdOption(int argc, char* argv[], const std::string& option)
{

    std::string cmd;
    for( int i = 0; i < argc; ++i)
    {
        std::string arg = argv[i];
        size_t arg_size = arg.length();
        size_t option_size = option.length();

        if(arg.find(option)==0){
        cmd = arg.substr(option_size,arg_size-option_size);
        }
    }
    return cmd;
}