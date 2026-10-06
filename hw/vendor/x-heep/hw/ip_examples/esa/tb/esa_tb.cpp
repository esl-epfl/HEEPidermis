// Verilator 4 driver for normal ESA neural-sample streaming.
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <stdexcept>
#include <string>
#include <vector>

#include "Vesa_tb.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

namespace {
const uint32_t kEnableOffset = 0x00;
const uint32_t kHpfEnableOffset = 0x04;
const uint32_t kEsaWindowShiftOffset = 0x08;
const uint32_t kEsaGainShiftOffset = 0x0c;
const uint32_t kFeatureWindowShiftOffset = 0x10;
const uint32_t kFeatureGainShiftOffset = 0x14;
const uint32_t kDecimationRateOffset = 0x18;
const uint32_t kEsaWindowShift = 2;
const uint32_t kEsaGainShift = 2;
const uint32_t kFeatureWindowShift = 1;
const uint32_t kFeatureGainShift = 1;
const uint32_t kDecimationRate = 8;
const unsigned kSamplePeriodCycles = 100;
const unsigned kSampleCount = 1000;

int32_t neural_sample(unsigned index) {
  // Integer microvolt samples inspired by ESA_for_DMA.m: low frequency
  // background, repeatable broadband noise, and occasional biphasic spikes.
  const double t = static_cast<double>(index) / 1000.0;
  const int32_t background = static_cast<int32_t>(
      std::lround(20.0 * std::sin(2.0 * M_PI * 8.0 * t) +
                  10.0 * std::sin(2.0 * M_PI * 25.0 * t)));
  const int32_t noise = static_cast<int32_t>((index * 37u + 11u) % 17u) - 8;
  int32_t spike = 0;
  uint32_t random = 0x6d2b79f5u;
  unsigned spike_index = 0;
  while (spike_index <= index) {
    random ^= random << 13;
    random ^= random >> 17;
    random ^= random << 5;
    spike_index += 5u + random % 46u;
    if (index == spike_index) spike = -100;
    if (index == spike_index + 1u) spike = 45;
  }
  return background + noise + spike;
}

class Testbench {
 public:
  explicit Testbench(const char* waveform) {
    Verilated::traceEverOn(true);
    dut_.trace(&trace_, 99);
    trace_.open(waveform);
    dut_.clk_i = 0;
    dut_.rst_ni = 0;
    dut_.phase_i = 0;
    dut_.push_i = 0;
    dut_.pop_i = 0;
    dut_.data_i = 0;
    dut_.reg_valid_i = 0;
    dut_.reg_write_i = 0;
    dut_.reg_addr_i = 0;
    dut_.reg_wdata_i = 0;
    for (int i = 0; i < 2; ++i) {
      tick_clock();
    }
    dut_.rst_ni = 1;
    tick_clock();
  }

  ~Testbench() {
    dut_.final();
    trace_.close();
  }

  void write_register(uint32_t address, uint32_t value) {
    dut_.clk_i = 0;
    dut_.phase_i = 1;
    dut_.reg_valid_i = 1;
    dut_.reg_write_i = 1;
    dut_.reg_addr_i = address;
    dut_.reg_wdata_i = value;
    sample();
    check(dut_.reg_ready_o, "register ready");
    check(!dut_.reg_error_o, "register write rejected");
    dut_.clk_i = 1;
    sample();
    dut_.reg_valid_i = 0;
    dut_.reg_write_i = 0;
    dut_.clk_i = 0;
    sample();
    dut_.clk_i = 1;
    sample();
  }

  void stream_cycle(bool push, uint32_t sample_value, bool consume_output = true) {
    dut_.clk_i = 0;
    dut_.phase_i = 2;
    dut_.push_i = push;
    dut_.pop_i = 0;
    dut_.data_i = sample_value;
    sample();

    check(dut_.done_o == 0, "done should remain low for stream mode");
    check(!(push && dut_.full_o), "input FIFO unexpectedly full");
    if (consume_output && !dut_.empty_o) {
      dut_.pop_i = 1;
      sample();
      received_.push_back(dut_.data_o);
    }

    dut_.clk_i = 1;
    sample();
    ++cycles_;
  }

  void configure_and_run() {
    write_register(kHpfEnableOffset, 1);
    write_register(kEsaWindowShiftOffset, kEsaWindowShift);
    write_register(kEsaGainShiftOffset, kEsaGainShift);
    write_register(kFeatureWindowShiftOffset, kFeatureWindowShift);
    write_register(kFeatureGainShiftOffset, kFeatureGainShift);
    write_register(kDecimationRateOffset, kDecimationRate);
    dut_.phase_i = 1;
    dut_.reg_valid_i = 1;
    dut_.reg_write_i = 1;
    dut_.reg_addr_i = kEsaWindowShiftOffset;
    dut_.reg_wdata_i = 32;
    dut_.eval();
    check(dut_.reg_error_o, "invalid average shift should be rejected");
    dut_.reg_valid_i = 0;
    dut_.reg_write_i = 0;
    write_register(kEnableOffset, 1);

    // Produce one neural sample on every 100th clock cycle. The HW FIFO is
    // drained continuously, like the DMA consuming generated features.
    unsigned sample_index = 0;
    for (unsigned cycle = 0; cycle < kSampleCount * kSamplePeriodCycles; ++cycle) {
      const bool sample_due = (cycle % kSamplePeriodCycles) == 0;
      const uint32_t value = sample_due
                                 ? static_cast<uint32_t>(neural_sample(sample_index))
                                 : 0;
      stream_cycle(sample_due, value);
      if (sample_due) ++sample_index;
    }

    for (int i = 0; i < 8; ++i) stream_cycle(false, 0);
    check(dut_.empty_o, "output FIFO should drain");
    check(received_ == expected_features(), "feature values or decimation cadence");

    // Leave one feature pending, then disable ESA and check that the output
    // FIFO and all filter state are cleared.
    for (unsigned i = 0; i < kDecimationRate; ++i) {
      const uint32_t value =
          static_cast<uint32_t>(neural_sample(kSampleCount + i));
      for (unsigned cycle = 0; cycle < kSamplePeriodCycles; ++cycle) {
        stream_cycle(cycle == 0, value, false);
      }
    }
    for (int i = 0; i < 3; ++i) stream_cycle(false, 0, false);
    check(!dut_.empty_o, "expected a pending feature before disabling");
    write_register(kEnableOffset, 0);
    check(dut_.empty_o, "disabling ESA should flush the output FIFO");

    std::printf("ESA neural stream PASS: %u samples, %u checked features, %u cycles\n",
                kSampleCount + kDecimationRate, static_cast<unsigned>(received_.size()),
                cycles_);
  }

 private:
  void tick_clock() {
    dut_.clk_i = 0;
    sample();
    dut_.clk_i = 1;
    sample();
  }

  void sample() {
    dut_.eval();
    trace_.dump(time_);
    time_ += 5000;  // VCD timescale is 1 ps: each half-cycle is 5 ns.
  }

  void check(bool condition, const char* what) const {
    if (!condition) {
      throw std::runtime_error("cycle " + std::to_string(cycles_) + ": " + what);
    }
  }

  static int32_t saturated_high_pass(int32_t input, int32_t previous) {
    int64_t difference = static_cast<int64_t>(input) - previous;
    if (difference > 0x7fffffffLL) difference = 0x7fffffffLL;
    if (difference < -0x7fffffffLL) difference = -0x7fffffffLL;
    return static_cast<int32_t>(difference);
  }

  static int32_t ses_average(int32_t input, int64_t* accumulator,
                             uint32_t window_shift, uint32_t gain_shift) {
    const int64_t scaled_input =
        static_cast<int64_t>(input) * (int64_t{1} << gain_shift);
    const int64_t previous_average = *accumulator >> window_shift;
    *accumulator += scaled_input - previous_average;
    return static_cast<int32_t>(*accumulator >> window_shift);
  }

  std::vector<uint32_t> expected_features() const {
    std::vector<uint32_t> expected;
    int32_t previous_input = 0;
    int64_t esa_accumulator = 0;
    int64_t feature_accumulator = 0;
    for (unsigned i = 0; i < kSampleCount; ++i) {
      const int32_t input = neural_sample(i);
      const int32_t hpf = saturated_high_pass(input, previous_input);
      previous_input = input;
      const int32_t magnitude = hpf < 0 ? -hpf : hpf;
      const int32_t esa = ses_average(magnitude, &esa_accumulator,
                                      kEsaWindowShift, kEsaGainShift);
      const int32_t feature = ses_average(esa, &feature_accumulator,
                                          kFeatureWindowShift,
                                          kFeatureGainShift);
      if ((i + 1) % kDecimationRate == 0) {
        expected.push_back(static_cast<uint32_t>(feature));
      }
    }
    return expected;
  }

  Vesa_tb dut_;
  VerilatedVcdC trace_;
  std::vector<uint32_t> received_;
  uint64_t time_ = 0;
  unsigned cycles_ = 0;
};
}  // namespace

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  const char* waveform = argc > 1 ? argv[1] : "esa.vcd";
  try {
    Testbench testbench(waveform);
    testbench.configure_and_run();
  } catch (const std::exception& error) {
    std::fprintf(stderr, "ESA neural stream FAIL: %s\n", error.what());
    return EXIT_FAILURE;
  }
  return EXIT_SUCCESS;
}
