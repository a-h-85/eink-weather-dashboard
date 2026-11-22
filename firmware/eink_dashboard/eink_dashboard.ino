// E-ink weather dashboard client for Seeed XIAO ESP32-C6 + 4.2" 400x300 b/w e-paper (SSD1683).
//
// Every wake-up: read battery -> WiFi -> GET /display.bin from the Pi -> full refresh -> deep sleep.
// All layout/weather logic lives on the Pi; this sketch only shows a 1-bit frame.
//
// Arduino IDE: board package "esp32" by Espressif (>= 3.0), board "XIAO_ESP32C6",
// library "GxEPD2" by Jean-Marc Zingg (installs Adafruit GFX as dependency).

#include <WiFi.h>
#include <HTTPClient.h>
#include <SPI.h>
#include <GxEPD2_BW.h>
#include "config.h"

static const int EPD_W = 400, EPD_H = 300;
static const size_t FRAME_BYTES = EPD_W * EPD_H / 8;  // 15000
static const int kBatteryPin = PIN_BATTERY;
static const int kEpdPwrPin = PIN_EPD_PWR;

// Waveshare 4.2" V2 / GoodDisplay GDEY042T81 (SSD1683). For the old Waveshare 4.2" V1 use GxEPD2_420.
GxEPD2_BW<GxEPD2_420_GDEY042T81, GxEPD2_420_GDEY042T81::HEIGHT>
    display(GxEPD2_420_GDEY042T81(PIN_EPD_CS, PIN_EPD_DC, PIN_EPD_RST, PIN_EPD_BUSY));

static uint8_t frame[FRAME_BYTES];  // 1 bit/pixel, MSB first, 1 = black

// Survives deep sleep: lets us reconnect to the same AP without a scan (~1 s faster).
RTC_DATA_ATTR int32_t savedChannel = 0;
RTC_DATA_ATTR uint8_t savedBssid[6];
RTC_DATA_ATTR uint16_t failures = 0;

static void deepSleep(uint32_t seconds) {
  Serial.printf("sleeping %lu s\n", (unsigned long)seconds);
  Serial.flush();
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  esp_sleep_enable_timer_wakeup((uint64_t)seconds * 1000000ULL);
  esp_deep_sleep_start();
}

static uint32_t readBatteryMv() {
  if (kBatteryPin < 0) return 0;
  analogSetPinAttenuation(kBatteryPin, ADC_11db);  // 0..~3.1 V at the pin = up to ~6 V battery
  analogReadMilliVolts(kBatteryPin);                // first conversion after wake-up is unreliable
  uint32_t sum = 0;
  for (int i = 0; i < 16; i++) sum += analogReadMilliVolts(kBatteryPin);
  return (uint32_t)((sum / 16) * BATTERY_DIVIDER);
}

static bool waitConnected(uint32_t timeoutMs) {
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < timeoutMs) delay(20);
  return WiFi.status() == WL_CONNECTED;
}

static bool connectWifi() {
#if USE_EXTERNAL_ANTENNA
  pinMode(3, OUTPUT);  digitalWrite(3, LOW);    // enable RF switch
  pinMode(14, OUTPUT); digitalWrite(14, HIGH);  // select u.FL antenna
#endif
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
#ifdef STATIC_IP
  WiFi.config(STATIC_IP, GATEWAY, SUBNET, DNS);
#endif
  bool ok = false;
  if (savedChannel > 0) {
    WiFi.begin(WIFI_SSID, WIFI_PASS, savedChannel, savedBssid, true);
    ok = waitConnected(6000);
    if (!ok) {  // AP changed channel / roamed: fall back to a normal scan
      savedChannel = 0;
      WiFi.disconnect();
      delay(50);
    }
  }
  if (!ok) {
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    ok = waitConnected(12000);
  }
  if (ok) {
    savedChannel = WiFi.channel();
    memcpy(savedBssid, WiFi.BSSID(), 6);
  }
  return ok;
}

static bool fetchFrame(uint32_t batteryMv, uint32_t &sleepSeconds) {
  HTTPClient http;
  String url = String("http://") + SERVER_HOST + ":" + SERVER_PORT + "/display.bin?bat=" + batteryMv;
  http.setConnectTimeout(5000);
  http.setTimeout(8000);
  if (!http.begin(url)) return false;
  const char *keys[] = {"X-Sleep-Seconds"};
  http.collectHeaders(keys, 1);

  bool ok = false;
  int code = http.GET();
  if (code == HTTP_CODE_OK && http.getSize() == (int)FRAME_BYTES) {
    WiFiClient *s = http.getStreamPtr();
    size_t got = 0;
    uint32_t t0 = millis();
    while (got < FRAME_BYTES && millis() - t0 < 10000) {
      size_t avail = s->available();
      if (avail) got += s->readBytes(frame + got, std::min(avail, FRAME_BYTES - got));
      else delay(2);
    }
    ok = (got == FRAME_BYTES);
    long hs = http.header("X-Sleep-Seconds").toInt();
    if (hs >= 60 && hs <= 7200) sleepSeconds = (uint32_t)hs;
  } else {
    Serial.printf("HTTP %d, size %d\n", code, http.getSize());
  }
  http.end();
  return ok;
}

static void showFrame() {
  if (kEpdPwrPin >= 0) {
    pinMode(kEpdPwrPin, OUTPUT);
    digitalWrite(kEpdPwrPin, HIGH);
    delay(10);
  }
  SPI.begin(PIN_EPD_SCK, -1, PIN_EPD_MOSI, PIN_EPD_CS);
  display.init(115200, true, 2, false);  // 2 ms reset pulse for Waveshare boards
  display.setRotation(0);
  display.setFullWindow();
  display.fillScreen(GxEPD_WHITE);
  display.drawBitmap(0, 0, frame, EPD_W, EPD_H, GxEPD_BLACK);
  display.display(false);  // full refresh: no ghosting, ~3-4 s
  display.hibernate();
  if (kEpdPwrPin >= 0) digitalWrite(kEpdPwrPin, LOW);
}

void setup() {
  Serial.begin(115200);
  uint32_t sleepSeconds = REFRESH_SECONDS;

  uint32_t mv = readBatteryMv();  // measure before WiFi loads the battery
  Serial.printf("battery %lu mV\n", (unsigned long)mv);
  if (BATTERY_CUTOFF_MV > 0 && mv > 1000 && mv < BATTERY_CUTOFF_MV) {
    deepSleep(6 * 3600);  // battery nearly empty: last frame stays on screen (shows low %)
  }

  if (connectWifi() && fetchFrame(mv, sleepSeconds)) {
    failures = 0;
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);  // radio off before the slow display refresh
    showFrame();
  } else {
    failures++;
    Serial.printf("update failed (%u in a row)\n", failures);
    if (failures <= 3) sleepSeconds = RETRY_SECONDS;
  }
  deepSleep(sleepSeconds);
}

void loop() {}
