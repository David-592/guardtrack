# GuardTrack — Vehicle Immobilizer System

## Files
- `app.py` — Flask backend server
- `database.py` — SQLite database functions
- `sms.py` — SMS queue handler
- `config.py` — Server settings & phone numbers
- `config.h` — ESP32 settings & pin definitions
- `main.ino` — ESP32 Arduino sketch
- `requirements.txt` — Python dependencies
- `Procfile` — Railway deployment config

## Setup

### 1. Edit config.py
- Add your phone numbers
- Change SECRET_KEY

### 2. Edit config.h
- Add your server URL after deploying
- Confirm pin numbers match your wiring

### 3. Run server locally
```
pip install flask flask-socketio
python app.py
```

### 4. Deploy to Railway
- Push to GitHub
- Connect repo to Railway
- Add custom domain in Railway settings

## Wiring Summary
| Module     | ESP32 Pins         |
|------------|--------------------|
| SIM7600G   | RX=16, TX=17, PWR=4|
| RFID RC522 | SS=5, RST=21       |
| FPM11A     | RX=13, TX=12       |
| Relay      | GPIO25             |
| LED        | GPIO2              |
| Voltage    | GPIO34 (analog)    |
