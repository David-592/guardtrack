from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps
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
import sqlite3, time, os

# ---------- Config ----------
MAX_FINGERPRINTS   = 5
MAX_RFID_CARDS     = 2
MAX_PHONES         = 2
HEARTBEAT_TIMEOUT_S = 90

# Credentials from env vars (fallback for local dev)
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "owner")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "change-me")
DEVICE_TOKEN       = os.environ.get("DEVICE_TOKEN", "change-me-device")
FLASK_SECRET       = os.environ.get("SECRET_KEY", SECRET_KEY or "change-me-secret")

# ---------- Device state ----------
device_state = {
    "last_seen_at": None, "last_voltage": None,
    "last_lat": None, "last_lng": None,
    "last_gps_valid": False, "last_rssi_dbm": None, "last_network": None,
    "went_offline_at": None,
}

def is_online():
    ls = device_state["last_seen_at"]
    return ls is not None and (time.time() - ls) < HEARTBEAT_TIMEOUT_S

def seconds_since_seen():
    ls = device_state["last_seen_at"]
    return None if ls is None else int(time.time() - ls)

# ---------- Flask ----------
app = Flask(__name__)
app.config["SECRET_KEY"] = FLASK_SECRET
socketio = SocketIO(app, cors_allowed_origins="*")

# ---------- Flask-Login ----------
login_manager = LoginManager(app)
login_manager.login_view = "login"

class Owner(UserMixin):
    def __init__(self, username):
        self.id = username

@login_manager.user_loader
def load_user(user_id):
    if user_id == DASHBOARD_USERNAME:
        return Owner(user_id)
    return None

# ---------- Device-token decorator ----------
def device_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        token = request.headers.get("X-Device-Auth", "")
        if token != DEVICE_TOKEN:
            return jsonify({"error": "unauthorized device"}), 401
        return f(*a, **kw)
    return wrapper

def user_or_device(f):
    """Endpoint accepts EITHER a logged-in user session OR a valid device token."""
    @wraps(f)
    def wrapper(*a, **kw):
        if current_user.is_authenticated:
            return f(*a, **kw)
        token = request.headers.get("X-Device-Auth", "")
        if token == DEVICE_TOKEN:
            return f(*a, **kw)
        return jsonify({"error": "authentication required"}), 401
    return wrapper

init_db()

pending_enrollment = {"active": False, "label": "", "fp_id": None}
failed_count = [0]

def seed_phones():
    existing = get_registered_phones()
    if not existing:
        for i, number in enumerate(REGISTERED_PHONES[:MAX_PHONES]):
            if number:
                add_phone(f"User {i+1}", number)

seed_phones()

# ==================================================
# AUTH ROUTES
# ==================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if u == DASHBOARD_USERNAME and p == DASHBOARD_PASSWORD:
            login_user(Owner(u))
            return redirect(url_for("index"))
        flash("Invalid username or password")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# ==================================================
# DEVICE-FACING API
# ==================================================
@app.route("/api/data", methods=["POST"])
@device_required
def receive_data():
    data = request.json or {}
    lat = data.get("lat", 0);  lng = data.get("lng", 0)
    voltage = data.get("voltage", 0);  status = data.get("status", "unknown")
    attempt = data.get("attempt", "");  method = data.get("method", "")
    gps_valid = data.get("gps_valid", False)
    rssi_dbm  = data.get("rssi_dbm");   network = data.get("network")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    was_offline = not is_online()
    device_state["last_seen_at"] = time.time()
    device_state["last_voltage"] = voltage
    if gps_valid:
        device_state["last_lat"] = lat; device_state["last_lng"] = lng
    device_state["last_gps_valid"] = bool(gps_valid)
    if rssi_dbm is not None: device_state["last_rssi_dbm"] = rssi_dbm
    if network  is not None: device_state["last_network"]  = network
    if was_offline: socketio.emit("reconnect", {"at": timestamp})

    log_voltage(voltage, timestamp)
    log_gps(lat, lng, gps_valid, timestamp)

    if attempt:
        log_attempt(method, attempt, lat, lng, timestamp)
        if attempt == "denied":
            failed_count[0] += 1
            queue_sms(f"GUARDTRACK ALERT\nFailed attempt #{failed_count[0]}\nMethod: {method.upper()}\nGPS: {lat:.5f},{lng:.5f}\nBattery: {voltage}V\nTime: {timestamp}")
        elif attempt == "granted":
            failed_count[0] = 0
    if voltage and voltage < LOW_VOLT_THRESHOLD:
        queue_sms(f"GUARDTRACK ALERT\nBattery low: {voltage}V\nGPS: {lat:.5f},{lng:.5f}")

    socketio.emit("update", {"lat":lat,"lng":lng,"voltage":voltage,"status":status,
                              "attempt":attempt,"method":method,"timestamp":timestamp,
                              "gps_valid":gps_valid,"online":True})
    return jsonify({"ok": True})

@app.route("/api/gps/batch", methods=["POST"])
@device_required
def gps_batch():
    data = request.json or {}
    points = data.get("points", []); saved = 0
    for p in points:
        try:
            lat = float(p.get("lat", 0));  lng = float(p.get("lng", 0))
            gv  = bool(p.get("gps_valid", False))
            ts  = p.get("utc") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_gps(lat, lng, gv, ts); saved += 1
        except (ValueError, TypeError):
            continue
    socketio.emit("gps_batch_uploaded", {"count": saved})
    return jsonify({"ok": True, "saved": saved})

@app.route("/api/killswitch", methods=["GET"])
@device_required
def kill_poll():
    state = get_system_state()
    sms_queue = get_pending_sms()
    return jsonify({"kill": state["kill_active"], "sms_queue": sms_queue, "enrollment": pending_enrollment})

@app.route("/api/killswitch", methods=["POST"])
@user_or_device
def set_kill():
    data = request.json or {}
    active = data.get("active", False)
    set_kill_switch(active)
    queue_sms(f"GUARDTRACK\nRemote kill {'ACTIVATED' if active else 'DEACTIVATED'} from dashboard.")
    socketio.emit("kill_update", {"kill": active})
    return jsonify({"ok": True, "kill": active})

@app.route("/api/fingerprint/enrolled", methods=["POST"])
@device_required
def confirm_enrollment():
    data = request.json or {}
    label = data.get("label", pending_enrollment["label"])
    fp_id = data.get("fp_id", pending_enrollment["fp_id"])
    add_fingerprint(label, fp_id)
    pending_enrollment["active"] = False
    pending_enrollment["label"] = ""; pending_enrollment["fp_id"] = None
    socketio.emit("fp_update", {"action":"added","label":label,"fp_id":fp_id})
    return jsonify({"ok": True})

# ==================================================
# DASHBOARD API — user login required
# ==================================================
@app.route("/api/rfid", methods=["GET"])
@login_required
def get_cards(): return jsonify(get_rfid_cards())

@app.route("/api/rfid", methods=["POST"])
@login_required
def register_card():
    data = request.json or {}
    label = (data.get("label") or "").strip();  uid = (data.get("uid") or "").strip()
    if not label or not uid: return jsonify({"ok":False,"error":"Label and UID required"}),400
    if len(get_rfid_cards()) >= MAX_RFID_CARDS:
        return jsonify({"ok":False,"error":f"Maximum {MAX_RFID_CARDS} RFID cards allowed"}),400
    if add_rfid_card(label, uid):
        socketio.emit("rfid_update",{"action":"added","label":label,"uid":uid})
        return jsonify({"ok":True})
    return jsonify({"ok":False,"error":"UID already registered"}),409

@app.route("/api/rfid/<int:card_id>", methods=["DELETE"])
@login_required
def remove_card(card_id):
    delete_rfid_card(card_id)
    socketio.emit("rfid_update", {"action":"removed","id":card_id})
    return jsonify({"ok": True})

@app.route("/api/fingerprint", methods=["GET"])
@login_required
def get_fps(): return jsonify(get_fingerprints())

@app.route("/api/fingerprint/enroll", methods=["POST"])
@login_required
def start_enrollment():
    data = request.json or {}
    label = (data.get("label") or "").strip();  fp_id = data.get("fp_id", 1)
    if not label: return jsonify({"ok":False,"error":"Label required"}),400
    try: fp_id = int(fp_id)
    except: return jsonify({"ok":False,"error":"fp_id must be an integer"}),400
    if fp_id < 1 or fp_id > MAX_FINGERPRINTS:
        return jsonify({"ok":False,"error":f"fp_id must be 1..{MAX_FINGERPRINTS}"}),400
    if len(get_fingerprints()) >= MAX_FINGERPRINTS:
        return jsonify({"ok":False,"error":f"Maximum {MAX_FINGERPRINTS} fingerprints allowed"}),400
    pending_enrollment["active"] = True
    pending_enrollment["label"] = label; pending_enrollment["fp_id"] = fp_id
    return jsonify({"ok":True,"message":f"Enrollment started for {label} as ID #{fp_id}"})

@app.route("/api/fingerprint/<int:fp_id>", methods=["DELETE"])
@login_required
def remove_fp(fp_id):
    delete_fingerprint(fp_id)
    socketio.emit("fp_update", {"action":"removed","id":fp_id})
    return jsonify({"ok": True})

# Phones GET is shared: dashboard AND device both need it
@app.route("/api/phones", methods=["GET"])
@user_or_device
def get_phones(): return jsonify(get_registered_phones())

@app.route("/api/phones", methods=["POST"])
@login_required
def register_phone():
    data = request.json or {}
    label = (data.get("label") or "").strip();  number = (data.get("number") or "").strip()
    if not label or not number: return jsonify({"ok":False,"error":"Label and number required"}),400
    phones = get_registered_phones()
    if len(phones) >= MAX_PHONES: return jsonify({"ok":False,"error":f"Maximum {MAX_PHONES} phones allowed"}),400
    if add_phone(label, number):
        socketio.emit("phone_update",{"action":"added","label":label,"number":number})
        return jsonify({"ok":True})
    return jsonify({"ok":False,"error":"Number already registered"}),409

@app.route("/api/phones/<int:phone_id>", methods=["DELETE"])
@login_required
def remove_phone(phone_id):
    delete_phone(phone_id)
    socketio.emit("phone_update",{"action":"removed","id":phone_id})
    return jsonify({"ok": True})

@app.route("/api/attempts", methods=["GET"])
@login_required
def get_attempts_route(): return jsonify(get_attempts())

@app.route("/api/voltage", methods=["GET"])
@login_required
def get_voltage_route(): return jsonify(get_voltage_history())

# ---------- GPS history ----------
def query_gps_since(seconds_back=6*3600, limit=500):
    conn = sqlite3.connect("guardtrack.db"); c = conn.cursor()
    c.execute("""SELECT lat,lng,gps_valid,timestamp FROM gps_log
                 WHERE gps_valid=1 ORDER BY id DESC LIMIT ?""", (limit,))
    rows = c.fetchall(); conn.close()
    return [{"lat":r[0],"lng":r[1],"gps_valid":bool(r[2]),"timestamp":r[3]} for r in reversed(rows)]

@app.route("/api/gps/history", methods=["GET"])
@login_required
def get_gps_history():
    limit = int(request.args.get("limit", 500))
    points = query_gps_since(limit=limit)
    return jsonify({"count":len(points),"points":points,"last":points[-1] if points else None})

@app.route("/api/gps/history", methods=["DELETE"])
@login_required
def clear_gps_history():
    conn = sqlite3.connect("guardtrack.db"); c = conn.cursor()
    c.execute("DELETE FROM gps_log"); conn.commit(); conn.close()
    socketio.emit("gps_history_cleared", {})
    return jsonify({"ok": True, "message": "GPS history cleared"})

@app.route("/api/state", methods=["GET"])
@login_required
def get_state():
    sys = get_system_state()
    return jsonify({
        "online": is_online(),
        "seconds_since_seen": seconds_since_seen(),
        "last_seen_at": device_state["last_seen_at"],
        "device": {"voltage":device_state["last_voltage"],"lat":device_state["last_lat"],
                    "lng":device_state["last_lng"],"gps_valid":device_state["last_gps_valid"],
                    "rssi_dbm":device_state["last_rssi_dbm"],"network":device_state["last_network"]},
        "immobilizer": {"kill_active":sys["kill_active"],"last_known_lat":sys["last_known_lat"],
                        "last_known_lng":sys["last_known_lng"],"last_updated":sys["last_updated"]},
        "failed_count": failed_count[0],
        "capacities": {"fingerprints":MAX_FINGERPRINTS,"rfid":MAX_RFID_CARDS,"phones":MAX_PHONES},
        "counts": {"fingerprints":len(get_fingerprints()),"rfid":len(get_rfid_cards()),
                    "phones":len(get_registered_phones())},
        "pending_enrollment": pending_enrollment,
    })

@app.route("/")
@login_required
def index():
    return render_template("index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
