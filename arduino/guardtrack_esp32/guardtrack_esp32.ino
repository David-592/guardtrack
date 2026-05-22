#ifndef CONFIG_H
#define CONFIG_H

#define SERVER_URL    "http://your-domain.com/api/data"
#define KILL_POLL_URL "http://your-domain.com/api/killswitch"

#define PHONE_1 "+15921234567"
#define PHONE_2 "+15929876543"
#define PHONE_3 ""

#define SIM_RX     16
#define SIM_TX     17
#define SIM_PWRKEY 4
#define RFID_SS    5
#define RFID_RST   21
#define FP_RX      13
#define FP_TX      12
#define LED_PIN    2
#define RELAY_PIN  25
#define VOLT_PIN   34

#define GPS_INTERVAL    10000
#define KILL_POLL_MS    5000
#define LOW_VOLT_THRESH 11.5

#endif
