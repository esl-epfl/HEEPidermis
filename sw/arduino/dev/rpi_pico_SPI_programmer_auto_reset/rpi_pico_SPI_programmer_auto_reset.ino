#include <SPI.h>

// Raspbery Pi Pico chosen SPI connections
#define PIN_MISO 16
#define PIN_CS 17
#define PIN_SCK 18
#define PIN_MOSI 19

#define SPI_FREQ 100000
#define MAX_DATA 512

#define SERIAL_TIMEOUT_MS 2000
#define SERIAL_STARTUP_TIMEOUT_MS 3000

#define RESET_DEBUG 0

#if RESET_DEBUG
#define RESET_DBG_PRINT(...) Serial.print(__VA_ARGS__)
#define RESET_DBG_PRINTLN(...) Serial.println(__VA_ARGS__)
#else
#define RESET_DBG_PRINT(...)
#define RESET_DBG_PRINTLN(...)
#endif

arduino::MbedSPI spi(PIN_MISO, PIN_MOSI, PIN_SCK);

uint8_t dataBuffer[MAX_DATA];

// Reset CPU from SPI registers
static constexpr uint32_t SOC_CTRL_BASE = 0x20000000u;
static constexpr uint32_t SOC_CTRL_BOOT_EXIT_LOOP = SOC_CTRL_BASE + 0x0Cu;
static constexpr uint32_t POWER_MANAGER_BASE = 0x20040000u;
static constexpr uint32_t PM_MONITOR_POWER_GATE_CORE = POWER_MANAGER_BASE + 0x8Cu;
static constexpr uint32_t PM_CPU_RESET_ASSERT = POWER_MANAGER_BASE + 0xA4u;
static constexpr uint32_t PM_CPU_RESET_DEASSERT = POWER_MANAGER_BASE + 0xA8u;
static constexpr uint32_t CPU_RST_N_MASK = (1u << 2);
static constexpr uint32_t PM_WAKEUP_STATE = POWER_MANAGER_BASE + 0x00u;
static constexpr uint32_t PM_CPU_RESET_ASSERT_COUNTER = POWER_MANAGER_BASE + 0x1Cu;
static constexpr uint32_t PM_CPU_RESET_DEASSERT_COUNTER = POWER_MANAGER_BASE + 0x20u;
static constexpr uint32_t PM_CPU_WAIT_ACK_SWITCH_ON = POWER_MANAGER_BASE + 0x2Cu;
static constexpr uint32_t PM_CPU_COUNTERS_STOP = POWER_MANAGER_BASE + 0x38u;

bool heepWrite(uint32_t address, const uint8_t *data, uint16_t length) {
  // SPI slave performs 32-bit word writes.
  if (address & 0x3) {
    return false;
  }

  uint16_t words = (length + 3) / 4;
  uint32_t padded_length = (uint32_t)words * 4;

  // Prevent 32-bit address wraparound.
  if (address > UINT32_MAX - (padded_length - 1)) {
    return false;
  }

  spi.beginTransaction(
    SPISettings(SPI_FREQ, MSBFIRST, SPI_MODE0));

  digitalWrite(PIN_CS, LOW);

  // Wrap length, in 32-bit words.
  spi.transfer(0x20);
  spi.transfer(words & 0xFF);

  spi.transfer(0x30);
  spi.transfer((words >> 8) & 0xFF);

  // WRITE memory command.
  spi.transfer(0x02);

  // Address: MSB byte first on the SPI wire.
  spi.transfer((address >> 24) & 0xFF);
  spi.transfer((address >> 16) & 0xFF);
  spi.transfer((address >> 8) & 0xFF);
  spi.transfer((address >> 0) & 0xFF);

  /*
   * ELF / CPU memory is little-endian.
   *
   * Example:
   *   memory bytes: 78 56 34 12
   *   word:         0x12345678
   *
   * SPI slave shifts a 32-bit word MSB-first, so send:
   *   12 34 56 78
   */
  for (uint16_t w = 0; w < words; w++) {
    uint32_t base = (uint32_t)w * 4;

    uint8_t b0 = (base + 0 < length) ? data[base + 0] : 0x00;
    uint8_t b1 = (base + 1 < length) ? data[base + 1] : 0x00;
    uint8_t b2 = (base + 2 < length) ? data[base + 2] : 0x00;
    uint8_t b3 = (base + 3 < length) ? data[base + 3] : 0x00;

    spi.transfer(b3);
    spi.transfer(b2);
    spi.transfer(b1);
    spi.transfer(b0);
  }

  digitalWrite(PIN_CS, HIGH);
  spi.endTransaction();

  return true;
}

// Helpers to write into Reset related registers
bool heepWriteWord(uint32_t address, uint32_t value) {
  uint8_t data[4] = {
    (uint8_t)(value >> 0),
    (uint8_t)(value >> 8),
    (uint8_t)(value >> 16),
    (uint8_t)(value >> 24)
  };

  return heepWrite(address, data, sizeof(data));
}

uint32_t heepReadWord(uint32_t address) {
  spi.beginTransaction(SPISettings(SPI_FREQ, MSBFIRST, SPI_MODE0));
  digitalWrite(PIN_CS, LOW);

  // We want 32 physical dummy clocks.
  // Slave counter is inclusive, so register = 31.
  spi.transfer(0x11);
  spi.transfer(0x1F);

  // Wrap length = 1 word
  spi.transfer(0x20);
  spi.transfer(0x01);
  spi.transfer(0x30);
  spi.transfer(0x00);

  // READ command
  spi.transfer(0x0B);

  // Address: MSB byte first
  spi.transfer((address >> 24) & 0xFF);
  spi.transfer((address >> 16) & 0xFF);
  spi.transfer((address >> 8) & 0xFF);
  spi.transfer((address >> 0) & 0xFF);

  // Exactly 32 physical dummy clocks
  for (int i = 0; i < 4; i++)
    spi.transfer(0x00);

  // Slave sends the 32-bit OBI word MSB first
  uint8_t b0 = spi.transfer(0x00);
  uint8_t b1 = spi.transfer(0x00);
  uint8_t b2 = spi.transfer(0x00);
  uint8_t b3 = spi.transfer(0x00);

  digitalWrite(PIN_CS, HIGH);
  spi.endTransaction();

  return ((uint32_t)b0 << 24) | ((uint32_t)b1 << 16) | ((uint32_t)b2 << 8) | ((uint32_t)b3 << 0);
}

bool readExact(uint8_t *dst, uint16_t length, uint32_t timeout_ms) {
  uint16_t received = 0;
  uint32_t last_byte_time = millis();

  while (received < length) {

    if (Serial.available() > 0) {
      dst[received++] = Serial.read();

      // Reset timeout every time progress is made.
      last_byte_time = millis();
    }

    if ((uint32_t)(millis() - last_byte_time) >= timeout_ms) {
      return false;
    }
  }

  return true;
}

bool waitForCpuResetState(bool released, uint32_t timeout_ms = 100) {
  uint32_t start = millis();

  while ((uint32_t)(millis() - start) < timeout_ms) {
    uint32_t value = heepReadWord(PM_MONITOR_POWER_GATE_CORE);

    bool cpu_rst_n = (value & CPU_RST_N_MASK) != 0;

    if (cpu_rst_n == released) {
      return true;
    }
  }

  return false;
}
bool heepResetCpuToBootRom() {
  RESET_DBG_PRINTLN("RESET: clear BOOT_EXIT_LOOP");
  if (!heepWriteWord(SOC_CTRL_BOOT_EXIT_LOOP, 0x00000000u)) {
    RESET_DBG_PRINTLN("RESET FAIL: BOOT_EXIT_LOOP");
    return false;
  }

  RESET_DBG_PRINTLN("RESET: clear WAKEUP_STATE");
  if (!heepWriteWord(PM_WAKEUP_STATE, 0x00000000u)) {
    RESET_DBG_PRINTLN("RESET FAIL: WAKEUP_STATE");
    return false;
  }

  RESET_DBG_PRINTLN("RESET: set assert counter");
  if (!heepWriteWord(PM_CPU_RESET_ASSERT_COUNTER, 5u)) {
    RESET_DBG_PRINTLN("RESET FAIL: ASSERT_COUNTER");
    return false;
  }

  RESET_DBG_PRINTLN("RESET: set deassert counter");
  if (!heepWriteWord(PM_CPU_RESET_DEASSERT_COUNTER, 5u)) {
    RESET_DBG_PRINTLN("RESET FAIL: DEASSERT_COUNTER");
    return false;
  }

  RESET_DBG_PRINTLN("RESET: assert CPU reset");
  if (!heepWriteWord(PM_CPU_RESET_ASSERT, 1u)) {
    RESET_DBG_PRINTLN("RESET FAIL: ASSERT_WRITE");
    return false;
  }

  RESET_DBG_PRINTLN("RESET: waiting for rst_n = 0");
  if (!waitForCpuResetState(false)) {
    uint32_t v = heepReadWord(PM_MONITOR_POWER_GATE_CORE);
    RESET_DBG_PRINT("RESET FAIL: rst_n did not go low, monitor=0x");
    RESET_DBG_PRINTLN(v, HEX);
    return false;
  }

  RESET_DBG_PRINTLN("RESET: clear assert request");
  if (!heepWriteWord(PM_CPU_RESET_ASSERT, 0u)) {
    RESET_DBG_PRINTLN("RESET FAIL: ASSERT_CLEAR");
    return false;
  }

  RESET_DBG_PRINTLN("RESET: deassert CPU reset");
  if (!heepWriteWord(PM_CPU_RESET_DEASSERT, 1u)) {
    RESET_DBG_PRINTLN("RESET FAIL: DEASSERT_WRITE");
    return false;
  }

  RESET_DBG_PRINTLN("RESET: waiting for rst_n = 1");
  if (!waitForCpuResetState(true)) {
    uint32_t v = heepReadWord(PM_MONITOR_POWER_GATE_CORE);
    RESET_DBG_PRINT("RESET FAIL: rst_n did not go high, monitor=0x");
    RESET_DBG_PRINTLN(v, HEX);
    return false;
  }

  RESET_DBG_PRINTLN("RESET: clear deassert request");
  if (!heepWriteWord(PM_CPU_RESET_DEASSERT, 0u)) {
    RESET_DBG_PRINTLN("RESET FAIL: DEASSERT_CLEAR");
#if RESET_DEBUG
    resetDebugReg("MONITOR_POWER_GATE_CORE",
                  PM_MONITOR_POWER_GATE_CORE);

    resetDebugReg("RESET_DEASSERT_COUNTER",
                  PM_CPU_RESET_DEASSERT_COUNTER);

    resetDebugReg("RESET_DEASSERT_REQUEST",
                  PM_CPU_RESET_DEASSERT);

    resetDebugReg("CPU_COUNTERS_STOP",
                  PM_CPU_COUNTERS_STOP);

    resetDebugReg("CPU_WAIT_ACK_SWITCH_ON",
                  PM_CPU_WAIT_ACK_SWITCH_ON);
#endif
    return false;
  }

  RESET_DBG_PRINTLN("RESET: success");
  return true;
}

#if RESET_DEBUG
void resetDebugReg(const char *name, uint32_t address) {
  uint32_t value = heepReadWord(address);
  Serial.print(name);
  Serial.print(" = 0x");
  Serial.println(value, HEX);
}
#endif

void setup() {
  pinMode(PIN_CS, OUTPUT);
  digitalWrite(PIN_CS, HIGH);

  spi.begin();

  Serial.begin(115200);

  // Do not block forever if no USB host connects.
  uint32_t start = millis();

  while (!Serial && (uint32_t)(millis() - start) < SERIAL_STARTUP_TIMEOUT_MS) {
  }

  Serial.println("HEEPidermis programmer ready");
}


void loop() {
  if (Serial.available() < 1)
    return;

  char command = Serial.peek();

  // CPU reset to Boot ROM.
  // This command is a single byte: 'X'
  if (command == 'X') {
    Serial.read();  // consume command byte

    if (heepResetCpuToBootRom()) {
      Serial.println("OK");
    } else {
      Serial.println("ERR_RESET");
    }

    return;
  }

  // Header is exactly 7 bytes.
  if (Serial.available() < 7)
    return;

  /*
   * PC -> Pico header:
   *
   * [0]      command = 'W'
   * [1..4]   address, little endian
   * [5..6]   length, little endian
   *
   * This byte order belongs only to the USB serial protocol.
   * heepWrite() converts address/data to the SPI slave wire format.
   */

  uint8_t header[7];

  if (!readExact(header, sizeof(header), SERIAL_TIMEOUT_MS)) {
    Serial.println("ERR_TIMEOUT_HEADER");
    return;
  }

  if (header[0] != 'W') {
    Serial.println("ERR_CMD");
    return;
  }

  uint32_t address =
    ((uint32_t)header[1]) | ((uint32_t)header[2] << 8) | ((uint32_t)header[3] << 16) | ((uint32_t)header[4] << 24);

  uint16_t length =
    ((uint16_t)header[5]) | ((uint16_t)header[6] << 8);

  if (length == 0 || length > MAX_DATA) {
    Serial.println("ERR_LENGTH");
    return;
  }

  if (address & 0x3) {
    Serial.println("ERR_ALIGN");
    return;
  }

  uint32_t padded_length =
    ((uint32_t)(length + 3) / 4) * 4;

  if (address > UINT32_MAX - (padded_length - 1)) {
    Serial.println("ERR_RANGE");
    return;
  }

  if (!readExact(dataBuffer, length, SERIAL_TIMEOUT_MS)) {
    Serial.println("ERR_TIMEOUT_DATA");
    return;
  }

  if (!heepWrite(address, dataBuffer, length)) {
    Serial.println("ERR_WRITE");
    return;
  }

  /*
   * IMPORTANT:
   * "OK" means that the Pico successfully transmitted the SPI transaction.
   * It does NOT currently mean that the ASIC contents were read back
   * and verified.
   */
  Serial.println("OK");
}