#!/usr/bin/env python3
"""
Garden-cam with:
• PIR polled at 10 Hz
• 30 s fixed cooldown between captures
• trigger_type=1 (PIR) numeric
• RPI_ALWAYS_ON flag for continuous vs. boot-snap modes
"""
# ── FAST SNAP for boot-snap mode ───────────────────────────────────────────────
import os, subprocess, datetime, time, sys
from pathlib import Path

RPI_ALWAYS_ON = True               # False = boot-snap-exit mode
TIME_ZONE     = "Europe/Copenhagen"
IMG_DIR       = Path(__file__).parent.resolve() / "images"

def early_snap() -> Path:
    """Capture one image ASAP on boot."""
    IMG_DIR.mkdir(exist_ok=True)
    fn = IMG_DIR / datetime.datetime.now().strftime("%Y%m%d_%H%M%S.jpg")
    cmd = [
        "libcamera-still","-n","-t","800",
        "--width","1920","--height","1080",
        "--encoding","jpg","-o",str(fn)
    ]
    if subprocess.run(cmd, capture_output=True).returncode:
        print("Early camera capture failed")
        sys.exit(1)
    return fn

if not RPI_ALWAYS_ON:
    FIRST_IMAGE = early_snap()
else:
    FIRST_IMAGE = None

# ── SLOW IMPORTS ───────────────────────────────────────────────────────────────
import json, re, signal, datetime as dt
import RPi.GPIO as GPIO, spidev, smbus2
from pytz import timezone
from firebase_admin import credentials, initialize_app, storage, firestore

# ── CONSTANTS & SETUP ──────────────────────────────────────────────────────────
PIR_PIN            = 17
SPI_BUS, SPI_DEV   = 0, 0
SPI_CMD_BATT       = [0xA5]
I2C_ADDR           = 0x08
CMD_REQUEST_SHUTDOWN = 0x07

BASE_DIR    = Path(__file__).parent.resolve()
CRED_FILE   = BASE_DIR / "trapapp-credentials.json"
ID_FILE     = Path.home() / ".id.json"
DETECTION_INTERVAL = 30     # fixed 30 s between captures

# Firestore init
cred = credentials.Certificate(str(CRED_FILE))
initialize_app(cred, {"storageBucket": "trapapp-2f398.appspot.com"})
db, bucket = firestore.client(), storage.bucket()
TRAP_ID = json.loads(ID_FILE.read_text())["TRAPID"]

# Pull config for ignore window (still respected)
def fetch_config():
    t = db.collection("traps").document(TRAP_ID).get()
    if not t.exists: return {}
    owner = t.to_dict()["owner"]
    u = (db.collection("users").document(owner)
          .collection("traps").document(TRAP_ID).get())
    return u.to_dict().get("config", {}) if u.exists else {}
cfg = fetch_config()

def parse_time(s):
    h, m, s = map(int, s.split(":"))
    return dt.time(h, m, s)

ignore_from = parse_time(cfg.get("IgnoreTimeFrom", "00:00:00"))
ignore_to   = parse_time(cfg.get("IgnoreTimeTo",   "00:00:00"))

# GPIO / SPI / I2C setup
GPIO.setmode(GPIO.BCM)
GPIO.setup(PIR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

try:
    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEV)
    spi.max_speed_hz = 500_000
except FileNotFoundError:
    spi = None

try:
    bus = smbus2.SMBus(1)
except FileNotFoundError:
    bus = None

def cleanup(*_):
    GPIO.cleanup()
    if spi: spi.close()
    if bus: bus.close()
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

# ── HELPERS ───────────────────────────────────────────────────────────────────
def read_battery():
    if not spi: return 69
    try:
        resp = spi.xfer2(SPI_CMD_BATT + [0])
        return max(0, min(100, resp[1]))
    except:
        return 69

def read_wifi():
    try:
        out = subprocess.check_output(
            ["iw", "dev", "wlan0", "link"],
            text=True, stderr=subprocess.DEVNULL
        )
        m = re.search(r"signal:\s*(-\d+) dBm", out)
        dbm = int(m.group(1)) if m else -90
        return max(0, min(100, round((dbm + 90) * 100 / 60)))
    except:
        return 0

def in_ignore_window(now):
    if ignore_from == ignore_to:
        return False
    t = now.time()
    if ignore_from < ignore_to:
        return ignore_from <= t < ignore_to
    return t >= ignore_from or t < ignore_to

def capture_image():
    """Use libcamera-still for capture."""
    path = early_snap()  # same function works
    return path

def upload_to_firebase(img_path, batt, sig):
    now = dt.datetime.now(timezone(TIME_ZONE))
    data = {
        "server_ts": now,
        "trap_id": TRAP_ID,
        "battery_charge": batt,
        "signal_strength": sig,
        "trigger_type": 1     # numeric PIR
    }
    ref = db.collection("imageInbox").document()
    ref.set(data)
    iid = ref.id

    blob = bucket.blob(f"images/{iid}/{img_path.name}")
    blob.upload_from_filename(str(img_path))
    blob.make_public()
    ref.update({"url": blob.public_url})

    img_path.unlink()
    print(f"✓ uploaded {img_path.name} | batt={batt}% sig={sig}%")

def send_shutdown():
    if bus:
        try:
            bus.write_byte(I2C_ADDR, CMD_REQUEST_SHUTDOWN)
        except Exception as e:
            print("I2C shutdown failed:", e)

# ── MODES ────────────────────────────────────────────────────────────────────
def continuous_mode():
    last_shot = 0.0
    print("Garden-cam armed (always-on)…")
    while True:
        now_ts = time.time()
        now_dt = dt.datetime.now(timezone(TIME_ZONE))

        if in_ignore_window(now_dt):
            time.sleep(1)
            continue

        if GPIO.input(PIR_PIN) and (now_ts - last_shot) >= DETECTION_INTERVAL:
            last_shot = now_ts
            try:
                upload_to_firebase(capture_image(), read_battery(), read_wifi())
            except Exception as e:
                print("ERROR during upload:", e)

        time.sleep(0.1)   # poll PIR at 10 Hz

def boot_snap_mode():
    print("Boot-snap mode: capturing one image…")
    try:
        upload_to_firebase(FIRST_IMAGE, read_battery(), read_wifi())
    except Exception as e:
        print("ERROR during boot-snap:", e)
    send_shutdown()
    print("Shutdown command sent; exiting.")
    cleanup()

# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if RPI_ALWAYS_ON:
        continuous_mode()
    else:
        boot_snap_mode()