#include <SPI.h>
#include <MFRC522.h>
#include <Adafruit_Fingerprint.h>
#include "config.h"

MFRC522 rfid(RFID_SS, RFID_RST);
HardwareSerial simSerial(1);
HardwareSerial fpSerial(2);
Adafruit_Fingerprint finger = Adafruit_Fingerprint(&fpSerial);

byte authorizedUID[4] = {0x9B, 0x27, 0x2C, 0x1F};

float gpsLat = 0, gpsLng = 0;
float lastKnownLat = 0, lastKnownLng = 0;
bool gpsValid = false;
bool killActive = false;
bool lowVoltAlertSent = false;
int failedAttempts = 0;

unsigned long lastGPSUpdate = 0;
unsigned long lastKillPoll = 0;

String phones[] = {PHONE_1, PHONE_2, PHONE_3};

void simCmd(String cmd, int wait = 1000) {
  simSerial.println(cmd);
  delay(wait);
  while (simSerial.available()) Serial.write(simSerial.read());
}

String simCmdResponse(String cmd, int wait = 2000) {
  simSerial.println(cmd);
  delay(wait);
  String resp = "";
  while (simSerial.available()) resp += (char)simSerial.read();
  return resp;
}

void powerOnSIM() {
  Serial.println("Powering on SIM7600G...");
  pinMode(SIM_PWRKEY, OUTPUT);
  digitalWrite(SIM_PWRKEY, LOW);
  delay(3000);
  digitalWrite(SIM_PWRKEY, HIGH);
  delay(5000);
  simCmd("AT");
  simCmd("ATE0");
  simCmd("AT+CMGF=1");
  simCmd("AT+CGPS=1,1");
  Serial.println("SIM7600G ready.");
}

void sendSMS(String number, String message) {
  if (number.length() == 0) return;
  simCmd("AT+CMGF=1");
  simSerial.print("AT+CMGS=\"");
  simSerial.print(number);
  simSerial.println("\"");
  delay(500);
  simSerial.print(message);
  delay(200);
  simSerial.write(26);
  delay(3000);
}

void sendSMSAll(String message) {
  for (int i = 0; i < 3; i++) sendSMS(phones[i], message);
}

void updateGPS() {
  String resp = simCmdResponse("AT+CGPSINFO", 2000);
  if (resp.indexOf("+CGPSINFO:") != -1 && resp.indexOf(",,") == -1) {
    int start = resp.indexOf(":") + 2;
    float rawLat = resp.substring(start, start + 10).toFloat();
    char ns = resp.charAt(start + 11);
    start += 13;
    float rawLng = resp.substring(start, start + 11).toFloat();
    char ew = resp.charAt(start + 12);
    int latDeg = (int)(rawLat / 100);
    float latMin = rawLat - latDeg * 100;
    gpsLat = latDeg + latMin / 60.0;
    if (ns == 'S') gpsLat = -gpsLat;
    int lngDeg = (int)(rawLng / 100);
    float lngMin = rawLng - lngDeg * 100;
    gpsLng = lngDeg + lngMin / 60.0;
    if (ew == 'W') gpsLng = -gpsLng;
    gpsValid = true;
    lastKnownLat = gpsLat;
    lastKnownLng = gpsLng;
  } else {
    gpsValid = false;
  }
}

float readVoltage() {
  int raw = analogRead(VOLT_PIN);
  return (raw / 4095.0) * 3.3 * ((100.0 + 10.0) / 10.0);
}

void postToServer(float voltage, String status, String attempt, String method) {
  float lat = gpsValid ? gpsLat : lastKnownLat;
  float lng = gpsValid ? gpsLng : lastKnownLng;
  String json = "{";
  json += "\"lat\":" + String(lat, 6) + ",";
  json += "\"lng\":" + String(lng, 6) + ",";
  json += "\"voltage\":" + String(voltage, 2) + ",";
  json += "\"status\":\"" + status + "\",";
  json += "\"attempt\":\"" + attempt + "\",";
  json += "\"method\":\"" + method + "\",";
  json += "\"gps_valid\":" + String(gpsValid ? "true" : "false");
  json += "}";
  simCmd("AT+HTTPINIT");
  simCmd("AT+HTTPPARA=\"CID\",1");
  simCmd("AT+HTTPPARA=\"URL\",\"" + String(SERVER_URL) + "\"");
  simCmd("AT+HTTPPARA=\"CONTENT\",\"application/json\"");
  simCmd("AT+HTTPDATA=" + String(json.length()) + ",10000", 2000);
  simSerial.println(json);
  delay(2000);
  simCmd("AT+HTTPACTION=1", 5000);
  simCmd("AT+HTTPTERM");
}

void checkKillSwitch() {
  simCmd("AT+HTTPINIT");
  simCmd("AT+HTTPPARA=\"CID\",1");
  simCmd("AT+HTTPPARA=\"URL\",\"" + String(KILL_POLL_URL) + "\"");
  simCmd("AT+HTTPACTION=0", 5000);
  String resp = simCmdResponse("AT+HTTPREAD", 2000);
  simCmd("AT+HTTPTERM");

  // Handle kill switch
  if (resp.indexOf("\"kill\":true") != -1 && !killActive) {
    killActive = true;
    digitalWrite(RELAY_PIN, LOW);
    sendSMSAll("GUARDTRACK ALERT\nRemote kill activated!\nIgnition relay cut.\nLast GPS: " + String(lastKnownLat, 5) + "," + String(lastKnownLng, 5));
  } else if (resp.indexOf("\"kill\":false") != -1 && killActive) {
    killActive = false;
    digitalWrite(RELAY_PIN, HIGH);
  }

  // Handle queued SMS from server
  if (resp.indexOf("\"sms_queue\":[") != -1) {
    int start = resp.indexOf("\"sms_queue\":[") + 13;
    int end = resp.indexOf("]", start);
    String queue = resp.substring(start, end);
    if (queue.length() > 2) {
      sendSMSAll("GUARDTRACK\n" + queue);
    }
  }

  // Handle enrollment trigger
  if (resp.indexOf("\"active\":true") != -1) {
    Serial.println("Enrollment triggered from dashboard");
  }
}

void grantAccess(String method) {
  Serial.println("Access GRANTED via " + method);
  digitalWrite(LED_PIN, HIGH);
  delay(3000);
  digitalWrite(LED_PIN, LOW);
  failedAttempts = 0;
  postToServer(readVoltage(), "unlocked", "granted", method);
}

void denyAccess(String method) {
  Serial.println("Access DENIED via " + method);
  for (int i = 0; i < 3; i++) {
    digitalWrite(LED_PIN, HIGH); delay(150);
    digitalWrite(LED_PIN, LOW);  delay(150);
  }
  failedAttempts++;
  postToServer(readVoltage(), "locked", "denied", method);
}

void checkRFID() {
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return;
  bool match = true;
  for (int i = 0; i < 4; i++) {
    if (rfid.uid.uidByte[i] != authorizedUID[i]) { match = false; break; }
  }
  if (match) grantAccess("rfid");
  else denyAccess("rfid");
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
}

void checkFingerprint() {
  uint8_t p = finger.getImage();
  if (p != FINGERPRINT_OK) return;
  p = finger.image2Tz();
  if (p != FINGERPRINT_OK) return;
  p = finger.fingerSearch();
  if (p == FINGERPRINT_OK) grantAccess("fingerprint");
  else denyAccess("fingerprint");
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, HIGH);
  digitalWrite(LED_PIN, LOW);
  simSerial.begin(115200, SERIAL_8N1, SIM_RX, SIM_TX);
  powerOnSIM();
  SPI.begin();
  rfid.PCD_Init();
  fpSerial.begin(57600, SERIAL_8N1, FP_RX, FP_TX);
  finger.begin(57600);
  if (finger.verifyPassword()) Serial.println("Fingerprint ready.");
  else Serial.println("Fingerprint not found!");
  updateGPS();
  sendSMSAll("GUARDTRACK ONLINE\nSystem started.\nGPS: " + String(lastKnownLat, 5) + "," + String(lastKnownLng, 5));
  Serial.println("System ready.");
}

void loop() {
  if (!killActive) {
    checkRFID();
    checkFingerprint();
  }
  if (millis() - lastGPSUpdate > GPS_INTERVAL) {
    lastGPSUpdate = millis();
    updateGPS();
    postToServer(readVoltage(), killActive ? "killed" : "locked", "", "");
  }
  if (millis() - lastKillPoll > KILL_POLL_MS) {
    lastKillPoll = millis();
    checkKillSwitch();
  }
  float v = readVoltage();
  if (v < LOW_VOLT_THRESH && !lowVoltAlertSent) {
    lowVoltAlertSent = true;
    sendSMSAll("GUARDTRACK ALERT\nBattery low: " + String(v, 1) + "V");
  } else if (v >= LOW_VOLT_THRESH) {
    lowVoltAlertSent = false;
  }
}
