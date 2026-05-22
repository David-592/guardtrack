# SMS is handled by the ESP32 SIM7600G directly
# This file queues SMS commands that the ESP32 picks up on next poll

pending_sms = []

def queue_sms(message):
    """Queue an SMS to be sent by the ESP32"""
    pending_sms.append(message)

def get_pending_sms():
    """ESP32 polls this — returns pending messages and clears queue"""
    msgs = pending_sms.copy()
    pending_sms.clear()
    return msgs
