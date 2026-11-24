#include <Arduino.h>
#include <SPI.h>
#include "hardware/clocks.h"
#include "hardware/gpio.h"

// HEEPidermis Raspberry Pi Pico programmer and clock source.
// PC protocol: X reset; W/R + <u32 LE address> + <u16 LE length> [+ W data];
// C1 clock on; C0 clock off; CF + <u32 LE Hz> set frequency;
// C? status;
// V enable VCOp, verify, then clock off.

#define PIN_MISO 16
#define PIN_CS 17
#define PIN_SCK 18
#define PIN_MOSI 19

#define PIN_LED_SERIAL 13
#define PIN_LED_SPI_WRITE 14
#define PIN_LED_SPI_READ 15

#define PIN_ASIC_CLK 21  // GPIO21 = GPOUT0
// Clock output is a resistive divider: GP21 -R1- node -R2- GP8. GP8 is the
// bottom of the divider: LOW while clocking, high-Z (no pulls) otherwise.
#define PIN_ASIC_CLK_REF 8

#define SPI_FREQ 100000u
#define MAX_DATA 512u
#define SERIAL_TIMEOUT_MS 2000u
#define SERIAL_TX_PROGRESS_TIMEOUT_MS 2500u
#define SERIAL_TX_PACKET_SIZE 16u
#define SERIAL_STARTUP_TIMEOUT_MS 3000u
#define LED_BLINK_HALF_PERIOD_MS 90u
#define LED_ACTIVITY_HOLD_MS 200u

// Clock policy. Frequency changes while running are prohibited by default.
#define ALLOW_LIVE_CLOCK_FREQUENCY_CHANGE 0
#define DEFAULT_ASIC_CLOCK_HZ 1000000u
// Keep the clock running on startup for compatibility with programmer.py.
// Set to 0 for a clock-off-on-startup policy (requires C1 before SPI use).
#define CLOCK_ON_AT_STARTUP 0

arduino::MbedSPI spi(PIN_MISO, PIN_MOSI, PIN_SCK);
uint8_t dataBuffer[MAX_DATA];
uint32_t requestedClockHz = DEFAULT_ASIC_CLOCK_HZ;
bool clockEnabled = false;

struct ActivityLed {
  uint8_t pin;
  uint32_t startedMs;
  uint32_t lastActivityMs;
  bool seen;
  bool outputHigh;
};
ActivityLed serialLed = {PIN_LED_SERIAL, 0, 0, false, false};
ActivityLed spiWriteLed = {PIN_LED_SPI_WRITE, 0, 0, false, false};
ActivityLed spiReadLed = {PIN_LED_SPI_READ, 0, 0, false, false};

void updateActivityLed(ActivityLed &led) {
  uint32_t now = millis();
  bool recent = led.seen &&
    (uint32_t)(now - led.lastActivityMs) < LED_ACTIVITY_HOLD_MS;
  bool high = recent &&
    (((uint32_t)(now - led.startedMs) / LED_BLINK_HALF_PERIOD_MS) % 2u == 0u);
  if (high != led.outputHigh) {
    digitalWrite(led.pin, high ? HIGH : LOW);
    led.outputHigh = high;
  }
}
void updateActivityLeds() {
  updateActivityLed(serialLed);
  updateActivityLed(spiWriteLed);
  updateActivityLed(spiReadLed);
}
void markActivity(ActivityLed &led) {
  uint32_t now = millis();
  if (!led.seen ||
      (uint32_t)(now - led.lastActivityMs) >= LED_ACTIVITY_HOLD_MS)
    led.startedMs = now;
  led.seen = true;
  led.lastActivityMs = now;
  updateActivityLeds();
}

// Complete binary USB responses even when the CDC transmit buffer fills.
bool serialWriteAll(const uint8_t *data, size_t length) {
  size_t sent = 0;
  uint32_t lastProgress = millis();
  while (sent < length) {
    size_t n = length - sent;
    if (n > SERIAL_TX_PACKET_SIZE) n = SERIAL_TX_PACKET_SIZE;
    size_t written = Serial.write(data + sent, n);
    if (written) {
      sent += written;
      lastProgress = millis();
      markActivity(serialLed);
      delay(0);
    } else {
      if ((uint32_t)(millis() - lastProgress) >= SERIAL_TX_PROGRESS_TIMEOUT_MS)
        return false;  // No ASCII error inside an unfinished binary frame.
      updateActivityLeds();
      delay(1);
    }
  }
  return true;
}

bool readExact(uint8_t *dst, uint16_t length, uint32_t timeoutMs) {
  uint16_t received = 0;
  uint32_t lastByte = millis();
  while (received < length) {
    updateActivityLeds();
    if (Serial.available() > 0) {
      dst[received++] = (uint8_t)Serial.read();
      lastByte = millis();
    }
    if ((uint32_t)(millis() - lastByte) >= timeoutMs) return false;
  }
  return true;
}

// Clock output uses the same RP2040 GPOUT0 implementation as test_pico_clock.
// Input, no pulls, output latch LOW. Direction is cleared before the function
// changes so the pin is never driven while being re-muxed.
void gpioHighZ(uint8_t pin) {
  gpio_set_dir(pin, GPIO_IN);
  gpio_put(pin, 0);
  gpio_set_function(pin, GPIO_FUNC_SIO);
  gpio_disable_pulls(pin);
}
// 12 mA drive on both divider pins, fast slew on the clock pin.
void clockApplyPadConfig() {
  gpio_set_drive_strength(PIN_ASIC_CLK, GPIO_DRIVE_STRENGTH_12MA);
  gpio_set_drive_strength(PIN_ASIC_CLK_REF, GPIO_DRIVE_STRENGTH_12MA);
  gpio_set_slew_rate(PIN_ASIC_CLK, GPIO_SLEW_RATE_FAST);
}

// Off: GP21 and GP8 both high-Z with no pulls, never outputs.
void clockOff() {
  clock_stop(clk_gpout0);
  gpioHighZ(PIN_ASIC_CLK);
  gpioHighZ(PIN_ASIC_CLK_REF);
  clockEnabled = false;
}
bool clockApply(uint32_t hz) {
  if (!hz) return false;
  uint32_t pllKHz = frequency_count_khz(
    CLOCKS_FC0_SRC_VALUE_PLL_SYS_CLKSRC_PRIMARY);
  if (!pllKHz) return false;
  uint32_t pllHz = pllKHz * 1000u;
  if (hz > pllHz) return false;
  bool ok = clock_configure(clk_gpout0, 0,
    CLOCKS_CLK_GPOUT0_CTRL_AUXSRC_VALUE_CLKSRC_PLL_SYS,
    pllHz, hz);
  if (!ok) return false;
  gpio_set_function(PIN_ASIC_CLK, GPIO_FUNC_GPCK);
  return true;
}
bool clockOn() {
  if (clockEnabled) return true;
  // Bottom of the divider LOW first, then route GPOUT0 to GP21.
  gpio_put(PIN_ASIC_CLK_REF, 0);
  gpio_set_function(PIN_ASIC_CLK_REF, GPIO_FUNC_SIO);
  gpio_set_dir(PIN_ASIC_CLK_REF, GPIO_OUT);
  if (!clockApply(requestedClockHz)) {
    clockOff();
    return false;
  }
  clockEnabled = true;
  return true;
}
bool clockSetFrequency(uint32_t hz) {
  if (!hz || hz > clock_get_hz(clk_sys)) return false;
#if !ALLOW_LIVE_CLOCK_FREQUENCY_CHANGE
  if (clockEnabled) return false;
#endif
  if (clockEnabled) {
    // Only available if live frequency changes are explicitly enabled.
    if (!clockApply(hz)) return false;
  }
  requestedClockHz = hz;
  return true;
}
void clockStatus() {
  Serial.print("CLK ");
  Serial.print(clockEnabled ? "ON " : "OFF ");
  Serial.print(clockEnabled ? clock_get_hz(clk_gpout0) : requestedClockHz);
  Serial.println(" Hz");
}

static constexpr uint32_t SOC_CTRL_BASE = 0x20000000u;
static constexpr uint32_t BOOT_EXIT_LOOP = SOC_CTRL_BASE + 0x0Cu;
static constexpr uint32_t PM_BASE = 0x20040000u;
static constexpr uint32_t PM_WAKEUP_STATE = PM_BASE + 0x00u;
static constexpr uint32_t PM_ASSERT_COUNT = PM_BASE + 0x1Cu;
static constexpr uint32_t PM_DEASSERT_COUNT = PM_BASE + 0x20u;
static constexpr uint32_t PM_MONITOR_POWER_GATE_CORE = PM_BASE + 0x8Cu;
static constexpr uint32_t PM_CPU_RESET_ASSERT = PM_BASE + 0xA4u;
static constexpr uint32_t PM_CPU_RESET_DEASSERT = PM_BASE + 0xA8u;
static constexpr uint32_t CPU_RST_N_MASK = 1u << 2;
// config/cheep_configs.hjson: AO external peripherals + VCO decoder + ENABLE.
static constexpr uint32_t VCO_ENABLE = 0x20070000u + 0x1000u + 0x0Cu;
static constexpr uint32_t VCOP_ENABLE_MASK = 1u << 0;

bool heepWrite(uint32_t address, const uint8_t *data, uint16_t length) {
  if (!length || (address & 3u)) return false;
  uint16_t words = (length + 3u) / 4u;
  uint32_t padded = (uint32_t)words * 4u;
  if (address > UINT32_MAX - (padded - 1u)) return false;
  markActivity(spiWriteLed);
  spi.beginTransaction(SPISettings(SPI_FREQ, MSBFIRST, SPI_MODE0));
  digitalWrite(PIN_CS, LOW);
  spi.transfer(0x20);
  spi.transfer(words & 0xFF);
  spi.transfer(0x30);
  spi.transfer((words >> 8) & 0xFF);
  spi.transfer(0x02);
  for (int i = 24; i >= 0; i -= 8) spi.transfer((address >> i) & 0xFF);
  for (uint16_t w = 0; w < words; ++w) {
    uint32_t b = (uint32_t)w * 4u;
    for (int i = 3; i >= 0; --i)
      spi.transfer(b + (uint32_t)i < length ? data[b + i] : 0x00);
    if ((w & 7u) == 0) updateActivityLeds();
  }
  digitalWrite(PIN_CS, HIGH);
  spi.endTransaction();
  return true;
}
bool heepWriteWord(uint32_t address, uint32_t value) {
  uint8_t data[4] = {(uint8_t)value, (uint8_t)(value >> 8),
                     (uint8_t)(value >> 16), (uint8_t)(value >> 24)};
  return heepWrite(address, data, 4);
}
uint32_t heepReadWord(uint32_t address) {
  markActivity(spiReadLed);
  spi.beginTransaction(SPISettings(SPI_FREQ, MSBFIRST, SPI_MODE0));
  digitalWrite(PIN_CS, LOW);
  spi.transfer(0x11);
  spi.transfer(0x1F); // 32 dummy clocks: slave register is inclusive.
  spi.transfer(0x20);
  spi.transfer(0x01);
  spi.transfer(0x30);
  spi.transfer(0x00);
  spi.transfer(0x0B);
  for (int i = 24; i >= 0; i -= 8) spi.transfer((address >> i) & 0xFF);
  for (int i = 0; i < 4; ++i) spi.transfer(0x00);
  uint32_t result = 0;
  for (int i = 0; i < 4; ++i) result = (result << 8) | spi.transfer(0x00);
  digitalWrite(PIN_CS, HIGH);
  spi.endTransaction();
  return result;
}
bool heepReadBlock(uint32_t address, uint8_t *dst, uint16_t length) {
  if (!length || length > MAX_DATA || (length & 3u) || (address & 3u) ||
      address > UINT32_MAX - ((uint32_t)length - 1u)) return false;
  for (uint16_t i = 0; i < length; i += 4) {
    uint32_t word = heepReadWord(address + i);
    for (int b = 0; b < 4; ++b) dst[i + b] = (uint8_t)(word >> (8 * b));
  }
  return true;
}
bool waitForCpuResetState(bool released, uint32_t timeoutMs = 100u) {
  uint32_t start = millis();
  while ((uint32_t)(millis() - start) < timeoutMs) {
    if (((heepReadWord(PM_MONITOR_POWER_GATE_CORE) & CPU_RST_N_MASK) != 0)
        == released) return true;
  }
  return false;
}
// CPU-domain reset to Boot ROM; not a physical reset of the whole board.
bool heepResetCpuToBootRom() {
  if (!heepWriteWord(BOOT_EXIT_LOOP, 0u)) return false;
  if (!heepWriteWord(PM_WAKEUP_STATE, 0u)) return false;
  if (!heepWriteWord(PM_ASSERT_COUNT, 5u)) return false;
  if (!heepWriteWord(PM_DEASSERT_COUNT, 5u)) return false;
  if (!heepWriteWord(PM_CPU_RESET_ASSERT, 1u)) return false;
  if (!waitForCpuResetState(false)) return false;
  if (!heepWriteWord(PM_CPU_RESET_ASSERT, 0u)) return false;
  if (!heepWriteWord(PM_CPU_RESET_DEASSERT, 1u)) return false;
  if (!waitForCpuResetState(true)) return false;
  if (!heepWriteWord(PM_CPU_RESET_DEASSERT, 0u)) return false;
  return true;
}
// Keep the CPU in Boot ROM: firmware could race this read-modify-write.
// An enabled VCOp is never toggled. The clock stays on if verification fails.
bool heepEnableVcopAndStopClock(uint32_t &readback) {
  uint32_t before = heepReadWord(VCO_ENABLE);
  uint32_t expected = before | VCOP_ENABLE_MASK;
  if (expected != before && !heepWriteWord(VCO_ENABLE, expected)) return false;
  readback = heepReadWord(VCO_ENABLE);
  if (readback != expected) return false;
  clockOff();
  return true;
}

void handleClockCommand() {
  uint8_t op;
  if (!readExact(&op, 1, SERIAL_TIMEOUT_MS)) {
    Serial.println("ERR_TIMEOUT_CLOCK");
    return;
  }
  if (op == '1') {
    if (clockOn()) { Serial.println("OK CLK ON"); }
    else Serial.println("ERR CLK ON");
  } else if (op == '0') {
    clockOff();
    Serial.println("OK CLK OFF");
  } else if (op == '?') {
    clockStatus();
  } else if (op == 'F') {
    uint8_t b[4];
    if (!readExact(b, 4, SERIAL_TIMEOUT_MS)) {
      Serial.println("ERR_TIMEOUT_CLOCK");
      return;
    }
    uint32_t hz = (uint32_t)b[0] | ((uint32_t)b[1] << 8) |
                  ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
#if !ALLOW_LIVE_CLOCK_FREQUENCY_CHANGE
    if (clockEnabled) { Serial.println("ERR CLK RUNNING"); return; }
#endif
    if (!clockSetFrequency(hz)) { Serial.println("ERR CLK SET"); return; }
    Serial.print("OK CLK SET ");
    Serial.print(clockEnabled ? clock_get_hz(clk_gpout0) : requestedClockHz);
    Serial.println(" Hz");
  } else {
    Serial.println("ERR CLOCK CMD");
  }
}

void setup() {
  pinMode(PIN_LED_SERIAL, OUTPUT);
  pinMode(PIN_LED_SPI_WRITE, OUTPUT);
  pinMode(PIN_LED_SPI_READ, OUTPUT);
  digitalWrite(PIN_LED_SERIAL, LOW);
  digitalWrite(PIN_LED_SPI_WRITE, LOW);
  digitalWrite(PIN_LED_SPI_READ, LOW);
  pinMode(PIN_CS, OUTPUT);
  digitalWrite(PIN_CS, HIGH);
  clockApplyPadConfig();
  clockOff();  // GP21 and GP8 high-Z.
  spi.begin();
#if CLOCK_ON_AT_STARTUP
  // If this fails, leave GPOUT disabled; host can inspect with C?.
  clockOn();
#endif
  Serial.begin(115200);
  uint32_t start = millis();
  while (!Serial && (uint32_t)(millis() - start) < SERIAL_STARTUP_TIMEOUT_MS) {}
  Serial.println("HEEPidermis programmer ready");
}

void loop() {
  updateActivityLeds();
  if (Serial.available() < 1) return;
  char command = (char)Serial.peek();
  if (command == 'X') {
    Serial.read();
    markActivity(serialLed);
    Serial.println(heepResetCpuToBootRom() ? "OK" : "ERR_RESET");
    return;
  }
  if (command == 'C') {
    Serial.read();
    markActivity(serialLed);
    handleClockCommand();
    return;
  }
  if (command == 'V') {
    Serial.read();
    markActivity(serialLed);
    uint32_t readback = 0;
    bool ok = heepEnableVcopAndStopClock(readback);
    Serial.print(ok ? "OK VCO " : "ERR_VCO ");
    Serial.println(readback, HEX);
    return;
  }
  // Preserve the pre-existing binary protocol for W and R.
  if (Serial.available() < 7) return;
  uint8_t header[7];
  if (!readExact(header, sizeof(header), SERIAL_TIMEOUT_MS)) {
    Serial.println("ERR_TIMEOUT_HEADER"); return;
  }
  markActivity(serialLed);
  if (header[0] != 'W' && header[0] != 'R') {
    Serial.println("ERR_CMD"); return;
  }
  uint32_t address = (uint32_t)header[1] | ((uint32_t)header[2] << 8) |
                     ((uint32_t)header[3] << 16) | ((uint32_t)header[4] << 24);
  uint16_t length = (uint16_t)header[5] | ((uint16_t)header[6] << 8);
  if (!length || length > MAX_DATA) { Serial.println("ERR_LENGTH"); return; }
  if (address & 3u) { Serial.println("ERR_ALIGN"); return; }
  uint32_t padded = (((uint32_t)length + 3u) / 4u) * 4u;
  if (address > UINT32_MAX - (padded - 1u)) {
    Serial.println("ERR_RANGE"); return;
  }
  if (header[0] == 'R') {
    if (length & 3u) { Serial.println("ERR_LENGTH_ALIGN"); return; }
    if (!heepReadBlock(address, dataBuffer, length)) {
      Serial.println("ERR_READ"); return;
    }
    const uint8_t dataHeader[] = {'D','A','T','A','\r','\n'};
    if (!serialWriteAll(dataHeader, sizeof(dataHeader))) return;
    serialWriteAll(dataBuffer, length);
    return;
  }
  if (!readExact(dataBuffer, length, SERIAL_TIMEOUT_MS)) {
    Serial.println("ERR_TIMEOUT_DATA"); return;
  }
  if (!heepWrite(address, dataBuffer, length)) {
    Serial.println("ERR_WRITE"); return;
  }
  markActivity(serialLed);
  // OK confirms transmission only. Use programmer.py --verify for readback.
  Serial.println("OK");
}
