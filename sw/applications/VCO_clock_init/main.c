#include "VCO_decoder.h"

int main(void) {
    // Enable the VCO used as system clock
    VCOp_enable(true);

    // Leave the oscillator running continuously
    while (1) {
        asm volatile ("wfi");
    }

    return 0;
}
