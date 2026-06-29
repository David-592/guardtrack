/*
 * GuardTrack ESP32 firmware — heartbeat MVP
 *
 * What this does (tonight's job):
 *   - Boots SIM7600G-H, registers on Digicel Guyana LTE
 *   - Activates PDP data context (APN "internet")
 *   - POSTs a heartbeat JSON to /api/data every 30 seconds
 *
 * What it does NOT do yet (we add as separate layers tomorrow):
 *   - GPS reading
 *   - Fingerprint / RFID authentication
 *   - Kill switch polling and relay control
 *   - Battery voltage reading
 *
 * Wiring (matches config.h):
 *   ESP32 GPIO 16 <- SIM7600 TXD     (data into ESP32)
 *   ESP32 GPIO 17 -> SIM7600 RXD     (data out of ESP32)
 *   ESP32 GPIO 4  -> SIM7600 PWRKEY  (optional)
 *   GND  <-> GND
 *   SIM7600 powered from EXTERNAL 5 V / 2 A+ supply (not from ESP32)
 *   LTE main antenna connected to MAIN port
 *
 * Build:
 *   Arduino IDE, Board = ESP32 Dev Module, 115200 baud Serial Monitor.
 *   No external libraries needed — raw AT commands only.
 *
 * Expected behaviour after flashing:
 *   ~30 seconds after boot, the dashboard pill flips from red Offline to
 *   green Online and stays there. /api/state will show online:true and
 *   the last_seen_at timestamp.
 */

#include <Arduino.h>
#include "config.h"

HardwareSerial Modem(1);

// ---------- AT helpers ----------
String sendAT(const String& cmd, const char* expect = "OK", uint32_t timeoutMs = 3000) {
  while (Modem.available()) Modem.read();
  Serial.print(F(">> ")); Serial.println(cmd);
  Modem.print(cmd); Modem.print("\r\n");
  String resp;
  uint32_t start = millis();
  while (millis() - start < timeoutMs) {
    while (Modem.available()) resp += (char)Modem.read();
    if (expect && resp.indexOf(expect) >= 0) break;
    if (resp.indexOf("ERROR") >= 0) break;
    delay(5);
  }
  Serial.print(resp);
  return resp;
}

bool waitFor(const char* expect, uint32_t timeoutMs, String* capture = nullptr) {
  String r;
  uint32_t start = millis();
  while (millis() - start < timeoutMs) {
    while (Modem.available()) {
      char c = Modem.read();
      Serial.write(c);
      r += c;
      if (r.indexOf(expect) >= 0) { if (capture) *capture = r; return true; }
    }
    delay(5);
  }
  if (capture) *capture = r;
  return false;
}

// ---------- Modem boot ----------
void powerOnModem() {
  pinMode(SIM_PWRKEY, OUTPUT);
  digitalWrite(SIM_PWRKEY, HIGH);
  delay(100);
  digitalWrite(SIM_PWRKEY, LOW);
  delay(1000);
  digitalWrite(SIM_PWRKEY, HIGH);
  // module needs ~10 s to finish booting
}

bool waitForRegistration(uint32_t timeoutMs = 90000) {
  uint32_t start = millis();
  while (millis() - start < timeoutMs) {
    String r = sendAT("AT+CREG?", "OK", 2000);
    if (r.indexOf(",1") >= 0 || r.indexOf(",5") >= 0) {
      Serial.println(F(">>> Registered on network"));
      return true;
    }
    delay(2000);
  }
  Serial.println(F("!! Not registered within timeout"));
  return false;
}

void modemInit() {
  Serial.println(F("Booting modem..."));
  uint32_t start = millis();
  bool alive = false;
  while (millis() - start < 30000) {
    if (sendAT("AT", "OK", 1000).indexOf("OK") >= 0) { alive = true; break; }
    delay(500);
  }
  if (!alive) {
    Serial.println(F("!! No response from modem. Check power/wiring/baud."));
    return;
  }

  sendAT("ATE0");                                  // echo off
  sendAT("AT+CMEE=2");                             // verbose errors
  sendAT("AT+CPIN?");                              // SIM ready?
  sendAT("AT+CSQ");                                // signal
  sendAT("AT+COPS?");                              // operator

  if (!waitForRegistration()) return;

  // APN + activate PDP data context
  String cgd = String("AT+CGDCONT=1,\"IP\",\"") + APN + "\"";
  sendAT(cgd);
  sendAT("AT+CGACT=1,1", "OK", 10000);
}

// ---------- HTTP POST (SIM7600 AT stack) ----------
bool httpPost(const String& url, const String& contentType, const String& body) {
  sendAT("AT+HTTPTERM", "OK", 1500);                          // ignore errors
  if (sendAT("AT+HTTPINIT", "OK", 3000).indexOf("OK") < 0) return false;
  sendAT(String("AT+HTTPPARA=\"URL\",\"") + url + "\"");
  if (contentType.length())
    sendAT(String("AT+HTTPPARA=\"CONTENT\",\"") + contentType + "\"");

  if (body.length()) {
    sendAT(String("AT+HTTPDATA=") + body.length() + ",10000", "DOWNLOAD", 5000);
    Modem.print(body);
    waitFor("OK", 10000);
  }

  sendAT("AT+HTTPACTION=1", "OK", 2000);                      // POST

  String urc;
  if (!waitFor("+HTTPACTION:", 30000, &urc)) {
    sendAT("AT+HTTPTERM");
    return false;
  }
  int p  = urc.indexOf("+HTTPACTION:");
  int c1 = urc.indexOf(',', p);
  int c2 = urc.indexOf(',', c1 + 1);
  int status = urc.substring(c1 + 1, c2).toInt();

  sendAT("AT+HTTPTERM", "OK", 1500);
  Serial.print(F("HTTP status: ")); Serial.println(status);
  return status >= 200 && status < 300;
}

// ---------- Telemetry ----------
String buildHeartbeatJson() {
  // Tonight: minimal stub payload so /api/state shows online + last_seen_at.
  // GPS, voltage, etc. will be added when those sensors come online.
  String j = "{";
  j += "\"voltage\":0,";
  j += "\"lat\":0,";
  j += "\"lng\":0,";
  j += "\"gps_valid\":false,";
  j += "\"status\":\"heartbeat\"";
  j += "}";
  return j;
}

void postHeartbeat() {
  String body = buildHeartbeatJson();
  Serial.print(F("Heartbeat body: ")); Serial.println(body);
  bool ok = httpPost(SERVER_URL, "application/json", body);
  Serial.println(ok ? F(">>> Heartbeat posted OK")
                    : F("!! Heartbeat failed"));
  digitalWrite(LED_PIN, ok ? HIGH : LOW);
}

// ---------- Arduino entry ----------
uint32_t lastTelem = 0;

void setup() {
  Serial.begin(115200);
  delay(150);
  Serial.println();
  Serial.println(F("=== GuardTrack ESP32 heartbeat MVP ==="));

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  Modem.begin(115200, SERIAL_8N1, SIM_RX, SIM_TX);
  powerOnModem();
  modemInit();

  Serial.println(F("Ready. Will POST heartbeat every 30 s."));
  postHeartbeat();             // immediate first post
  lastTelem = millis();
}

void loop() {
  if (millis() - lastTelem >= TELEMETRY_INTERVAL_MS) {
    lastTelem = millis();
    postHeartbeat();
  }

  // Pass-through any chatter for debugging
  while (Serial.available()) Modem.write(Serial.read());
  while (Modem.available())  Serial.write(Modem.read());

  delay(10);
}
