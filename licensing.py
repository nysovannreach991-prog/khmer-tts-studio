"""License key — អនុញ្ញាតតែអ្នកដែលមាន key ពីម្ចាស់កម្មវិធីប៉ុណ្ណោះ។

Key = "AT1-" + base64url(payload + ហត្ថលេខា Ed25519)។ payload:
    version(1) · id(4) · machine(6, សូន្យ = គ្រប់កុំព្យូទ័រ) · ថ្ងៃចេញ(2) · ថ្ងៃផុតកំណត់(2, 0 = គ្មាន) · ឈ្មោះ(utf-8)
ថ្ងៃគិតចាប់ពី 2024-01-01។ ម្ចាស់បង្កើត key ដោយ license_admin.bat (private key នៅក្នុង %APPDATA% របស់ម្ចាស់ប៉ុណ្ណោះ)។
បើ PUBLIC_KEY ទទេ (ម្ចាស់មិនទាន់រៀបចំ) — កម្មវិធីដំណើរការដោយមិនត្រូវការ key។
"""
import base64
import datetime
import hashlib
import json
import os
import struct

import ed25519

PUBLIC_KEY = "595f9addbea9c21acd0cbe2ad3a39c7c00a0ebf6a63dc6f30cc6f2bc4aca9be4"  # license_admin.py សរសេរនៅទីនេះពេលរៀបចំលើកដំបូង

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "AITeam1")
KEY_FILE = os.path.join(DATA_DIR, "license.key")
PRIVATE_FILE = os.path.join(DATA_DIR, "private.key")      # ម្ចាស់តែប៉ុណ្ណោះ
STATE_FILE = os.path.join(DATA_DIR, "license_state.json")  # ថ្ងៃចុងក្រោយដែលបានឃើញ + បញ្ជី revoke ចុងក្រោយ
REVOKED_FILE = os.path.join(APP_DIR, "revoked.json")       # ផ្សព្វផ្សាយតាម Update
PREFIX = "AT1-"
EPOCH = datetime.date(2024, 1, 1)
ANY_MACHINE = b"\0" * 6


class LicenseError(Exception):
    pass


def enabled():
    return bool(PUBLIC_KEY)


# ---------------- machine code ----------------
def _machine_raw():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                             winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        guid = winreg.QueryValueEx(key, "MachineGuid")[0]
    except OSError:
        import uuid
        guid = str(uuid.getnode())
    return hashlib.sha256(f"AITeam1:{guid}".encode()).digest()[:6]


def machine_code(raw=None):
    """ឧ. "K4ZQM-2XH7A" — អ្នកប្រើផ្ញើលេខនេះទៅម្ចាស់ ដើម្បីទទួលបាន key សម្រាប់កុំព្យូទ័រនេះ"""
    code = base64.b32encode(raw or _machine_raw()).decode().rstrip("=")
    return f"{code[:5]}-{code[5:]}"


def parse_machine_code(code):
    code = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    if not code:
        return ANY_MACHINE
    try:
        raw = base64.b32decode(code + "=" * (-len(code) % 8))
    except ValueError:
        raw = b""
    if len(raw) != 6:
        raise LicenseError("Machine code មិនត្រឹមត្រូវ (ឧ. K4ZQM-2XH7A)")
    return raw


# ---------------- key ----------------
def _days(date):
    return (date - EPOCH).days


def _date(days):
    return EPOCH + datetime.timedelta(days=days)


def make_key(secret, name, machine=ANY_MACHINE, expires=None, key_id=None):
    """ម្ចាស់ប៉ុណ្ណោះ — បង្កើត key (expires = datetime.date ឬ None)"""
    name_b = name.strip().encode("utf-8")[:48]
    payload = struct.pack(">B4s6sHH", 1, key_id or os.urandom(4), machine, _days(datetime.date.today()),
                          _days(expires) if expires else 0) + name_b
    blob = payload + ed25519.sign(secret, payload)
    return PREFIX + base64.urlsafe_b64encode(blob).decode().rstrip("=")


def decode(key):
    """ពិនិត្យហត្ថលេខា ហើយត្រឡប់ព័ត៌មាន key (មិនទាន់ពិនិត្យកុំព្យូទ័រ/ថ្ងៃផុតកំណត់)"""
    text = "".join((key or "").split())
    if text.upper().startswith(PREFIX):
        text = text[len(PREFIX):]
    try:
        blob = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        raise LicenseError("Key មិនត្រឹមត្រូវ — សូមពិនិត្យថាបាន copy គ្រប់តួអក្សរ") from None
    if len(blob) < 15 + 64:
        raise LicenseError("Key មិនត្រឹមត្រូវ — ខ្លីពេក")
    payload, sig = blob[:-64], blob[-64:]
    if not ed25519.verify(bytes.fromhex(PUBLIC_KEY), payload, sig):
        raise LicenseError("Key មិនត្រឹមត្រូវ (ហត្ថលេខាខុស)")
    ver, kid, machine, issued, expires = struct.unpack(">B4s6sHH", payload[:15])
    if ver != 1:
        raise LicenseError("Key ជាកំណែថ្មីជាងកម្មវិធីនេះ — សូម Update កម្មវិធី")
    return {"id": kid.hex(), "machine": machine, "issued": _date(issued),
            "expires": _date(expires) if expires else None, "name": payload[15:].decode("utf-8", "replace")}


# ---------------- state (ថ្ងៃ + revoke) ----------------
def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def _today():
    """ថ្ងៃនេះ — មិនអនុញ្ញាតឱ្យថយក្រោយថ្ងៃចុងក្រោយដែលបានឃើញ (ការពារការប្តូរម៉ោងកុំព្យូទ័រថយក្រោយ)"""
    state = _load_json(STATE_FILE, {})
    today = datetime.date.today()
    try:
        last = datetime.date.fromisoformat(state.get("last_seen", ""))
    except ValueError:
        last = today
    if today > last or "last_seen" not in state:
        state["last_seen"] = today.isoformat()
        _save_state(state)
    return max(today, last)


def revoked_ids():
    ids = set(_load_json(REVOKED_FILE, {}).get("revoked", []))
    ids |= set(_load_json(STATE_FILE, {}).get("revoked", []))
    return ids


def remember_revoked(ids):
    """បញ្ជី revoke ដែលទាញពី GitHub — រក្សាទុក ដើម្បីកុំឱ្យគេចដោយបិទ Internet"""
    state = _load_json(STATE_FILE, {})
    state["revoked"] = sorted(set(state.get("revoked", [])) | set(ids))
    _save_state(state)


# ---------------- check ----------------
def check(key):
    """ត្រឡប់ព័ត៌មាន key បើត្រឹមត្រូវសម្រាប់កុំព្យូទ័រនេះ, បើមិនដូច្នោះ raise LicenseError"""
    info = decode(key)
    if info["machine"] != ANY_MACHINE and info["machine"] != _machine_raw():
        raise LicenseError(f"Key នេះសម្រាប់កុំព្យូទ័រផ្សេង — Machine code នៃកុំព្យូទ័រនេះ៖ {machine_code()}")
    if info["id"] in revoked_ids():
        raise LicenseError("Key នេះត្រូវបានដកសិទ្ធិហើយ — សូមទាក់ទងម្ចាស់កម្មវិធី")
    if info["expires"] and _today() > info["expires"]:
        raise LicenseError(f"Key នេះផុតកំណត់នៅ {info['expires'].isoformat()} — សូមទាក់ទងម្ចាស់កម្មវិធី")
    return info


def is_owner():
    """កុំព្យូទ័ររបស់ម្ចាស់ (មាន private key ដែលត្រូវនឹង PUBLIC_KEY) — មិនត្រូវការ key"""
    try:
        with open(PRIVATE_FILE, encoding="utf-8") as f:
            secret = bytes.fromhex(f.read().strip())
        return ed25519.public_key(secret).hex() == PUBLIC_KEY
    except (OSError, ValueError):
        return False


def saved_key():
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def activate(key):
    """ពិនិត្យ key ហើយរក្សាទុក។ ត្រឡប់ព័ត៌មាន key"""
    info = check(key)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        f.write("".join(key.split()))
    return info


def status():
    """(ok, info ឬ None, សារកំហុស)"""
    if not enabled():
        return True, None, ""
    if is_owner():
        return True, {"name": "ម្ចាស់កម្មវិធី", "expires": None, "id": "owner", "owner": True}, ""
    key = saved_key()
    if not key:
        return False, None, ""
    try:
        return True, check(key), ""
    except LicenseError as e:
        return False, None, str(e)


def describe(info):
    if not info:
        return "គ្មាន License"
    if info.get("owner"):
        return "👑 ម្ចាស់កម្មវិធី"
    exp = f"ផុតកំណត់ {info['expires'].isoformat()}" if info["expires"] else "គ្មានថ្ងៃផុតកំណត់"
    return f"👤 {info['name']} · {exp}"


def fetch_revoked():
    """ទាញ revoked.json ចុងក្រោយពី GitHub (ឱ្យការដកសិទ្ធិមានប្រសិទ្ធភាពភ្លាម មិនចាំ Update)"""
    import updater
    cfg = updater.load_config()
    if not cfg:
        return set()
    data = json.loads(updater._get(updater._raw_url(cfg, updater._latest_ref(cfg), "revoked.json")))
    ids = set(data.get("revoked", []))
    remember_revoked(ids)
    return ids
