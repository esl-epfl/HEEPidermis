#include <stdio.h>
#include "soc_ctrl.h"
#include "uart.h"
#include "core_v_mini_mcu.h"
#include "x-heep.h"
#include "syscalls.h"
#include <sys/stat.h>
#include <sys/reent.h>
#include <string.h>
#include <newlib.h>
#include <unistd.h>
#include <reent.h>
#include <errno.h>
#include "uart_regs.h"
#include "pad_control.h"
#include "pad_control_regs.h"
#include "gpio.h"

int main() {

    soc_ctrl_t soc_ctrl;
    soc_ctrl.base_addr = mmio_region_from_addr((uintptr_t)SOC_CTRL_START_ADDRESS);


    pad_control_t pad_control;
    pad_control.base_addr = mmio_region_from_addr((uintptr_t)PAD_CONTROL_START_ADDRESS);
    pad_control_set_mux(&pad_control, (ptrdiff_t)PAD_CONTROL_PAD_MUX_LC_XING_REG_OFFSET,   1);
    pad_control_set_mux(&pad_control, (ptrdiff_t)PAD_CONTROL_PAD_MUX_LC_DIR_REG_OFFSET,     1);

    gpio_cfg_t pin_data = { .pin = 2, .mode = GpioModeIn };
    if (gpio_config (pin_data) != GpioOk) printf("Gpio initialization failed!\n");
    gpio_cfg_t pin_led = { .pin = 3, .mode = GpioModeIn };
    if (gpio_config (pin_led) != GpioOk) printf("Gpio initialization failed!\n");


    uart_t uart;
    uart.base_addr   = mmio_region_from_addr((uintptr_t)UART_START_ADDRESS);
    uart.baudrate    = UART_BAUDRATE;
    uart.clk_freq_hz = soc_ctrl_get_frequency(&soc_ctrl);
    #ifdef UART_NCO
    uart.nco         = UART_NCO/10;
    #else
    uart.nco         = ((uint64_t)uart.baudrate << (NCO_WIDTH + 4)) / uart.clk_freq_hz;
    #endif

    if (uart_init(&uart) != kErrorOk) {
        errno = ENOSYS;
        return -1;
    }


    _writestr("Starting Echo Test. Type something...\n");

    while(1) {
        uint32_t reg = mmio_region_read32(uart.base_addr, UART_RDATA_REG_OFFSET);
        printf("--->%d\n",reg);
        for (int i = 0 ; i < 20000 ; i++) asm volatile ("nop");
    }
}