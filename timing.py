import math
import socket
import statistics
import struct
import time

from datetime import datetime, timedelta, timezone
from pathlib import Path


HistoryFile = Path(__file__).resolve().parent / "data" / "history.json"
ChinaTime = timezone(timedelta(hours=8))
NtpServers = ("ntp1.aliyun.com", "time.google.com", "pool.ntp.org")

MinDelay = 200
MaxDelay = 3000
DefaultDelay = 500
MaxPing = 5000
HistorySize = 10


def valid_ping(ping):
    return isinstance(ping, (int, float)) and math.isfinite(ping) and 0 < ping <= MaxPing


def clean_history(history):
    clean = []
    if not isinstance(history, list):
        return []

    for item in history:
        if not isinstance(item, dict) or not valid_ping(item.get("ping")):
            continue
        wake = item.get("wake", 0)
        if not isinstance(wake, (int, float)) or not math.isfinite(wake):
            wake = 0
        clean.append({
            "ping": round(item["ping"]),
            "wake": max(0, min(round(wake, 3), 100))
        })
    return clean[-HistorySize:]


def pick_delay(ping, history):
    history = clean_history(history)
    old_pings = [item["ping"] for item in history]

    if valid_ping(ping):
        current = ping
        if old_pings:
            current = current * 0.75 + statistics.median(old_pings) * 0.25
    elif old_pings:
        current = statistics.median(old_pings)
    else:
        return DefaultDelay / 1000

    wake = statistics.median([item["wake"] for item in history]) if history else 0
    delay = round(current / 2 + 100 + wake)
    return max(MinDelay, min(delay, MaxDelay)) / 1000


def add_history(history, ping, wake):
    history = clean_history(history)
    if not valid_ping(ping):
        return history
    history.append({
        "ping": round(ping),
        "wake": max(0, min(round(wake, 3), 100))
    })
    return history[-HistorySize:]


def ntp_stamp(seconds, fraction):
    return seconds - 2208988800 + fraction / 2 ** 32


def ntp_offset(host):
    packet = bytearray(48)
    packet[0] = 0x1B
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(4)
        before = time.time()
        sock.sendto(packet, (host, 123))
        data, _ = sock.recvfrom(48)
        after = time.time()

    if len(data) < 48:
        raise ConnectionError("Short NTP response")
    values = struct.unpack("!12I", data[:48])
    received = ntp_stamp(values[8], values[9])
    sent = ntp_stamp(values[10], values[11])
    return ((received - before) + (sent - after)) / 2


def synced_clock():
    for host in NtpServers:
        try:
            offset = ntp_offset(host)
            now = datetime.fromtimestamp(time.time() + offset, timezone.utc)
            return now.astimezone(ChinaTime), time.monotonic()
        except (OSError, ValueError, ConnectionError):
            pass
    raise ConnectionError("Could not sync time with an NTP server")


def clock_now(start, mono):
    return start + timedelta(seconds=time.monotonic() - mono)


def wait_text(seconds):
    seconds = max(0, int(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
