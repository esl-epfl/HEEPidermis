#include <stdio.h>

#include "input_signal.h"   // defines signal_length and signal[] (values in nS)
#include "descompv5.h"

/* PRINTF: works for chip sim, FPGA, and native host compile */
#define PRINTF_IN_SIM  1
#define PRINTF_IN_FPGA 1

#if TARGET_SIM && PRINTF_IN_SIM
    #define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#elif PRINTF_IN_FPGA && !TARGET_SIM
    #define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#else
    #define PRINTF(...)
#endif

#ifndef signal_line_start
#define signal_line_start 500000
#endif

#ifndef signal_line_end
#define signal_line_end 600000
#endif

#ifndef signal_sample_step
#define signal_sample_step 1
#endif

extern int EDA_U_diag0[];
extern int EDA_U_diag1[];
extern int EDA_U_diag2[];
extern int EDA_L_mult_1[];
extern int EDA_L_mult_2[];

static int tonic[signal_length];
static int phasic[signal_length];

int main(void)
{
    EDA_build_A_and_factorize();

    solve_v5(signal_length, signal, tonic, phasic,
             EDA_U_diag0, EDA_U_diag1, EDA_U_diag2,
             EDA_L_mult_1, EDA_L_mult_2);

    /* Metadata header so compare.py can parse parameters */
    PRINTF("# signal_length=%d line_start=%d line_end=%d sample_step=%d units=nS\n",
           signal_length, signal_line_start, signal_line_end, signal_sample_step);
    PRINTF("idx,signal_nS,tonic,phasic\n");
    for (int i = 0; i < signal_length; i++)
        PRINTF("%d,%d,%d,%d\n", i, signal[i], tonic[i], phasic[i]);

    return 0;
}
