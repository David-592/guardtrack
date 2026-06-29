#ifndef CONFIG_H
#define CONFIG_H

// --- Server (your live Render dashboard) ---
#define SERVER_URL       "https://vehicle-immobilizer.onrender.com/api/data"
#define KILL_POLL_URL    "https://vehicle-immobilizer.onrender.com/api/killswitch"

// --- Cellular ---
#define APN              "internet"        // Digicel Guyana

// --- Registered phone numbers (up to 2 now) ---
#define PHONE_1 "+5926789608"
#define PHONE_2 ""

// --- Pin definitions ---
#define SIM_RX      16     // ESP32 RX  <- SIM7600 TXD
#define SIM_TX      17     // ESP32 TX  -> SIM7600 RXD
#define SIM_PWRKEY  4
#define RFID_SS     5
#define RFID_RST    21
#define FP_RX       13
#define FP_TX       12
#define LED_PIN     2
#define RELAY_PIN   25
#define VOLT_PIN    34

// --- Timing ---
#define TELEMETRY_INTERVAL_MS  30000UL    // POST heartbeat every 30 s
#define KILL_POLL_MS           5000UL     // poll kill switch every 5 s
#define LOW_VOLT_THRESH        11.5

#endif
