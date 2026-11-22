#pragma once
// ---- edit these ------------------------------------------------------------

#include "secrets.h"   // your WiFi and server address, see secrets.example.h (git-ignored)

// Raspberry Pi running server/server.py (give it a fixed IP / DHCP reservation).
// Set these in secrets.h; the values here are only defaults.
#ifndef SERVER_HOST
#define SERVER_HOST "192.168.1.10"
#endif
#ifndef SERVER_PORT
#define SERVER_PORT 8080
#endif

// Optional static IP for the ESP32: saves ~0.5-1 s of DHCP per wake-up.
// #define STATIC_IP  IPAddress(192, 168, 1, 60)
// #define GATEWAY    IPAddress(192, 168, 1, 1)
// #define SUBNET     IPAddress(255, 255, 255, 0)
// #define DNS        IPAddress(192, 168, 1, 1)

// Fallback sleep if the server doesn't send X-Sleep-Seconds (normally it aligns to :00/:30)
#define REFRESH_SECONDS 1800
// Retry interval after a failed WiFi/HTTP attempt (first 3 failures, then REFRESH_SECONDS)
#define RETRY_SECONDS   300

// ---- wiring (Seeed XIAO ESP32-C6 pin names) ------------------------------------
// e-paper module:  VCC->3V3  GND->GND  DIN->D10  CLK->D8  CS->D1  DC->D3  RST->D4  BUSY->D5
#define PIN_EPD_SCK   D8
#define PIN_EPD_MOSI  D10
#define PIN_EPD_CS    D1
#define PIN_EPD_DC    D3
#define PIN_EPD_RST   D4
#define PIN_EPD_BUSY  D5
// Waveshare boards with a PWR pin: connect to D2 and set this to D2; otherwise -1
#define PIN_EPD_PWR   -1

// Battery voltage divider (battery+ -> 1M -> A0 -> 1M -> GND, 100nF A0->GND; any equal pair
// >= 100k works, below ~1M the 100nF is optional). Battery+ also feeds the XIAO 5V pin.
// Set PIN_BATTERY to -1 if not fitted.
#define PIN_BATTERY      A0   // -1 = no divider fitted (USB power, reports 0 mV -> plug icon)
#define BATTERY_DIVIDER  2.0f
// Below this the ESP stops waking WiFi (protects an unprotected Li-ion cell). 0 = off.
#define BATTERY_CUTOFF_MV 3200   // 3x NiMH: 1.07 V/cell protects the cells from deep discharge

// Use the u.FL connector + external antenna instead of the onboard one
#define USE_EXTERNAL_ANTENNA 0
