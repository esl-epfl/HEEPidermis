#include <Arduino.h>

#include "hardware/clocks.h"
#include "hardware/gpio.h"

// ------------------------------------------------------------
// ASIC clock configuration
// ------------------------------------------------------------

static constexpr uint ASIC_CLK_PIN = 21;  // GPIO21 = GPOUT0

static uint32_t requested_clk_hz = 1000000;  // Default: 1 MHz
static bool clk_enabled = false;


// ------------------------------------------------------------
// Clock control
// ------------------------------------------------------------

bool clockApply(uint32_t frequency_hz) {
  if (frequency_hz == 0) {
    return false;
  }

  uint32_t pll_sys_hz =
    frequency_count_khz(
      CLOCKS_FC0_SRC_VALUE_PLL_SYS_CLKSRC_PRIMARY)
    * 1000;

  if (pll_sys_hz == 0 || frequency_hz > pll_sys_hz) {
    return false;
  }

  bool ok = clock_configure(
    clk_gpout0,
    0,
    CLOCKS_CLK_GPOUT0_CTRL_AUXSRC_VALUE_CLKSRC_PLL_SYS,
    pll_sys_hz,
    frequency_hz);

  if (!ok) {
    return false;
  }

  gpio_set_function(ASIC_CLK_PIN, GPIO_FUNC_GPCK);

  // Does not do anything
  
  // gpio_set_slew_rate(
  //   ASIC_CLK_PIN,
  //   GPIO_SLEW_RATE_FAST);

  // gpio_set_drive_strength(
  //   ASIC_CLK_PIN,
  //   GPIO_DRIVE_STRENGTH_12MA);

  return true;
}

bool clockOn() {
  if (!clockApply(requested_clk_hz)) {
    return false;
  }

  clk_enabled = true;
  return true;
}


void clockOff() {
  // Stop the actual clock generator.
  clock_stop(clk_gpout0);

  /*
     * Disconnect GPOUT from the pin and explicitly drive
     * the ASIC clock LOW.
     */
  gpio_set_function(ASIC_CLK_PIN, GPIO_FUNC_SIO);
  gpio_set_dir(ASIC_CLK_PIN, GPIO_OUT);
  gpio_put(ASIC_CLK_PIN, 0);

  clk_enabled = false;
}


bool clockSetFrequency(uint32_t frequency_hz) {
  if (frequency_hz == 0) {
    return false;
  }

  uint32_t source_hz = clock_get_hz(clk_sys);

  if (frequency_hz > source_hz) {
    return false;
  }

  requested_clk_hz = frequency_hz;

  /*
     * If the clock is already running, change frequency
     * immediately.
     *
     * If it is OFF, only remember the new value.
     */
  if (clk_enabled) {
    return clockApply(requested_clk_hz);
  }

  return true;
}


// ------------------------------------------------------------
// Serial command handling
// ------------------------------------------------------------

void handleCommand(String command) {
  command.trim();

  if (command == "C ON") {

    if (clockOn()) {
      Serial.print("OK CLK ON ");
      Serial.print(clock_get_hz(clk_gpout0));
      Serial.println(" Hz");
    } else {
      Serial.println("ERR CLK ON");
    }

    return;
  }


  if (command == "C OFF") {

    clockOff();

    Serial.println("OK CLK OFF");
    return;
  }


  if (command == "C GET") {

    Serial.print("CLK ");

    if (clk_enabled) {
      Serial.print("ON ");
      Serial.print(clock_get_hz(clk_gpout0));
    } else {
      Serial.print("OFF ");
      Serial.print(requested_clk_hz);
    }

    Serial.println(" Hz");

    return;
  }


  if (command.startsWith("C SET ")) {

    String freqString = command.substring(6);
    uint32_t frequency = freqString.toInt();

    if (frequency == 0) {
      Serial.println("ERR BAD FREQUENCY");
      return;
    }

    if (!clockSetFrequency(frequency)) {
      Serial.println("ERR CLK SET");
      return;
    }

    Serial.print("OK CLK SET ");

    if (clk_enabled) {
      Serial.print(clock_get_hz(clk_gpout0));
    } else {
      Serial.print(requested_clk_hz);
    }

    Serial.println(" Hz");

    return;
  }


  if (command == "HELP") {

    Serial.println("Commands:");
    Serial.println("  C SET <Hz>");
    Serial.println("  C ON");
    Serial.println("  C OFF");
    Serial.println("  C GET");

    return;
  }


  Serial.println("ERR UNKNOWN COMMAND");
}


// ------------------------------------------------------------
// Arduino
// ------------------------------------------------------------

void setup() {
  Serial.begin(115200);

  // Start with ASIC clock safely LOW.
  clockOff();

  delay(500);

  Serial.println();
  Serial.println("Pico ASIC clock test");
  Serial.println("GPIO21 = ASIC clock output");
  Serial.println("Type HELP for commands");
}


void loop() {
  if (Serial.available()) {

    String command = Serial.readStringUntil('\n');

    handleCommand(command);
  }
}