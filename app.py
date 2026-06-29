from flask import Flask, request, jsonify, render_template
from flask_socketio import SocketIO, emit
from database import (
    init_db, log_attempt, log_voltage, log_gps,
    get_attempts, get_voltage_history, get_system_state,
    set_kill_switch, add_rfid_card, get_rfid_cards, delete_rfid_card,
    add_fingerprint, get_fingerprints, delete_fingerprint,
    get_registered_phones, add_phone, delete_phone
)
from sms import queue_sms, get_pending_sms
from config import REGISTERED_PHONES, SECRET_KEY, ALERT_THRESHOLD, LOW_VOLT_THRESHOLD
from datetime import datetime
import time
import os

# ---------- Capacity limits (change here, applied everywhere) ----------
MAX_FINGERPRINTS = 5
MAX_RFID_CARDS   = 2
MAX_PHONES       = 2

# ---------- Device online tracking ----------
HEARTBEAT_TIMEOUT_S = 90       # device is "offline" if no telemetry for this many seconds
device_state = {
    "last_seen_at": None,      # epoch seconds of last /api/data POST
    "last_voltage": None,
    "last_lat": None,
    "last_lng": None,
    "last_gps_valid": False,
    "last_rssi_dbm": None,
    "last_network": None,
}

def is_online():
    ls = device_state["last_seen_at"]
    return ls is not None and (time.time() - ls) < HEARTBEAT_TIMEOUT_S

def seconds_since_seen():
    ls = device_state["last_seen_at"]
    return None if ls is None else int(time.time() - ls)

# ---------- Flask app ----------
app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*")

init_db()

pending_enrollment = {"active": False, "label": "", "fp_id": None}
failed_count = [0]

def seed_phones():
    existing = get_registered_phones()
    if not existing:
        # Only seed up to MAX_PHONES to avoid creating more than allowed
        for i, number in enumerate(REGISTERED_PHONES[:MAX_PHONES]):
            if number:
                add_phone(f"User {i+1}", number)

seed_phones()

# ---------- Device-facing API ----------
@app.route("/api/data", methods=["POST"])
def receive_data():
    data = request.json or {}
    lat       = data.get("lat", 0)
    lng       = data.get("lng", 0)
    voltage   = data.get("voltage", 0)
    status    = data.get("status", "unknown")
    attempt   = data.get("attempt", "")
    method    = data.get("method", "")
    gps_valid = data.get("gps_valid", False)
    rssi_dbm  = data.get("rssi_dbm")
    network   = data.get("network")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Heartbeat / live-state tracking
    device_state["last_seen_at"]   = time.time()
    device_state["last_voltage"]   = voltage
    device_state["last_lat"]       = lat
    device_state["last_lng"]       = lng
    device_state["last_gps_valid"] = bool(gps_valid)
    if rssi_dbm is not None:
        device_state["last_rssi_dbm"] = rssi_dbm
    if network is not None:
        device_state["last_network"] = network

    log_voltage(voltage, timestamp)
    log_gps(lat, lng, gps_valid, timestamp)

    if attempt:
        log_attempt(method, attempt, lat, lng, timestamp)
        if attempt == "denied":
            failed_count[0] += 1
            queue_sms(
                f"GUARDTRACK ALERT\n"
                f"Failed attempt #{failed_count[0]}\n"
                f"Method: {method.upper()}\n"
                f"GPS: {lat:.5f},{lng:.5f}\n"
                f"Battery: {voltage}V\n"
                f"Time: {timestamp}"
            )
        elif attempt == "granted":
            failed_count[0] = 0

    if voltage and voltage < LOW_VOLT_THRESHOLD:
        queue_sms(f"GUARDTRACK ALERT\nBattery low: {voltage}V\nGPS: {lat:.5f},{lng:.5f}")

    socketio.emit("update", {
        "lat": lat, "lng": lng, "voltage": voltage,
        "status": status, "attempt": attempt,
        "method": method, "timestamp": timestamp,
        "gps_valid": gps_valid,
        "online": True,
    })
    return jsonify({"ok": True})

@app.route("/api/killswitch", methods=["GET"])
def kill_poll():
    state = get_system_state()
    sms_queue = get_pending_sms()
    return jsonify({"kill": state["kill_active"], "sms_queue": sms_queue, "enrollment": pending_enrollment})

@app.route("/api/killswitch", methods=["POST"])
def set_kill():
    data = request.json or {}
    active = data.get("active", False)
    set_kill_switch(active)
    queue_sms(f"GUARDTRACK\nRemote kill {'ACTIVATED' if active else 'DEACTIVATED'} from dashboard.")
    socketio.emit("kill_update", {"kill": active})
    return jsonify({"ok": True, "kill": active})

# ---------- RFID ----------
@app.route("/api/rfid", methods=["GET"])
def get_cards():
    return jsonify(get_rfid_cards())

@app.route("/api/rfid", methods=["POST"])
def register_card():
    data = request.json or {}
    label = (data.get("label") or "").strip()
    uid   = (data.get("uid") or "").strip()
    if not label or not uid:
        return jsonify({"ok": False, "error": "Label and UID required"}), 400
    if len(get_rfid_cards()) >= MAX_RFID_CARDS:
        return jsonify({"ok": False, "error": f"Maximum {MAX_RFID_CARDS} RFID cards allowed"}), 400
    success = add_rfid_card(label, uid)
    if success:
        socketio.emit("rfid_update", {"action": "added", "label": label, "uid": uid})
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "UID already registered"}), 409

@app.route("/api/rfid/<int:card_id>", methods=["DELETE"])
def remove_card(card_id):
    delete_rfid_card(card_id)
    socketio.emit("rfid_update", {"action": "removed", "id": card_id})
    return jsonify({"ok": True})

# ---------- Fingerprint ----------
@app.route("/api/fingerprint", methods=["GET"])
def get_fps():
    return jsonify(get_fingerprints())

@app.route("/api/fingerprint/enroll", methods=["POST"])
def start_enrollment():
    data = request.json or {}
    label = (data.get("label") or "").strip()
    fp_id = data.get("fp_id", 1)
    if not label:
        return jsonify({"ok": False, "error": "Label required"}), 400
    try:
        fp_id = int(fp_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "fp_id must be an integer"}), 400
    if fp_id < 1 or fp_id > MAX_FINGERPRINTS:
        return jsonify({"ok": False, "error": f"fp_id must be 1..{MAX_FINGERPRINTS}"}), 400
    if len(get_fingerprints()) >= MAX_FINGERPRINTS:
        return jsonify({"ok": False, "error": f"Maximum {MAX_FINGERPRINTS} fingerprints allowed"}), 400
    pending_enrollment["active"] = True
    pending_enrollment["label"] = label
    pending_enrollment["fp_id"] = fp_id
    return jsonify({"ok": True, "message": f"Enrollment started for {label} as ID #{fp_id}"})

@app.route("/api/fingerprint/enrolled", methods=["POST"])
def confirm_enrollment():
    data = request.json or {}
    label = data.get("label", pending_enrollment["label"])
    fp_id = data.get("fp_id", pending_enrollment["fp_id"])
    add_fingerprint(label, fp_id)
    pending_enrollment["active"] = False
    pending_enrollment["label"] = ""
    pending_enrollment["fp_id"] = None
    socketio.emit("fp_update", {"action": "added", "label": label, "fp_id": fp_id})
    return jsonify({"ok": True})

@app.route("/api/fingerprint/<int:fp_id>", methods=["DELETE"])
def remove_fp(fp_id):
    delete_fingerprint(fp_id)
    socketio.emit("fp_update", {"action": "removed", "id": fp_id})
    return jsonify({"ok": True})

# ---------- Phones ----------
@app.route("/api/phones", methods=["GET"])
def get_phones():
    return jsonify(get_registered_phones())

@app.route("/api/phones", methods=["POST"])
def register_phone():
    data = request.json or {}
    label  = (data.get("label") or "").strip()
    number = (data.get("number") or "").strip()
    if not label or not number:
        return jsonify({"ok": False, "error": "Label and number required"}), 400
    phones = get_registered_phones()
    if len(phones) >= MAX_PHONES:
        return jsonify({"ok": False, "error": f"Maximum {MAX_PHONES} phones allowed"}), 400
    success = add_phone(label, number)
    if success:
        socketio.emit("phone_update", {"action": "added", "label": label, "number": number})
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Number already registered"}), 409

@app.route("/api/phones/<int:phone_id>", methods=["DELETE"])
def remove_phone(phone_id):
    delete_phone(phone_id)
    socketio.emit("phone_update", {"action": "removed", "id": phone_id})
    return jsonify({"ok": True})

# ---------- Telemetry history ----------
@app.route("/api/attempts", methods=["GET"])
def get_attempts_route():
    return jsonify(get_attempts())

@app.route("/api/voltage", methods=["GET"])
def get_voltage_route():
    return jsonify(get_voltage_history())

# ---------- Dashboard state (one-shot snapshot) ----------
@app.route("/api/state", methods=["GET"])
def get_state():
    sys = get_system_state()
    return jsonify({
        "online":             is_online(),
        "seconds_since_seen": seconds_since_seen(),
        "last_seen_at":       device_state["last_seen_at"],
        "device": {
            "voltage":   device_state["last_voltage"],
            "lat":       device_state["last_lat"],
            "lng":       device_state["last_lng"],
            "gps_valid": device_state["last_gps_valid"],
            "rssi_dbm":  device_state["last_rssi_dbm"],
            "network":   device_state["last_network"],
        },
        "immobilizer": {
            "kill_active":     sys["kill_active"],
            "last_known_lat":  sys["last_known_lat"],
            "last_known_lng":  sys["last_known_lng"],
            "last_updated":    sys["last_updated"],
        },
        "failed_count": failed_count[0],
        "capacities": {
            "fingerprints": MAX_FINGERPRINTS,
            "rfid":         MAX_RFID_CARDS,
            "phones":       MAX_PHONES,
        },
        "counts": {
            "fingerprints": len(get_fingerprints()),
            "rfid":         len(get_rfid_cards()),
            "phones":       len(get_registered_phones()),
        },
        "pending_enrollment": pending_enrollment,
    })

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
