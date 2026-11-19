"""
manual_chrome.py: Dedicated Google Chrome launcher for manual human control.
Allows the user to browse, chat with Gemini directly, adjust settings,
or inspect accounts with zero time limits, no automation scripts, and no auto-closing.
When the user closes Chrome (or presses [Enter]), it cleans locks and notifies
the Gemini Web API Call Router to re-attach the account to the headless pool.
"""

import sys
import os
import time
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

# Ensure UTF-8 output on Windows
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

if len(sys.argv) < 2:
    print("[ERROR] Missing account_id argument.")
    input("\nPress Enter to exit...")
    sys.exit(1)

account_id = sys.argv[1].strip()
BASE_DIR = Path(__file__).resolve().parent
profile_dir = BASE_DIR / "profiles" / account_id
profile_dir.mkdir(parents=True, exist_ok=True)
(profile_dir / "Default").mkdir(parents=True, exist_ok=True)

def find_chrome():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
            val, _ = winreg.QueryValueEx(key, "")
            if val and os.path.exists(val):
                return str(val)
    except Exception:
        pass
    for p in [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
        str(BASE_DIR / "ms-playwright" / "chromium-1248" / "chrome-win64" / "chrome.exe"),
    ]:
        if os.path.exists(p):
            return p
    return "chrome.exe"

def clean_profile_locks():
    for lf in ["lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket", "SingletonSocket.sock"]:
        try:
            (profile_dir / lf).unlink(missing_ok=True)
        except Exception:
            pass
    for item in profile_dir.glob("**/Singleton*"):
        try:
            item.unlink(missing_ok=True)
        except Exception:
            pass

clean_profile_locks()

chrome_exe = find_chrome()

print("=" * 68)
print("    GEMINI WEB API CALL ROUTER - MANUAL CHROME CONTROL")
print(f"    Account: {account_id}")
print("=" * 68)
print("\n[1/2] Opening Google Chrome for manual browsing...")
print(f"      Profile Directory: {profile_dir.resolve()}")
print(f"      Chrome Path:       {chrome_exe}")

chrome_cmd = [
    chrome_exe,
    f"--user-data-dir={str(profile_dir.resolve())}",
    "--profile-directory=Default",
    "--no-first-run",
    "--no-default-browser-check",
    "--window-size=1300,900",
    "https://gemini.google.com/app"
]

try:
    proc = subprocess.Popen(chrome_cmd)
except Exception as e:
    print(f"\n[ERROR] Failed to start Google Chrome: {e}")
    input("\nPress Enter to exit...")
    sys.exit(1)

print("\n[2/2] Google Chrome is now running on your screen!")
print("=" * 68)
print("  >>> MANUAL CONTROL & GOOGLE LOGIN ACTIVE <<<")
print("  - You have FULL manual control with NO timeouts or auto-closing.")
print("  - You can sign in to Google, complete 2FA, or chat/browse freely.")
print("  - All login cookies and session tokens are saved automatically.")
print("  - When you are finished:")
print("      -> Simply CLOSE the Google Chrome window, OR")
print("      -> Press [Enter] here in this console window.")
print("=" * 68 + "\n")

import msvcrt

while True:
    if proc.poll() is not None:
        print("\n[INFO] Google Chrome window closed by user.")
        break
    if msvcrt.kbhit():
        key = msvcrt.getch()
        if key in (b'\r', b'\n', b'q', b'Q'):
            print("\n[INFO] Exit requested via console. Terminating Chrome...")
            try:
                proc.terminate()
            except Exception:
                pass
            break
    time.sleep(0.5)

print("\n[INFO] Chrome exited. Waiting 1.5s for session cookies to persist...")
time.sleep(1.5)
clean_profile_locks()

print(f"[INFO] Re-attaching account '{account_id}' to Router headless pool...")
try:
    router_port = int(os.environ.get("PORT", "5550"))
    req = urllib.request.Request(
        f"http://127.0.0.1:{router_port}/accounts/{account_id}/close-manual-chrome",
        data=b"",
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        print(f"[SUCCESS] Account '{account_id}' is now re-attached and READY in Router!")
except Exception as err:
    print(f"[NOTE] Router re-attach notification: {err}")

print("\n" + "=" * 68)
print(f"  ALL DONE! '{account_id}' is ready for automated routing.")
print("=" * 68)
print("Closing window in 3 seconds...")
time.sleep(3)
