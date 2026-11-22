// Pololu S7V8F3 test: the XIAO ESP32-C6 as a voltmeter (no multimeter needed).
//
// Wiring (XIAO on USB, battery feeds ONLY the Pololu):
//   battery+ -> Pololu VIN, battery- -> Pololu GND -> XIAO GND (e.g. a GND socket on the e-paper board)
//   Pololu VOUT -> R -> D0 -> R -> GND      (two EQUAL resistors, any value 220 ohm .. 1 Mohm)
//   SHDN unconnected
//
// Serial monitor (115200) prints the regulator output once a second:
//   ~3300 mV            -> regulator OK
//   ~battery voltage    -> regulator passes the input straight through: defective, don't use
//   ~0 mV               -> no output: defective, or wiring/battery
#include <Arduino.h>

static const int kPin = A0;          // D0
static const float kDivider = 2.0f;  // two equal resistors

void setup() {
  Serial.begin(115200);
  analogSetPinAttenuation(kPin, ADC_11db);
}

void loop() {
  analogReadMilliVolts(kPin);
  uint32_t sum = 0;
  for (int i = 0; i < 32; i++) sum += analogReadMilliVolts(kPin);
  uint32_t mv = (uint32_t)((sum / 32) * kDivider);
  const char *verdict = mv < 500    ? "no output (defective, or check wiring/battery/switch)"
                      : mv < 3150   ? "too low (defective, or battery empty)"
                      : mv <= 3450  ? "OK: regulated 3.3 V"
                                    : "TOO HIGH: passes battery voltage through -> defective, don't use";
  Serial.printf("Pololu VOUT = %4lu mV   %s\n", (unsigned long)mv, verdict);
  delay(1000);
}
