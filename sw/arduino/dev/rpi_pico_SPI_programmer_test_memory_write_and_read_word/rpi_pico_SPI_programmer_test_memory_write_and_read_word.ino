#include <SPI.h>

#define PIN_MISO 16
#define PIN_CS 17
#define PIN_SCK 18
#define PIN_MOSI 19

#define SPI_FREQ 100000
#define MAX_DATA 512

arduino::MbedSPI spi(PIN_MISO, PIN_MOSI, PIN_SCK);

uint8_t dataBuffer[MAX_DATA];

void heepWriteWord(uint32_t address, uint32_t value) {
  spi.beginTransaction(SPISettings(SPI_FREQ, MSBFIRST, SPI_MODE0));
  digitalWrite(PIN_CS, LOW);

  // Wrap length = 1 word
  spi.transfer(0x20);
  spi.transfer(0x01);
  spi.transfer(0x30);
  spi.transfer(0x00);

  // WRITE command
  spi.transfer(0x02);

  // Address: MSB byte first
  spi.transfer((address >> 24) & 0xFF);
  spi.transfer((address >> 16) & 0xFF);
  spi.transfer((address >> 8) & 0xFF);
  spi.transfer((address >> 0) & 0xFF);

  // Data: MSB byte first
  spi.transfer((value >> 24) & 0xFF);
  spi.transfer((value >> 16) & 0xFF);
  spi.transfer((value >> 8) & 0xFF);
  spi.transfer((value >> 0) & 0xFF);

  digitalWrite(PIN_CS, HIGH);
  spi.endTransaction();
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

void heepWrite(uint32_t address, const uint8_t *data, uint16_t length) {
  uint16_t words = (length + 3) / 4;

  spi.beginTransaction(
    SPISettings(SPI_FREQ, MSBFIRST, SPI_MODE0));

  digitalWrite(PIN_CS, LOW);

  // Wrap length, in 32-bit words
  spi.transfer(0x20);
  spi.transfer(words & 0xFF);

  spi.transfer(0x30);
  spi.transfer((words >> 8) & 0xFF);

  // WRITE memory
  spi.transfer(0x02);

  // Address, little endian
  spi.transfer((address >> 0) & 0xFF);
  spi.transfer((address >> 8) & 0xFF);
  spi.transfer((address >> 16) & 0xFF);
  spi.transfer((address >> 24) & 0xFF);

  // Data
  for (uint32_t i = 0; i < (uint32_t)words * 4; i++) {
    spi.transfer(i < length ? data[i] : 0x00);
  }

  digitalWrite(PIN_CS, HIGH);

  spi.endTransaction();
}


bool readExact(uint8_t *dst, uint16_t length) {
  uint16_t received = 0;

  while (received < length) {
    if (Serial.available()) {
      dst[received++] = Serial.read();
    }
  }

  return true;
}

void testBootromRead() {
  spi.beginTransaction(SPISettings(SPI_FREQ, MSBFIRST, SPI_MODE0));

  digitalWrite(PIN_CS, LOW);

  // 31 dummy cycles
  spi.transfer(0x11);
  spi.transfer(0x1F);  // 31

  // 16 words = 64 bytes
  spi.transfer(0x20);
  spi.transfer(0x10);
  spi.transfer(0x30);
  spi.transfer(0x00);

  // READ
  spi.transfer(0x0B);

  // 0x20010000
  spi.transfer(0x20);
  spi.transfer(0x01);
  spi.transfer(0x00);
  spi.transfer(0x00);

  // 32 dummy clocks
  for (int i = 0; i < 4; i++)
    spi.transfer(0x00);

  // Clock out 64 bytes from MISO
  for (int i = 0; i < 64; i++)
    spi.transfer(0x00);

  digitalWrite(PIN_CS, HIGH);

  spi.endTransaction();
}

void testSram() {
  uint32_t address = 0x00007000;
  uint32_t written = 0x12345678;

  heepWriteWord(address, written);

  delayMicroseconds(100);

  uint32_t readback = heepReadWord(address);

  Serial.print("Written:  0x");
  Serial.println(written, HEX);

  Serial.print("Readback: 0x");
  Serial.println(readback, HEX);

  if (readback == written)
    Serial.println("SRAM TEST OK");
  else
    Serial.println("SRAM TEST FAILED");
}

void setup() {
  pinMode(PIN_CS, OUTPUT);
  digitalWrite(PIN_CS, HIGH);

  spi.begin();

  Serial.begin(115200);

  while (!Serial) {}

  Serial.println("HEEPidermis programmer ready");
}



void loop() {
  if (!Serial.available())
    return;

  char cmd = Serial.read();

  if (cmd == 't') {
    // testBootromRead();
    // heepWriteWord(0x00007000, 0x12345678);
    // delay(100);
    // heepReadWord(0x00007000);
    testSram();
  }
}