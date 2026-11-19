import os
import sys
import time
import urllib.request
from pathlib import Path

# Configure local Playwright browsers path if present
local_browsers = Path(__file__).parent / "ms-playwright"
if local_browsers.exists() and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers)

from playwright.sync_api import sync_playwright

# Attach thread to interactive desktop so GUI windows render on the user's monitor
try:
    import ctypes
    user32 = ctypes.WinDLL('user32')
    kernel32 = ctypes.WinDLL('kernel32')
    hdesk = user32.OpenDesktopW("default", 0, False, 0x01FF)
    if hdesk:
        user32.SetThreadDesktop(hdesk)
except Exception:
    pass

account_id = sys.argv[1].strip() if len(sys.argv) > 1 and sys.argv[1].strip() else "bolenstudio1993"
profile = Path(__file__).parent / "profiles" / account_id
profile.mkdir(parents=True, exist_ok=True)

print("=" * 65)
print(f"  Gemini Web API Call Router - Google Sign-in: {account_id}")
print("=" * 65)

# 1. Release background context from router and kill conflicting processes
print(f"\n[1/3] Preparing profile directory for '{account_id}'...")

def release_router_context():
    try:
        router_port = int(os.environ.get("PORT", "5550"))
        req = urllib.request.Request(f"http://127.0.0.1:{router_port}/accounts/{account_id}/release", data=b"", method="POST")
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass

def get_profile_file_lockers(file_path):
    try:
        import ctypes
        from ctypes import wintypes
        rstrtmgr = ctypes.WinDLL('rstrtmgr')

        class RM_UNIQUE_PROCESS(ctypes.Structure):
            _fields_ = [('dwProcessId', wintypes.DWORD), ('ProcessStartTime', wintypes.FILETIME)]

        class RM_PROCESS_INFO(ctypes.Structure):
            _fields_ = [
                ('Process', RM_UNIQUE_PROCESS),
                ('strAppName', wintypes.WCHAR * 256),
                ('strServiceShortName', wintypes.WCHAR * 64),
                ('ApplicationType', wintypes.UINT),
                ('AppStatus', wintypes.ULONG),
                ('TSSessionId', wintypes.DWORD),
                ('bRestartable', wintypes.BOOL),
            ]

        session_handle = wintypes.DWORD()
        session_key = (wintypes.WCHAR * 33)()
        if rstrtmgr.RmStartSession(ctypes.byref(session_handle), 0, session_key) != 0:
            return []
        try:
            path_arr = (wintypes.LPCWSTR * 1)(str(Path(file_path).resolve()))
            if rstrtmgr.RmRegisterResources(session_handle, 1, path_arr, 0, None, 0, None) != 0:
                return []
            n_needed = wintypes.UINT(0)
            n_proc = wintypes.UINT(0)
            reasons = wintypes.DWORD()
            res = rstrtmgr.RmGetList(session_handle, ctypes.byref(n_needed), ctypes.byref(n_proc), None, ctypes.byref(reasons))
            if res not in (0, 234) or n_needed.value == 0:
                return []
            n_proc.value = n_needed.value
            proc_infos = (RM_PROCESS_INFO * n_proc.value)()
            if rstrtmgr.RmGetList(session_handle, ctypes.byref(n_needed), ctypes.byref(n_proc), proc_infos, ctypes.byref(reasons)) != 0:
                return []
            return [proc_infos[i].Process.dwProcessId for i in range(n_proc.value)]
        finally:
            rstrtmgr.RmEndSession(session_handle)
    except Exception:
        return []

def kill_browser_locks():
    # 1. Ask router to release context
    release_router_context()

    # 2. Kill file lockers via Restart Manager
    targets = [
        profile / "lockfile",
        profile / "SingletonLock",
        profile / "Default" / "Network" / "Cookies",
        profile / "Default" / "Preferences",
        profile / "Profile 1" / "Network" / "Cookies",
    ]
    for tf in targets:
        if tf.exists():
            for pid in get_profile_file_lockers(tf):
                if pid and pid != os.getpid():
                    try:
                        import subprocess
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
                    except Exception:
                        pass

    # 3. Kill any processes where command line contains account_id
    import subprocess
    import base64
    ps_kill = f"""
    $searcher = [System.Management.ManagementObjectSearcher]::new("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name LIKE '%chrome%' OR Name LIKE '%headless%'")
    foreach ($r in $searcher.Get()) {{
        if ($r["CommandLine"] -like "*{account_id}*") {{
            $p = $r["ProcessId"]
            cmd.exe /c "taskkill /F /T /PID $p" | Out-Null
        }}
    }}
    """
    b64 = base64.b64encode(ps_kill.encode('utf-16le')).decode('ascii')
    try:
        subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", b64], capture_output=True, timeout=8)
    except Exception:
        pass

def clean_profile_locks():
    for lf in ["lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket", "SingletonSocket.sock"]:
        (profile / lf).unlink(missing_ok=True)
    for item in profile.glob("**/Singleton*"):
        try:
            item.unlink(missing_ok=True)
        except Exception:
            pass

kill_browser_locks()
time.sleep(0.5)
clean_profile_locks()

# 2. Launch Google Chrome in Headed Mode via Playwright
print(f"[2/3] Launching Google Chrome directly on your screen...")

launch_kwargs = {
    "user_data_dir": str(profile.resolve()),
    "headless": False,
    "viewport": None,
    "ignore_default_args": ["--enable-automation"],
    "args": [
        "--test-type",
        "--disable-blink-features=AutomationControlled",
        "--use-angle=d3d11",
        "--enable-gpu-rasterization",
        "--enable-features=NetworkService,NetworkServiceInProcess",
        "--disable-quic",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--window-position=100,100",
        "--window-size=1280,850",
        "--no-first-run",
        "--no-default-browser-check",
        "--profile-directory=Default",
    ]
}

logged_in = False
detected_cookies_count = 0

with sync_playwright() as p:
    context = None
    try:
        context = p.chromium.launch_persistent_context(channel="chrome", **launch_kwargs)
    except Exception as e_chrome:
        print(f"[NOTE] System Chrome channel fallback ({e_chrome}), launching Playwright Chromium...")
        try:
            context = p.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as e_all:
            print(f"[ERROR] Failed to launch browser: {e_all}")
            input("\nPress Enter to exit...")
            sys.exit(1)

    # Inject anti-detection stealth script
    try:
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    except Exception:
        pass

    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.bring_to_front()
    except Exception:
        pass
    try:
        page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=45000)
    except Exception as nav_e:
        print(f"[NOTE] Initial navigation: {nav_e}")

    print("\n" + "=" * 65)
    print("  >>> GOOGLE CHROME IS NOW OPEN ON YOUR SCREEN! <<<")
    print("  1. In the opened Chrome window, sign in to your Google Account.")
    print(f"  2. Account ID: '{account_id}'")
    print("  3. Complete sign-in until you see the Gemini chat screen.")
    print("=" * 65 + "\n")

    print("[3/3] Live Monitoring: Waiting for your Google Sign-in...")
    print(">>> Press [ENTER] in this window once you reach the Gemini chat screen (or close Chrome) <<<\n")

    import msvcrt

    browser_closed = False
    def on_close(_):
        global browser_closed
        browser_closed = True

    context.on("close", on_close)

    def check_context_auth(ctx):
        try:
            # Check if active page is on Gemini Web (and not stuck in accounts.google.com)
            pages = [p for p in ctx.pages if not p.is_closed()]
            on_gemini = any("gemini.google.com" in (p.url or "") and "accounts.google.com" not in (p.url or "") for p in pages)
            cookies = ctx.cookies()
            auth = [c['name'] for c in cookies if c['name'] in ('SID', 'SSID', '__Secure-1PSID', 'SAPISID')]
            # Both core Google auth tokens AND on Gemini Web
            is_valid = len(auth) >= 3 and on_gemini
            return is_valid, len(auth), on_gemini
        except Exception:
            return False, 0, False

    new_login_detected_at = None

    for i in range(600):  # Wait up to 10 minutes
        time.sleep(1)

        # 1. Check if browser was closed
        if browser_closed or not context.pages or all(p.is_closed() for p in context.pages):
            print("\n[INFO] Chrome window closed by user.")
            break

        # 2. Check if user pressed Enter in console
        if msvcrt.kbhit():
            msvcrt.getch()
            print("\n[INFO] [Enter] detected! Finalizing login...")
            break

        # 3. Check for Google auth cookies and Gemini navigation
        is_authed, auth_cnt, on_gemini = check_context_auth(context)
        if is_authed:
            detected_cookies_count = auth_cnt
            if not logged_in:
                logged_in = True
                new_login_detected_at = time.time()
                print(f"\n[OK] >>> GEMINI WEB SIGN-IN DETECTED ({auth_cnt} auth tokens found)! <<<")
                print(">>> Finalizing session in 6 seconds (or press [Enter] to finish immediately)...")
            elif new_login_detected_at and (time.time() - new_login_detected_at >= 6):
                print("\n[OK] Auto-finalizing login session...")
                break
        else:
            if (i + 1) % 5 == 0:
                status_str = "Redirecting to Gemini..." if auth_cnt >= 3 and not on_gemini else f"waiting for sign-in ({auth_cnt}/4 tokens)"
                print(f"      ... {status_str} ({i+1}s elapsed) [Press Enter or close Chrome when done]")

    # Check cookies one final time before closing
    if not logged_in:
        logged_in, detected_cookies_count = check_context_auth(context)

    # Gracefully close browser so all cookies and session files are flushed to disk
    try:
        context.close()
    except Exception:
        pass

# Post-close cleanup of any leftover locks
time.sleep(0.5)
kill_browser_locks()
clean_profile_locks()

if logged_in:
    print(f"\nActivating profile '{account_id}' in Gemini API Call Router...")
    try:
        router_port = int(os.environ.get("PORT", "5550"))
        req = urllib.request.Request(f"http://127.0.0.1:{router_port}/accounts/{account_id}/finish-login", data=b"", method="POST")
        res = urllib.request.urlopen(req, timeout=30)
        print(f"[SUCCESS] Account '{account_id}' successfully activated and verified in Router!")
    except Exception as e:
        print(f"[NOTE] Router activation note: {e}")

    print("\n" + "=" * 65)
    print(f"  ALL SET! {account_id} IS NOW READY FOR AUDIO TRANSCRIBE & CHAT!")
    print("=" * 65 + "\n")
else:
    print("\n[WARNING] No Google sign-in was detected before Chrome closed.")
    print("Please run this batch file again and complete the Google login.")

input("Press Enter to close this window...")
