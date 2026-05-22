import sqlite3
from datetime import datetime

DB = "guardtrack.db"

def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            method TEXT, result TEXT, lat REAL, lng REAL, timestamp TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS voltage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voltage REAL, timestamp TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS gps_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lat REAL, lng REAL, gps_valid INTEGER, timestamp TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS rfid_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT, uid TEXT UNIQUE, registered_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS fingerprints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT, fp_id INTEGER UNIQUE, registered_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            id INTEGER PRIMARY KEY,
            kill_active INTEGER DEFAULT 0,
            last_known_lat REAL DEFAULT 0,
            last_known_lng REAL DEFAULT 0,
            last_updated TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS registered_phones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT, number TEXT UNIQUE, active INTEGER DEFAULT 1, registered_at TEXT
        )
    """)
    c.execute("""
        INSERT OR IGNORE INTO system_state (id, kill_active, last_updated)
        VALUES (1, 0, ?)
    """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit()
    conn.close()

def log_attempt(method, result, lat, lng, timestamp):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("INSERT INTO attempts (method, result, lat, lng, timestamp) VALUES (?,?,?,?,?)", (method, result, lat, lng, timestamp))
    conn.commit()
    conn.close()

def log_voltage(voltage, timestamp):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("INSERT INTO voltage_log (voltage, timestamp) VALUES (?,?)", (voltage, timestamp))
    conn.commit()
    conn.close()

def log_gps(lat, lng, gps_valid, timestamp):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("INSERT INTO gps_log (lat, lng, gps_valid, timestamp) VALUES (?,?,?,?)", (lat, lng, int(gps_valid), timestamp))
    if gps_valid:
        c.execute("UPDATE system_state SET last_known_lat=?, last_known_lng=?, last_updated=? WHERE id=1", (lat, lng, timestamp))
    conn.commit()
    conn.close()

def get_attempts(limit=50):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT method, result, lat, lng, timestamp FROM attempts ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [{"method": r[0], "result": r[1], "lat": r[2], "lng": r[3], "timestamp": r[4]} for r in rows]

def get_voltage_history(limit=20):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT voltage, timestamp FROM voltage_log ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [{"voltage": r[0], "timestamp": r[1]} for r in reversed(rows)]

def get_system_state():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT kill_active, last_known_lat, last_known_lng, last_updated FROM system_state WHERE id=1")
    row = c.fetchone()
    conn.close()
    return {"kill_active": bool(row[0]), "last_known_lat": row[1], "last_known_lng": row[2], "last_updated": row[3]}

def set_kill_switch(active: bool):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("UPDATE system_state SET kill_active=?, last_updated=? WHERE id=1", (int(active), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def add_rfid_card(label, uid):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO rfid_cards (label, uid, registered_at) VALUES (?,?,?)", (label, uid, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def get_rfid_cards():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT id, label, uid, registered_at FROM rfid_cards")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "label": r[1], "uid": r[2], "registered_at": r[3]} for r in rows]

def delete_rfid_card(card_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("DELETE FROM rfid_cards WHERE id=?", (card_id,))
    conn.commit()
    conn.close()

def add_fingerprint(label, fp_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO fingerprints (label, fp_id, registered_at) VALUES (?,?,?)", (label, fp_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def get_fingerprints():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT id, label, fp_id, registered_at FROM fingerprints")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "label": r[1], "fp_id": r[2], "registered_at": r[3]} for r in rows]

def delete_fingerprint(fp_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("DELETE FROM fingerprints WHERE id=?", (fp_id,))
    conn.commit()
    conn.close()

def get_registered_phones():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT id, label, number, active, registered_at FROM registered_phones")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "label": r[1], "number": r[2], "active": bool(r[3]), "registered_at": r[4]} for r in rows]

def add_phone(label, number):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO registered_phones (label, number, registered_at) VALUES (?,?,?)", (label, number, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def delete_phone(phone_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("DELETE FROM registered_phones WHERE id=?", (phone_id,))
    conn.commit()
    conn.close()
