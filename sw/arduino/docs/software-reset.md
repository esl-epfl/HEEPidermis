# Software-controlled CPU reset

The Raspberry Pi Pico SPI programmer resets the **CPU domain**, not the entire HEEPidermis SoC. It does so through memory-mapped X-HEEP power-manager registers over SPI. SRAM contents and the configuration of peripherals outside the CPU reset domain are preserved, allowing repeated firmware loading and internal-VCOp clock handover.

From the repository root:

```bash
make spi-reset
```

The Python SDK sends the `X` command over USB serial; the Pico handles it in `heepResetCpuToBootRom()` in [`heep_programmer.ino`](../heep_programmer/heep_programmer.ino), returning `OK` on success or `ERR_RESET` on failure.

## Registers used

| Register | Address | Purpose |
| --- | --- | --- |
| `SOC_CTRL.BOOT_EXIT_LOOP` | `0x2000000C` | Write `0` to keep Boot ROM waiting in SPI/JTAG boot mode |
| `POWER_MANAGER.WAKEUP_STATE` | `0x20040000` | Write `0` for normal boot rather than sleep-state restoration |
| `POWER_MANAGER.CPU_RESET_ASSERT_COUNTER` | `0x2004001C` | Set reset assertion counter to `5` |
| `POWER_MANAGER.CPU_RESET_DEASSERT_COUNTER` | `0x20040020` | Set reset release counter to `5` |
| `POWER_MANAGER.MONITOR_POWER_GATE_CORE` | `0x2004008C` | Poll bit 2, `cpu_subsystem_rst_n` (low = CPU reset asserted) |
| `POWER_MANAGER.MASTER_CPU_FORCE_RESET_ASSERT` | `0x200400A4` | Write `1` to request assertion and `0` to clear it |
| `POWER_MANAGER.MASTER_CPU_FORCE_RESET_DEASSERT` | `0x200400A8` | Write `1` to request release and `0` to clear it |

The low three monitor bits are `{cpu_subsystem_rst_n, cpu_subsystem_powergate_iso_n, cpu_subsystem_powergate_switch_n}`; CPU reset is bit 2.

## Reset sequence

1. **Prepare Boot ROM:** Clear `BOOT_EXIT_LOOP` and `WAKEUP_STATE`. This prevents an immediate jump to a previously configured application on the SPI/JTAG boot path, and ensures normal boot instead of sleep-state restoration.
2. **Configure counters:** Set both reset counters to `5`. The power manager sequences assertion/release through these counters.
3. **Assert reset:** Write `1` to `MASTER_CPU_FORCE_RESET_ASSERT`, poll `MONITOR_POWER_GATE_CORE` until `(value & 0x4) == 0`, then write `0` to clear the request.
4. **Release reset:** Write `1` to `MASTER_CPU_FORCE_RESET_DEASSERT`, poll until `(value & 0x4) != 0`, then write `0` to clear the request.

Each poll has a 100 ms timeout. Failed register operations or state transitions cause the Pico to return `ERR_RESET`.

## What happens afterward?

When released, the CPU begins executing the Boot ROM. With `BOOT_SELECT = 0` (SPI/JTAG boot path), the cleared `BOOT_EXIT_LOOP` keeps the CPU waiting. The programmer's `run` operation later writes the application entry point to `SOC_CTRL.BOOT_ADDRESS` (`0x20000010`) and sets `BOOT_EXIT_LOOP = 1`. With flash boot selected, the Boot ROM follows the flash boot path instead.

This procedure does **not** power-cycle the chip, erase SRAM, or reset peripherals outside the CPU reset domain. A working system clock is required for the power manager to progress. The internal VCOp oscillator can remain configured and running while the CPU domain is reset.

**Counter-rearming caveat:** The implementation configures the counters but does not set their corresponding `CPU_COUNTERS_STOP` bits. The `reg_to_counter.sv` FSM stays in `EXPIRE` until the associated stop bit is asserted, so the five-count delay may not be rearmed on later resets. Repeated-reset timing should be verified.

## Source files

- [Pico firmware](../heep_programmer/heep_programmer.ino) — `heepResetCpuToBootRom()`
- [Python SDK](../heep_sdk.py) — `reset_cpu_to_bootrom()`
- [Power-manager RTL](../../../hw/vendor/x-heep/hw/ip/power_manager/rtl/power_manager.sv.tpl)
- [Counter RTL](../../../hw/vendor/x-heep/hw/ip/power_manager/rtl/reg_to_counter.sv)
- [Boot ROM](../../../hw/vendor/x-heep/hw/ip/boot_rom/boot_rom.S)
