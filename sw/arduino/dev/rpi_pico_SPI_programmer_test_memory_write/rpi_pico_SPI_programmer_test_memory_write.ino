#include <SPI.h>

#define PIN_MISO 16
#define PIN_CS 17
#define PIN_SCK 18
#define PIN_MOSI 19

#define SPI_FREQ 100000
#define MAX_DATA 512

#define SERIAL_TIMEOUT_MS 2000
#define SERIAL_STARTUP_TIMEOUT_MS 3000

arduino::MbedSPI spi(PIN_MISO, PIN_MOSI, PIN_SCK);

uint8_t dataBuffer[MAX_DATA];


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