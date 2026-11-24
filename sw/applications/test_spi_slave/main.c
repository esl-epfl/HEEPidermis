#include "x-heep.h"
#include <stdio.h>
#include <stdint.h>

#define PRINTF_IN_SIM  1
#define PRINTF_IN_FPGA 1

#if TARGET_SIM && PRINTF_IN_SIM
    #define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#elif PRINTF_IN_FPGA && !TARGET_SIM
    #define PRINTF(fmt, ...) printf(fmt, ##__VA_ARGS__)
#else
    #define PRINTF(...)
#endif

// Tests SPI slave writes by reading a known SRAM location and checking the received 32-bit value.

int main(void)
{
    volatile uint32_t *test_addr =
        (volatile uint32_t *)0x00001000;

    PRINTF("SPI slave test\n");
    PRINTF("Memory[0x00001000] = 0x%08lx\n",
           (unsigned long)*test_addr);

    return 0;
}