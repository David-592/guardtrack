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

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*")

init_db()

pending_enrollment = {"active": False, "label": "", "fp_id": None}
failed_count = [0]

def seed_phones():
    existing = get_registered_phones()
    if not existing:
        for i, number in enumerate(REGISTERED_PHONES):
            if number:
                add_phone(f"User {i+1}", number)

seed_phones()

@app.route("/api/data", methods=["POST"])
def receive_data():
    data = request.json
    lat       = data.get("lat", 0)
    lng       = data.get("lng", 0)
    voltage   = data.get("voltage", 0)
    status    = data.get("status", "unknown")
    attempt   = data.get("attempt", "")
    method    = data.get("method", "")
    gps_valid = data.get("gps_valid", False)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

    if voltage < LOW_VOLT_THRESHOLD:
        queue_sms(f"GUARDTRACK ALERT\nBattery low: {voltage}V\nGPS: {lat:.5f},{lng:.5f}")

    socketio.emit("update", {
        "lat": lat, "lng": lng, "voltage": voltage,
        "status": status, "attempt": attempt,
        "method": method, "timestamp": timestamp,
        "gps_valid": gps_valid
    })
    return jsonify({"ok": True})

@app.route("/api/killswitch", methods=["GET"])
def kill_poll():
    state = get_system_state()
    sms_queue = get_pending_sms()
    return jsonify({"kill": state["kill_active"], "sms_queue": sms_queue, "enrollment": pending_enrollment})

@app.route("/api/killswitch", methods=["POST"])
def set_kill():
    data = request.json
    active = data.get("active", False)
    set_kill_switch(active)
    queue_sms(f"GUARDTRACK\nRemote kill {'ACTIVATED' if active else 'DEACTIVATED'} from dashboard.")
    socketio.emit("kill_update", {"kill": active})
    return jsonify({"ok": True, "kill": active})

@app.route("/api/rfid", methods=["GET"])
def get_cards():
    return jsonify(get_rfid_cards())

@app.route("/api/rfid", methods=["POST"])
def register_card():
    data = request.json
    label = data.get("label", "")
    uid   = data.get("uid", "")
    if not label or not uid:
        return jsonify({"ok": False, "error": "Label and UID required"}), 400
    success = add_rfid_card(label, uid)
    if success:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "UID already registered"}), 409

@app.route("/api/rfid/<int:card_id>", methods=["DELETE"])
def remove_card(card_id):
    delete_rfid_card(card_id)
    return jsonify({"ok": True})

@app.route("/api/fingerprint", methods=["GET"])
def get_fps():
    return jsonify(get_fingerprints())

@app.route("/api/fingerprint/enroll", methods=["POST"])
def start_enrollment():
    data = request.json
    label = data.get("label", "")
    fp_id = data.get("fp_id", 1)
    if not label:
        return jsonify({"ok": False, "error": "Label required"}), 400
    pending_enrollment["active"] = True
    pending_enrollment["label"] = label
    pending_enrollment["fp_id"] = fp_id
    return jsonify({"ok": True, "message": f"Enrollment started for {label} as ID #{fp_id}"})

@app.route("/api/fingerprint/enrolled", methods=["POST"])
def confirm_enrollment():
    data = request.json
    label = data.get("label", pending_enrollment["label"])
    fp_id = data.get("fp_id", pending_enrollment["fp_id"])
    add_fingerprint(label, fp_id)
    pending_enrollment["active"] = False
    pending_enrollment["label"] = ""
    pending_enrollment["fp_id"] = None
    return jsonify({"ok": True})

@app.route("/api/fingerprint/<int:fp_id>", methods=["DELETE"])
def remove_fp(fp_id):
    delete_fingerprint(fp_id)
    return jsonify({"ok": True})

@app.route("/api/phones", methods=["GET"])
def get_phones():
    return jsonify(get_registered_phones())

@app.route("/api/phones", methods=["POST"])
def register_phone():
    data = request.json
    label  = data.get("label", "")
    number = data.get("number", "")
    if not label or not number:
        return jsonify({"ok": False, "error": "Label and number required"}), 400
    phones = get_registered_phones()
    if len(phones) >= 3:
        return jsonify({"ok": False, "error": "Maximum 3 phones allowed"}), 400
    success = add_phone(label, number)
    if success:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Number already registered"}), 409

@app.route("/api/phones/<int:phone_id>", methods=["DELETE"])
def remove_phone(phone_id):
    delete_phone(phone_id)
    return jsonify({"ok": True})

@app.route("/api/attempts", methods=["GET"])
def get_attempts_route():
    return jsonify(get_attempts())

@app.route("/api/voltage", methods=["GET"])
def get_voltage_route():
    return jsonify(get_voltage_history())

@app.route("/api/state", methods=["GET"])
def get_state():
    return jsonify(get_system_state())

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    import os
port = int(os.environ.get("PORT", 8080))
socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)

