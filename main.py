#!/usr/bin/env python3
"""
Garden-Cam Python side · refactored
  • PIR polled at 10 Hz with high/low logging
  • 30 s cooldown between captures
  • boot-snap vs continuous modes via CLI
  • better error handling, optional spidev/smbus
  • logging instead of print
"""
import os
import sys
import json
import re
import time
import signal
import argparse
import subprocess
import logging
from pathlib import Path
import datetime as dt

# Optional hardware libraries
try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None

try:
    import spidev
except ImportError:
    spidev = None

try:
    import smbus2
    SMBus = smbus2.SMBus
except ImportError:
    smbus2 = None
    SMBus = None

try:
    from pytz import timezone
except ImportError:
    timezone = None

try:
    from firebase_admin import credentials, initialize_app, storage, firestore
except ImportError:
    credentials = initialize_app = storage = firestore = None

# ── Constants ────────────────────────────────────────────────────────────────
TIME_ZONE           = "Europe/Copenhagen"
IMG_DIR             = Path(__file__).parent.resolve() / "images"
PIR_PIN             = 17
SPI_BUS, SPI_DEV    = 0, 0
SPI_CMD_BATT        = [0xA5]
I2C_ADDR            = 0x08
CMD_REQUEST_SHUTDOWN= 0x07
DETECTION_INTERVAL  = 10
BASE_DIR            = Path(__file__).parent.resolve()
CRED_FILE           = BASE_DIR / "trapapp-credentials.json"
ID_FILE             = Path.home() / ".id.json"

# ── Helpers ─────────────────────────────────────────────────────────────────
def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_trap_id():
    try:
        data = ID_FILE.read_text()
        return json.loads(data).get("TRAPID")
    except Exception as e:
        logging.error(f"Could not load TRAPID from {ID_FILE}: {e}")
        sys.exit(1)


def fetch_config(db, trap_id):
    try:
        trap_doc = db.collection("traps").document(trap_id).get()
        if not trap_doc.exists:
            return {}
        owner = trap_doc.to_dict().get("owner")
        user_doc = (
            db.collection("users").document(owner)
              .collection("traps").document(trap_id)
              .get()
        )
        return user_doc.to_dict().get("config", {}) if user_doc.exists else {}
    except Exception as e:
        logging.warning(f"Error fetching config: {e}")
        return {}


def parse_time(s: str) -> dt.time:
    try:
        hh, mm, ss = map(int, s.split(":"))
        return dt.time(hh, mm, ss)
    except Exception:
        return dt.time(0, 0, 0)


def in_ignore_window(now: dt.datetime, start: dt.time, end: dt.time) -> bool:
    if start == end:
        return False
    t = now.time()
    if start < end:
        return start <= t < end
    return t >= start or t < end


def early_snap() -> Path:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    filename = dt.datetime.now().strftime("%Y%m%d_%H%M%S.jpg")
    path = IMG_DIR / filename
    cmd = [
        "libcamera-still", "-n", "-t", "800",
        "--width", "1920", "--height", "1080",
        "--encoding", "jpg", "-o", str(path)
    ]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0:
        logging.error(f"Camera capture failed: {res.stderr.decode().strip()}")
        sys.exit(1)
    return path


def read_battery(spi_dev) -> int:
    if not spi_dev:
        return 0
    try:
        resp = spi_dev.xfer2(SPI_CMD_BATT + [0])
        return max(0, min(100, resp[1]))
    except Exception as e:
        logging.warning(f"Battery read failed: {e}")
        return 0


def read_wifi_signal() -> int:
    try:
        out = subprocess.check_output(
            ["iw", "dev", "wlan0", "link"],
            text=True, stderr=subprocess.DEVNULL
        )
        m = re.search(r"signal:\s*(-\d+)\s*dBm", out)
        dbm = int(m.group(1)) if m else -90
        return max(0, min(100, round((dbm + 90) * 100 / 60)))
    except Exception:
        return 0


def upload_to_firebase(db, bucket, trap_id, img_path, batt, sig):
    now = dt.datetime.now(timezone(TIME_ZONE)) if timezone else dt.datetime.utcnow()
    data = {
        "server_ts": now,
        "trap_id": trap_id,
        "battery_charge": batt,
        "signal_strength": sig,
        "trigger_type": 1,
    }
    doc_ref = db.collection("imageInbox").document()
    doc_ref.set(data)
    blob = bucket.blob(f"images/{doc_ref.id}/{img_path.name}")
    blob.upload_from_filename(str(img_path))
    blob.make_public()
    doc_ref.update({"url": blob.public_url})
    img_path.unlink()
    logging.info(f"Uploaded {img_path.name} batt={batt}% sig={sig}%")


def send_shutdown(bus_dev):
    if not bus_dev:
        return
    try:
        bus_dev.write_byte(I2C_ADDR, CMD_REQUEST_SHUTDOWN)
        logging.info("Shutdown command sent via I2C")
    except Exception as e:
        logging.warning(f"Shutdown via I2C failed: {e}")


def cleanup(spi_dev, bus_dev):
    if GPIO:
        GPIO.cleanup()
    if spi_dev:
        spi_dev.close()
    if bus_dev:
        bus_dev.close()
    logging.info("Cleanup complete, exiting.")
    sys.exit(0)

# ── Modes ───────────────────────────────────────────────────────────────────
def continuous_mode(spi_dev, bus_dev, cfg):
    start = parse_time(cfg.get("IgnoreTimeFrom", "00:00:00"))
    end   = parse_time(cfg.get("IgnoreTimeTo",   "00:00:00"))
    last_shot = 0.0
    last_pir = False
    logging.info("Entering continuous mode.")
    while True:
        now = dt.datetime.now(timezone(TIME_ZONE)) if timezone else dt.datetime.utcnow()
        if in_ignore_window(now, start, end):
            time.sleep(1)
            continue
        current_pir = GPIO.input(PIR_PIN) if GPIO else False
        if current_pir != last_pir:
            state = "HIGH" if current_pir else "LOW"
            logging.info(f"PIR {state}")
            last_pir = current_pir
        if current_pir:
            if time.time() - last_shot >= DETECTION_INTERVAL:
                last_shot = time.time()
                img = early_snap()
                batt = read_battery(spi_dev)
                sig  = read_wifi_signal()
                upload_to_firebase(db, bucket, TRAP_ID, img, batt, sig)
        time.sleep(0.1)


def boot_snap_mode(spi_dev, bus_dev):
    logging.info("Entering boot-snap mode.")
    img = early_snap()
    batt = read_battery(spi_dev)
    sig  = read_wifi_signal()
    upload_to_firebase(db, bucket, TRAP_ID, img, batt, sig)
    send_shutdown(bus_dev)
    cleanup(spi_dev, bus_dev)

# ── Entry Point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    setup_logging()
    parser = argparse.ArgumentParser(description="Garden-Cam control script")
    parser.add_argument(
        "--boot-snap", action="store_true",
        help="Capture once on boot then shut down"
    )
    args = parser.parse_args()

    TRAP_ID = load_trap_id()

    # GPIO setup
    if GPIO:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(PIR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    # SPI init
    spi_dev = None
    if spidev:
        try:
            spi_dev = spidev.SpiDev()
            spi_dev.open(SPI_BUS, SPI_DEV)
            spi_dev.max_speed_hz = 500_000
        except Exception as e:
            logging.warning(f"SPI init failed: {e}")
            spi_dev = None

    # I2C init
    bus_dev = None
    if SMBus:
        try:
            bus_dev = SMBus(1)
        except Exception as e:
            logging.warning(f"I2C init failed: {e}")
            bus_dev = None

    # Firebase init
    if credentials and initialize_app and firestore and storage:
        try:
            cred = credentials.Certificate(str(CRED_FILE))
            initialize_app(cred, {"storageBucket": "trapapp-2f398.appspot.com"})
            db     = firestore.client()
            bucket = storage.bucket()
        except Exception as e:
            logging.error(f"Firebase init failed: {e}")
            sys.exit(1)
    else:
        logging.error("Missing Firebase libraries.")
        sys.exit(1)

    # Config fetch
    cfg = fetch_config(db, TRAP_ID)

    # Signal handlers
    signal.signal(signal.SIGINT, lambda *_: cleanup(spi_dev, bus_dev))
    signal.signal(signal.SIGTERM, lambda *_: cleanup(spi_dev, bus_dev))

    # Run selected mode
    if args.boot_snap:
        boot_snap_mode(spi_dev, bus_dev)
    else:
        continuous_mode(spi_dev, bus_dev, cfg)
