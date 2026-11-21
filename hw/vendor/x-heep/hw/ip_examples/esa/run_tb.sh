#!/usr/bin/env bash
set -euo pipefail

esa_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$esa_dir/../../../../../.." && pwd)"
verilator_bin="${VERILATOR:-verilator}"

if ! command -v "$verilator_bin" >/dev/null 2>&1; then
  echo "Verilator not found. Source .private/init.sh from the repository root first." >&2
  exit 1
fi
if [[ $("$verilator_bin" --version) != Verilator\ 4.* ]]; then
  echo "This testbench requires Verilator 4 (project default: 4.210)." >&2
  exit 1
fi

build_dir="$esa_dir/build"
obj_dir="$build_dir/obj_dir"
mkdir -p "$obj_dir"

"$verilator_bin" --cc --exe --trace --trace-structs --top-module esa_tb \
  --Mdir "$obj_dir" \
  "$repo_root/hw/vendor/x-heep/hw/core-v-mini-mcu/include/reg_pkg.sv" \
  "$repo_root/hw/vendor/x-heep/hw/core-v-mini-mcu/include/fifo_pkg.sv" \
  "$repo_root/hw/vendor/x-heep/hw/vendor/pulp_platform_common_cells/src/fifo_v3.sv" \
  "$esa_dir/rtl/esa_hpf_op.sv" \
  "$esa_dir/rtl/esa_abs_op.sv" \
  "$esa_dir/rtl/esa_ses_average_op.sv" \
  "$esa_dir/rtl/esa_decimation_op.sv" \
  "$esa_dir/rtl/esa_registers.sv" \
  "$esa_dir/rtl/esa_fifo_buffer.sv" \
  "$esa_dir/rtl/esa.sv" \
  "$esa_dir/tb/esa_tb.sv" \
  "$esa_dir/tb/esa_tb.cpp"
make -C "$obj_dir" -f Vesa_tb.mk -j "${JOBS:-2}"
"$obj_dir/Vesa_tb" "$build_dir/esa.vcd"
echo "Waveform: $build_dir/esa.vcd"
echo "Open with: gtkwave $build_dir/esa.vcd $esa_dir/tb/esa.gtkw"
