"""Gemini Router ក្នុងកម្មវិធី (Direct) — ប្រើ engine របស់ Gemini Router ផ្ទាល់ក្នុង process នេះ
(Playwright + Chrome profile ដែលចូល Google រួចហើយ) ដោយមិនបាច់បើក Router server (port 5550) ដាច់ដោយឡែក។

របៀបដំណើរការ: ថត Router (មាន engine.py + profiles/) → sys.path → import engine → account_manager.initialize()
ក្នុង thread ដែលមាន asyncio loop ផ្ទាល់ · សំណើបកប្រែ (ទម្រង់ OpenAI chat/completions) → execute_gemini_chat
ដូច routers/api.py ធ្វើ (ជ្រើសគណនី · ប្តូរគណនីពេលបរាជ័យ · rate-limit → cooldown)។

បើ Router server កំពុងដំណើរការនៅខាងក្រៅរួចហើយ (port 5550) — Chrome profile ត្រូវចាក់សោដោយវា → មិនអាចបើកម្តងទៀត
→ ផ្ញើតាម HTTP ទៅ 127.0.0.1:5550 ជំនួស (លទ្ធផលដូចគ្នា)។
"""
import asyncio
import base64
import glob
import json
import logging
import logging.handlers
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROUTER_PORT = 5550
HTTP_URL = f"http://127.0.0.1:{ROUTER_PORT}/v1/chat/completions"
# បញ្ជី model ពេល engine មិនទាន់ផ្ទុក (ដូច SUPPORTED_MODELS របស់ Router)
FALLBACK_MODELS = [
    ("gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite"), ("gemini-3.5-flash-lite-low", "Gemini 3.5 Flash Lite (Low)"),
    ("gemini-3.8-flash", "Gemini 3.8 Flash"), ("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)"),
    ("gemini-3.1-pro", "Gemini 3.1 Pro"), ("gemini-3.1-pro-low", "Gemini 3.1 Pro (Low)"),
    ("gemini-4-argon", "Gemini 4 Argon"), ("gemini-4-hydrogen", "Gemini 4 Hydrogen"),
    ("gemini-web", "Gemini Web (Auto)"),
]
RECOVERY_MODEL = "gemini-3.5-flash-lite"  # Router ប្រើ model លឿននេះ ពេល Gemini Web error / គាំង

_lock = threading.Lock()
_state = {"status": "off", "msg": "", "dir": "", "error": ""}  # off · starting · ready · external · error
_loop = None
_thread = None
_engine = None
_ready = threading.Event()
_log = logging.getLogger("gemini-direct")
# Router ដែលមកជាមួយ Setup (build_installer.py ដាក់ engine ក្នុងថតកម្មវិធី) — អ្នកប្រើមិនចាំបាច់មាន Folder Router ទេ
BUNDLED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemini_router")


# ---------------------------------------------------------------- ថត Router
def is_valid(folder):
    return bool(folder) and os.path.isfile(os.path.join(folder, "engine.py"))


def find_router_dir(hint=""):
    """ថត Router ដែលមាន engine.py — hint (ពីការកំណត់) មុន · បន្ទាប់មក ទីតាំងធម្មតា (ថ្មីបំផុតមុន)"""
    if is_valid(hint):
        return hint
    if is_valid(BUNDLED):
        return BUNDLED
    patterns = []
    for root in (os.environ.get("GEMINI_ROUTER_DIR", ""), "D:/Software", os.path.join(os.path.expanduser("~"), "Desktop"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "AITeam1")):
        if not root:
            continue
        patterns += [os.path.join(root, "engine.py"), os.path.join(root, "*", "engine.py"),
                     os.path.join(root, "*", "*", "engine.py")]
    found = []
    for pat in patterns:
        for p in glob.glob(pat):
            folder = os.path.dirname(p)
            if "ms-playwright" in folder or folder in found:
                continue
            # ត្រូវជា Router ពិត (មាន routers/ ឬ profiles/) — មិនមែន engine.py ផ្សេង
            if os.path.isdir(os.path.join(folder, "routers")) or os.path.isdir(os.path.join(folder, "profiles")):
                found.append(folder)
    found.sort(key=lambda f: os.path.getmtime(os.path.join(f, "engine.py")), reverse=True)
    return found[0] if found else ""


def server_running(port=ROUTER_PORT):
    """Router server (ដាច់ដោយឡែក) កំពុងស្តាប់លើ port នេះ?"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------- ស្ថានភាព
def status():
    """{status, msg, dir, error, accounts: [(id, status, plan)], ready: n}"""
    with _lock:
        out = dict(_state)
    out["accounts"], out["ready"] = [], 0
    if _engine is not None:
        try:
            for acc in list(_engine.account_manager.accounts.values()):
                out["accounts"].append((acc.account_id, acc.status, getattr(acc, "plan", "")))
                if acc.status == "ready" and not getattr(acc, "is_paused", False):
                    out["ready"] += 1
        except Exception:  # noqa: BLE001
            pass
    return out


NO_ACCOUNT = "⚠ មិនទាន់មានគណនី Gemini — បើកទំព័រ Gemini Router រួចចុច ➕ Manual Account Login ដើម្បីចូល Google"


def status_text():
    """អត្ថបទខ្លីសម្រាប់ GUI"""
    st = status()
    s = st["status"]
    if s == "off":
        return "○ មិនទាន់បើក"
    if s == "starting":
        return "⏳ កំពុងបើក Chrome profile… " + (st["msg"] or "")
    if s == "external":
        return f"↪ Router server កំពុងដំណើរការខាងក្រៅ (port {ROUTER_PORT}) → ប្រើវាតាម HTTP"
    if s == "error":
        return f"⚠ {st['error'][:200]}"
    if not st["ready"] and not any(a[1] == "ready" for a in st["accounts"]):
        need = [a[0] for a in st["accounts"] if a[1] == "needs_login"]
        return NO_ACCOUNT + (f" · ត្រូវចូល Google ម្តងទៀត: {', '.join(need)}" if need else "")
    names = ", ".join(a[0] for a in st["accounts"] if a[1] == "ready") or "—"
    need = [a[0] for a in st["accounts"] if a[1] == "needs_login"]
    txt = f"✓ រួចរាល់ · {st['ready']}/{len(st['accounts'])} គណនី ({names})"
    if need:
        txt += f" · ត្រូវចូល Google ម្តងទៀត: {', '.join(need)}"
    return txt


def _set(status_, msg="", error=""):
    with _lock:
        _state.update(status=status_, msg=msg, error=error)


def active():
    """engine ក្នុង process នេះ រួចរាល់ (មិនមែន external)"""
    return _state["status"] == "ready" and _engine is not None and _loop is not None


# ---------------------------------------------------------------- បើក / បិទ
def start(router_dir, log_path=""):
    """បើក engine ក្នុង thread ផ្ទាល់ (មិនរង់ចាំ) — ហៅម្តងទៀតពេលកំពុង/រួចរាល់ = មិនធ្វើអ្វី
    log_path = ឯកសារកំណត់ត្រា engine (ទទេ → router_direct.log ក្បែរកម្មវិធី)"""
    global _thread
    with _lock:
        if _state["status"] in ("starting", "ready", "external") and _thread and _thread.is_alive():
            return
        _state.update(status="starting", msg="", error="", dir=router_dir)
        _ready.clear()
        _thread = threading.Thread(target=_run, args=(router_dir, log_path), daemon=True, name="gemini-direct")
        _thread.start()


def wait_ready(timeout=180):
    """រង់ចាំរហូត ready / external / error → True បើប្រើបាន (ready ឬ external)"""
    _ready.wait(timeout)
    return _state["status"] in ("ready", "external")


def _bundled_version(site, name):
    """កំណែ package ក្នុង site-packages ដែល Router ភ្ជាប់មកជាមួយ (ពីឈ្មោះថត name-X.Y.Z.dist-info) ឬ "" """
    for d in glob.glob(os.path.join(site, f"{name}-*.dist-info")):
        return os.path.basename(d)[len(name) + 1: -len(".dist-info")]
    return ""


def _vtuple(v):
    out = []
    for part in v.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def _use_bundled_playwright(router_dir):
    """Router ភ្ជាប់ Python ផ្ទាល់ខ្លួន (python/Lib/site-packages) ជាមួយ Playwright ថ្មី (ឧ. 1.64) — Playwright ចាស់
    របស់ system (ឧ. 1.44) ធ្វើឱ្យ driver (node) គាំង "TargetClosedError" ពេល Chrome profile មួយបិទភ្លាម → Chrome ទាំងអស់ដាច់។
    → ផ្ទុក playwright (+greenlet, pyee) ពី site-packages របស់ Router បើវាថ្មីជាង ឬ system គ្មាន · Python ត្រូវជា 3.11 ដូចគ្នា"""
    site = os.path.join(router_dir, "python", "Lib", "site-packages")
    bundled = _bundled_version(site, "playwright") if os.path.isdir(site) else ""
    if not bundled or "playwright" in sys.modules:
        return
    pyds = glob.glob(os.path.join(site, "greenlet", "*.pyd"))
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    if pyds and not any(tag in os.path.basename(x) for x in pyds):
        _log.info("bundled playwright skipped: greenlet built for another Python (%s)", os.path.basename(pyds[0]))
        return
    try:
        import importlib.metadata as md
        current = md.version("playwright")
    except Exception:  # noqa: BLE001
        current = ""
    if current and _vtuple(current) >= _vtuple(bundled):
        return
    sys.path.insert(0, site)
    try:
        for name in ("typing_extensions", "greenlet", "pyee", "playwright", "playwright.async_api"):
            __import__(name)
        _log.info("using Router's bundled playwright %s (system: %s)", bundled, current or "none")
    except Exception as e:  # noqa: BLE001
        _log.warning("bundled playwright failed (%s) — falling back to system", e)
        for name in [m for m in sys.modules if m.split(".")[0] in ("greenlet", "pyee", "playwright")]:
            sys.modules.pop(name, None)
    finally:
        sys.path.remove(site)
        sys.path.append(site)  # package ផ្សេងដែល system គ្មាន (ឧ. fastapi) នៅតែរកឃើញ — តែ package របស់កម្មវិធីឈ្នះ


def _install_requirements(router_dir):
    """pip install -r <router>/requirements.txt (តែ Router ដែលភ្ជាប់មកជាមួយកម្មវិធី) → True បើជោគជ័យ"""
    import importlib
    import subprocess
    req = os.path.join(router_dir, "requirements.txt")
    if os.path.normcase(os.path.abspath(router_dir)) != os.path.normcase(BUNDLED) or not os.path.isfile(req):
        return False
    _set("starting", "ដំឡើង library របស់ Gemini Router (ម្តងគត់ ~1 នាទី)")
    py = sys.executable.replace("pythonw.exe", "python.exe")
    try:
        proc = subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location", "-r", req],
                              capture_output=True, text=True, errors="replace", timeout=900,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:  # noqa: BLE001
        _log.warning("router requirements install failed: %s", e)
        return False
    if proc.returncode:
        _log.warning("router requirements install failed: %s", (proc.stdout + proc.stderr)[-800:])
        return False
    for name in [m for m in sys.modules if m.split(".")[0] in ("engine", "playwright", "greenlet", "pyee", "fastapi", "pydantic")]:
        sys.modules.pop(name, None)
    importlib.invalidate_caches()
    return True


def _run(router_dir, log_path):
    global _loop, _engine
    try:
        if not is_valid(router_dir):
            raise RuntimeError(f"រកមិនឃើញ engine.py ក្នុង {router_dir or '(ទទេ)'} — ជ្រើសថត Gemini Router")
        if server_running():
            _set("external", "Router server ខាងក្រៅ")
            _ready.set()
            return
        _set("starting", "ផ្ទុក engine")
        if router_dir not in sys.path:
            sys.path.append(router_dir)  # append — module របស់កម្មវិធីនេះនៅតែឈ្នះបើឈ្មោះដូចគ្នា
        pw = os.path.join(router_dir, "ms-playwright")
        if os.path.isdir(pw) and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = pw
        _use_bundled_playwright(router_dir)
        # engine.py ហៅ logging.basicConfig(stdout) — pythonw គ្មាន stdout → ដាក់ NullHandler មុន ឱ្យវាមិនធ្វើអ្វី
        root = logging.getLogger()
        if not root.handlers:
            root.addHandler(logging.NullHandler())
        try:
            import engine  # noqa: PLC0415 — module របស់ Router
        except ImportError as e:
            # Router ភ្ជាប់មកតាម Update: launcher ចាស់មិនទាន់ដំឡើង library → ដំឡើងនៅទីនេះ រួចសាកម្តងទៀត
            if not _install_requirements(router_dir):
                raise RuntimeError(f"ផ្ទុក engine របស់ Router មិនបាន ({e}) — pip install playwright fastapi uvicorn pydantic") from None
            try:
                import engine  # noqa: PLC0415
            except ImportError as e2:
                raise RuntimeError(f"ផ្ទុក engine របស់ Router មិនបាន ({e2}) — បិទកម្មវិធី ហើយបើកវិញ") from None
        _engine = engine
        rlog = logging.getLogger("gemini-router")
        rlog.setLevel(logging.INFO)
        if log_path and not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in rlog.handlers):
            try:
                h = logging.handlers.RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
                h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
                rlog.addHandler(h)
                _log.addHandler(h)
                _log.setLevel(logging.INFO)
            except OSError:
                pass
        _set("starting", "បើក Chrome profile")
        loop = asyncio.new_event_loop()  # Windows → Proactor (Playwright ត្រូវការ subprocess)
        asyncio.set_event_loop(loop)
        engine.account_manager.main_loop = loop
        _loop = loop
        loop.create_task(_init(engine))
        loop.run_forever()
    except Exception as e:  # noqa: BLE001
        _log.error("Gemini Router Direct failed: %s", e, exc_info=True)
        _set("error", error=str(e))
        _ready.set()
    finally:
        if _loop is not None:
            try:
                _loop.close()
            except Exception:  # noqa: BLE001
                pass
        _loop = None
        if _state["status"] not in ("error", "external"):
            _set("off")


async def _init(eng):
    """ដូច account_manager.initialize() តែផ្ទុក profile ស្របគ្នា (≤3) ហើយប្រកាស ready ពេលគណនីដំបូង ready —
    initialize() ដើមផ្ទុកម្តងមួយ: profile មួយគាំង (timeout 180វិ) → រង់ចាំ 6 នាទីមុនបកប្រែបាន"""
    am = eng.account_manager
    try:
        eng.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        am.playwright = await eng.async_playwright().start()
        ids = sorted(p.name for p in eng.PROFILES_DIR.iterdir() if p.is_dir() and p.name != "official_chrome")
        sem = asyncio.Semaphore(3)

        async def load(acc_id):
            async with sem:
                try:
                    await am.load_headless_account(acc_id)
                except Exception as e:  # noqa: BLE001
                    _log.warning("load %s failed: %s", acc_id, e)
        tasks = [asyncio.ensure_future(load(i)) for i in ids]

        def n_ready():
            return sum(1 for a in am.accounts.values() if a.status == "ready")
        pending = list(tasks)
        while pending and not n_ready():
            done, pending = await asyncio.wait(pending, timeout=1.0, return_when=asyncio.FIRST_COMPLETED)
            pending = list(pending)
            _set("starting", f"{len(ids) - len(pending)}/{len(ids)} profile · ready {n_ready()}")
        if not n_ready():
            # engine នៅបើកដដែល — បើបិទ អ្នកប្រើថ្មីមិនអាចបន្ថែមគណនីដំបូង (➕ Manual Account Login) ឬ Relogin បានទេ
            _log.warning("no ready Gemini account yet (%d profiles) - waiting for a sign-in", len(ids))
        _set("ready")
        _log.info("Gemini Router Direct ready: %s", status_text())
        _ready.set()
        if pending:  # profile ដែលនៅសល់ → បន្តផ្ទុកនៅខាងក្រោយ
            await asyncio.gather(*pending, return_exceptions=True)
            _log.info("all profiles loaded: %s", status_text())
        try:
            await am.check_official_chrome_cdp()
        except Exception as e:  # noqa: BLE001
            _log.debug("cdp probe: %s", e)
        if am.session_keepalive_enabled and (am.keepalive_task is None or am.keepalive_task.done()):
            am.keepalive_task = asyncio.ensure_future(am._session_keepalive_loop())
    except Exception as e:  # noqa: BLE001
        _log.error("Gemini Router Direct init failed: %s", e, exc_info=True)
        _set("error", error=str(e))
        _ready.set()
        if _loop is not None:
            _loop.stop()


def stop(timeout=20):
    """បិទ Chrome ទាំងអស់ និង loop (ពេលបិទកម្មវិធី)"""
    global _thread
    loop, eng = _loop, _engine
    if loop is None or eng is None:
        _set("off")
        return
    try:
        fut = asyncio.run_coroutine_threadsafe(eng.account_manager.shutdown(), loop)
        fut.result(timeout)
    except Exception:  # noqa: BLE001
        pass
    try:
        loop.call_soon_threadsafe(loop.stop)
    except RuntimeError:
        pass
    if _thread:
        _thread.join(5)
    _thread = None
    _set("off")


# ---------------------------------------------------------------- model
def models():
    """[(id, ឈ្មោះ)] ដូច GET /v1/models របស់ Router"""
    if _engine is not None:
        try:
            return [(m["id"], m["name"]) for m in _engine.SUPPORTED_MODELS]
        except Exception:  # noqa: BLE001
            pass
    return list(FALLBACK_MODELS)


# ---------------------------------------------------------------- សំណើ
def _build_prompt(payload):
    """messages (OpenAI) → (prompt, audio_path) ដូច routers/api.py: system → [System Context] · user ចុងក្រោយ"""
    msgs = payload.get("messages") or []
    users = [m for m in msgs if m.get("role") == "user"]
    if not users:
        raise RuntimeError("សំណើគ្មានសារ user")
    raw = users[-1].get("content")
    texts, audio_path = [], None
    if isinstance(raw, list):
        for p in raw:
            if not isinstance(p, dict):
                continue
            if p.get("type") == "text":
                texts.append(p.get("text", ""))
            elif p.get("type") == "input_audio" and isinstance(p.get("input_audio"), dict):
                data, fmt = p["input_audio"].get("data"), p["input_audio"].get("format", "mp3")
                if data:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{fmt}") as f:
                        f.write(base64.b64decode(data))
                        audio_path = f.name
        prompt = "\n".join(texts)
    else:
        prompt = str(raw or "")
    systems = [m["content"] for m in msgs if m.get("role") == "system" and isinstance(m.get("content"), str)
               and m["content"].strip()]
    blocks = []
    prefix = getattr(_engine, "MODEL_PROMPT_PREFIXES", {}).get(payload.get("model") or "", "") if _engine else ""
    if prefix:
        blocks.append(prefix)
    if systems:
        blocks.append("[System Context / Instructions:\n" + "\n".join(systems) + "]")
    if blocks:
        prompt = "\n\n".join(blocks) + f"\n\nUser Question:\n{prompt}"
    return prompt, audio_path


async def _chat(prompt, audio_path, model, timeout):
    """ដូច chat_completions ក្នុង routers/api.py: ជ្រើសគណនី → execute_gemini_chat · បរាជ័យ → គណនីបន្ទាប់ (≤3)"""
    eng = _engine
    am = eng.account_manager
    try:
        account = await am.get_next_account(model=model)
    except Exception as e:  # noqa: BLE001 — HTTPException 503 = គ្មានគណនី ready
        detail = getattr(e, "detail", None) or str(e)
        raise RuntimeError(f"Gemini Router: គ្មានគណនី Gemini ដែលចូលបាន ({str(detail)[:120]}) — "
                           "បើកទំព័រ Gemini Router រួចចុច ➕ Manual Account Login ដើម្បីចូល Google") from None
    attempts = min(3, max(1, len(am.accounts)))
    # កំណត់ត្រាសំណើ ដូច Router (Live Request Log + telemetry.db) — Dashboard ក្នុងកម្មវិធីអានពីទីនេះ
    rl = eng.request_logger
    req_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    entry = await rl.log_start(req_id, "/v1/chat/completions", account.account_id, model, prompt)
    t0 = time.time()
    last = None
    for n in range(attempts):
        if n:
            prev = account
            try:
                account = await am.get_next_account(model=model)
            except Exception:  # noqa: BLE001
                account = prev
            _log.info("failover → %s (attempt %d/%d)", account.account_id, n + 1, attempts)
            entry.account_id = account.account_id
        try:
            text = await eng.execute_gemini_chat(account, prompt, audio_path=audio_path, model=model, timeout=timeout)
            if eng.is_gemini_rate_limit_error(text):
                account.trigger_cooldown(600, reason=f"Rate limit: {text.strip()[:120]}")
                raise eng.GeminiInternalError(f"Rate limited by Gemini Web: {text.strip()[:120]}")
            if eng.is_gemini_internal_error(text) and model != RECOVERY_MODEL:
                _log.warning("Gemini Web internal error on %s → retry with %s", model, RECOVERY_MODEL)
                text = await eng.execute_gemini_chat(account, prompt, audio_path=audio_path, model=RECOVERY_MODEL,
                                                     timeout=timeout)
            if eng.is_gemini_internal_error(text):
                raise eng.GeminiInternalError(f"Gemini Web internal error: {text.strip()[:160]}")
            await rl.log_finish(entry, "success", 200, time.time() - t0, response_preview=text[:300], response_full=text)
            return text, account.account_id, req_id
        except (eng.GeminiInternalError, TimeoutError, RuntimeError) as e:
            if getattr(account, "cancelled", False) or isinstance(e, eng.RequestCancelledError):
                raise RuntimeError("Gemini Router: សំណើត្រូវបានបញ្ឈប់") from None
            last = e
            _log.warning("attempt %d/%d failed on %s: %s", n + 1, attempts, account.account_id, e)
            if n + 1 < attempts:
                await asyncio.sleep(1.0)
        finally:
            account.is_busy = False  # get_next_account កំណត់ True — execute_gemini_chat ក៏ដោះ តែបើបរាជ័យមុនចាប់ផ្តើម
    await rl.log_finish(entry, "error", 504 if isinstance(last, TimeoutError) else 502, time.time() - t0,
                        error_message=str(last)[:300])
    raise RuntimeError(f"Gemini Router (ក្នុងកម្មវិធី) បរាជ័យ {attempts} ដង: {str(last)[:300]}")


def _http_post(payload, timeout):
    """Router server ខាងក្រៅ (port 5550) → HTTP ដូចសព្វដង"""
    req = urllib.request.Request(HTTP_URL, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer sk-gemini-direct"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail)["detail"]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"Gemini Router (port {ROUTER_PORT}) error {e.code}: {str(detail)[:300]}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Gemini Router (port {ROUTER_PORT}) ភ្ជាប់មិនបាន ({e.reason})") from None


def chat_completion(payload, timeout=600, router_dir=""):
    """សំណើទម្រង់ OpenAI {model, messages} → ចម្លើយទម្រង់ OpenAI {choices:[{message:{content}}], usage}
    engine មិនទាន់បើក → បើក (router_dir) ហើយរង់ចាំ · Router server ខាងក្រៅ → HTTP"""
    if _state["status"] in ("off", "error"):
        start(router_dir or _state["dir"] or find_router_dir())
    if not wait_ready(180):
        raise RuntimeError(f"Gemini Router (ក្នុងកម្មវិធី) បើកមិនបាន — {_state['error'] or _state['msg']}")
    if _state["status"] == "external" or _loop is None:
        if not server_running():  # server ខាងក្រៅបិទទៅវិញ → សាកបើក engine ក្នុងកម្មវិធីម្តងទៀត
            _set("off")
            return chat_completion(payload, timeout, router_dir)
        return _http_post(payload, timeout)
    model = (payload.get("model") or "").strip()
    ids = {m for m, _ in models()}
    if model not in ids:
        model = "gemini-3.8-flash" if "gemini-3.8-flash" in ids else getattr(_engine.account_manager, "preferred_fast_model", "")
    prompt, audio_path = _build_prompt(payload)
    t = time.time()
    try:
        fut = asyncio.run_coroutine_threadsafe(_chat(prompt, audio_path, model, int(timeout)), _loop)
        text, acc, req_id = fut.result(timeout + 30)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Gemini Router (ក្នុងកម្មវិធី): គ្មានចម្លើយក្នុង {int(timeout)}វិ") from None
    finally:
        if audio_path and os.path.exists(audio_path):
            try:
                os.unlink(audio_path)
            except OSError:
                pass
    _log.info("%s · %s · %.1fs · %d chars", model, acc, time.time() - t, len(text))
    return {"id": req_id, "object": "chat.completion", "model": model, "account_id": acc,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": max(1, len(prompt.split())), "completion_tokens": max(1, len(text.split())),
                      "total_tokens": max(1, len(prompt.split())) + max(1, len(text.split()))}}


# ---------------------------------------------------------------- Dashboard (router_panel.py)
_BASE = f"http://127.0.0.1:{ROUTER_PORT}"


def dashboard_mode():
    """ប្រភពទិន្នន័យ Dashboard: "engine" (ក្នុងកម្មវិធី) · "http" (Router server ខាងក្រៅកំពុងរត់) · None"""
    if active():
        return "engine"
    return "http" if server_running() else None


def _http(method, path, body=None, timeout=20):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(_BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer sk-gemini-direct"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail)["detail"]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"Router {e.code}: {str(detail)[:200]}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Router ភ្ជាប់មិនបាន ({e.reason})") from None


def accounts():
    """[acc.to_dict()] ដូច GET /accounts របស់ Router — [] បើគ្មានប្រភព"""
    if _engine is None or not active():
        if server_running():
            try:
                return list(_http("GET", "/accounts", timeout=8).get("accounts", []))
            except Exception:  # noqa: BLE001
                return []
        return []
    out = []
    for acc in list(_engine.account_manager.accounts.values()):
        try:
            out.append(acc.to_dict())
        except Exception:  # noqa: BLE001
            pass
    return out


def logs(limit=100):
    """កំណត់ត្រាសំណើចុងក្រោយ (ថ្មីមុន) ដូច GET /logs/requests"""
    if _engine is None or not active():
        if server_running():
            try:
                return list(_http("GET", f"/logs/requests?limit={int(limit)}", timeout=8).get("logs", []))
            except Exception:  # noqa: BLE001
                return []
        return []
    try:
        return [e.to_dict() for e in list(_engine.request_logger.entries)[:limit]]
    except Exception:  # noqa: BLE001
        return []


def _call(coro, timeout=60):
    """រត់ coroutine លើ loop របស់ engine (ពី thread ផ្សេង) ហើយរង់ចាំលទ្ធផល"""
    if _loop is None:
        coro.close()
        raise RuntimeError("Gemini Router (ក្នុងកម្មវិធី) មិនទាន់បើក")
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout)


async def _finish_login(eng, acc_id):
    """ដូច POST /accounts/{id}/finish-login: បិទ Chrome ដែលបើកឱ្យចូល → ផ្ទុក profile ជា headless → រកឃើញ plan"""
    am = eng.account_manager
    task = am.login_tasks.get(acc_id)
    if task:
        task.cancel()
    acc = am.accounts.get(acc_id)
    lp = getattr(acc, "login_proc", None) if acc else None
    if lp and lp.poll() is None:
        try:
            lp.terminate()
        except Exception:  # noqa: BLE001
            pass
    eng.kill_profile_browser_processes(acc_id)
    await asyncio.sleep(1.0)
    p = eng.PROFILES_DIR / acc_id
    for lf in ("lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket", "SingletonSocket.sock"):
        try:
            (p / lf).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    acc = await am.load_headless_account(acc_id)
    if acc:
        try:
            await eng.detect_account_gemini_plan(acc)
        except Exception as e:  # noqa: BLE001
            _log.warning("plan detect %s: %s", acc_id, e)
    return acc


def action(name, acc_id=""):
    """សកម្មភាពពី Dashboard (thread របស់ GUI) → អត្ថបទលទ្ធផលខ្លី · កំហុស → RuntimeError"""
    eng = _engine
    if eng is None or _loop is None or not active():
        if server_running():
            return _action_http(name, acc_id)
        raise RuntimeError("Gemini Router មិនទាន់បើក — ធីក Direct ឬបើក Router server សិន")
    am = eng.account_manager
    acc = am.accounts.get(acc_id)
    per_account = ("pause", "reset_cooldown", "close_browser", "relogin", "finish_relogin", "delete")
    if name in per_account and acc is None and name not in ("finish_relogin",):
        raise RuntimeError(f"គ្មានគណនី {acc_id}")
    if name == "pause":
        acc.is_paused = not getattr(acc, "is_paused", False)
        acc.save_meta()
        return f"{acc_id}: " + ("⏸ ផ្អាកហើយ" if acc.is_paused else "▶ បើកវិញហើយ")
    if name in ("pause_all", "unpause_all"):
        for a in am.accounts.values():
            a.is_paused = name == "pause_all"
            a.save_meta()
        return "⏸ ផ្អាកគ្រប់គណនី" if name == "pause_all" else "▶ បើកគ្រប់គណនីវិញ"
    if name == "reset_cooldown":
        acc.reset_cooldown()
        return f"↺ {acc_id}: cooldown លុបហើយ"
    if name == "reset_all_cooldowns":
        n = sum(1 for a in am.accounts.values() if a.is_cooling_down())
        for a in am.accounts.values():
            a.reset_cooldown()
        return f"↺ cooldown លុប {n} គណនី"
    if name == "close_browser":
        _call(acc.close_browser(), 30)
        return f"⚡ {acc_id}: Chrome បិទហើយ (បើកវិញពេលមានសំណើ)"
    if name == "close_all":
        n = _call(am.close_all_browsers(), 60)
        return f"🧹 បិទ Chrome {n} — RAM ទំនេរ"
    if name == "relogin":
        _call(am.open_manual_chrome(acc_id), 120)
        return f"🌐 Chrome បើកសម្រាប់ {acc_id} — ចូល Google រួច ចុច ✓ Done"
    if name == "add_account":
        _call(am.start_login_session(acc_id), 120)
        return f"🌐 Chrome បើកសម្រាប់គណនីថ្មី {acc_id} — ចូល Google រួច ចុច ✓ Done នៅជួរគណនី"
    if name == "finish_relogin":
        if acc is not None and acc.status == "manual_control":
            a = _call(am.close_manual_chrome(acc_id), 120)
        else:
            a = _call(_finish_login(eng, acc_id), 240)
        st = getattr(a, "status", "?")
        return f"✓ {acc_id}: {st}" + ("" if st == "ready" else " — នៅមិនទាន់ចូល Google")
    if name == "delete":
        _call(am.delete_account(acc_id), 60)
        return f"🗑 លុប {acc_id} ហើយ"
    if name == "refresh_usage":
        n = 0
        for a in list(am.accounts.values()):
            if a.status == "ready" and not a.is_busy:
                try:
                    _call(eng.fetch_account_usage(a), 120)
                    n += 1
                except Exception as e:  # noqa: BLE001
                    _log.warning("usage %s: %s", a.account_id, e)
        return f"📊 ទាញ usage {n} គណនី"
    if name == "check_all":
        return _call(_check_all(eng), 600)
    if name == "clear_logs":
        _call(eng.request_logger.clear_logs(), 30)
        return "🗑 លុបកំណត់ត្រាហើយ"
    raise RuntimeError(f"មិនស្គាល់សកម្មភាព {name}")


def _action_http(name, acc_id=""):
    """សកម្មភាពដដែល តាម HTTP ទៅ Router server ខាងក្រៅ (endpoint ដូច Dashboard របស់វា)"""
    q = urllib.parse.quote(acc_id)
    if name == "check_all":
        return _check_all_http()
    if name == "finish_relogin":
        accs = {a["account_id"]: a for a in accounts()}
        path = f"/accounts/{q}/close-manual-chrome" if accs.get(acc_id, {}).get("in_manual_control") else f"/accounts/{q}/finish-login"
        r = _http("POST", path, timeout=240)
    elif name == "add_account":
        r = _http("POST", "/accounts/add", {"account_id": acc_id}, timeout=120)
    elif name == "delete":
        r = _http("DELETE", f"/accounts/{q}", timeout=60)
    elif name == "clear_logs":
        r = _http("DELETE", "/logs/requests")
    else:
        path = {"pause": f"/accounts/{q}/toggle-pause", "pause_all": "/accounts/pause-all", "unpause_all": "/accounts/unpause-all",
                "reset_cooldown": f"/accounts/{q}/reset-cooldown", "reset_all_cooldowns": "/accounts/reset-all-cooldowns",
                "close_browser": f"/accounts/{q}/close-browser", "close_all": "/accounts/close-browsers",
                "relogin": f"/accounts/{q}/open-manual-chrome", "refresh_usage": "/accounts/fetch-all-usage"}.get(name)
        if not path:
            raise RuntimeError(f"មិនស្គាល់សកម្មភាព {name}")
        r = _http("POST", path, timeout=180 if name == "refresh_usage" else 120)
    return str(r.get("message") or r.get("status") or "✓")[:160] if isinstance(r, dict) else "✓"


# ---------------------------------------------------------------- 🩺 Check All Accounts
CHECK_PROMPT = "Reply with exactly one word: OK"
_checks = {}  # account_id → {"state": testing|ok|fail|skip, "msg", "seconds", "at"}


def checks():
    """លទ្ធផលពិនិត្យចុងក្រោយរបស់គណនីនីមួយៗ (Dashboard បង្ហាញក្នុងជួរ CHECK)"""
    return {k: dict(v) for k, v in list(_checks.items())}


def _mark(acc_id, state, msg="", seconds=0.0):
    _checks[acc_id] = {"state": state, "msg": msg, "seconds": seconds, "at": time.time()}


def _check_summary(ids):
    res = [(i, _checks.get(i, {})) for i in ids]
    ok = [i for i, c in res if c.get("state") == "ok"]
    bad = [f"{i} ({c.get('msg', '')[:40]})" for i, c in res if c.get("state") == "fail"]
    skip = [i for i, c in res if c.get("state") == "skip"]
    out = f"🩺 {len(ok)}/{len(res)} គណនីដំណើរការ"
    if bad:
        out += " · ✕ " + ", ".join(bad)
    if skip:
        out += " · ⏭ រំលង " + ", ".join(skip)
    return out if bad or skip or not res else out + " — ទាំងអស់ដំណើរការល្អ ✓"


def _precheck(a):
    """គណនីដែលមិនអាចសាកបាន → (state, មូលហេតុ) · សាកបាន → None  (a = dict ដូច /accounts)"""
    raw = a.get("raw_status") or a.get("status") or ""
    if a.get("is_paused"):
        return "skip", "ផ្អាក (Paused)"
    if raw != "ready":
        return "fail", {"needs_login": "ត្រូវចូល Google ម្តងទៀត", "error": "Error — " + str(a.get("last_error") or "")[:80],
                        "initializing": "កំពុងបើក មិនទាន់រួច", "manual_control": "Chrome បើក manual"}.get(raw, raw or "?")
    if a.get("is_cooling_down"):
        return "fail", f"cooldown {a.get('cooldown_remaining_seconds', 0)}s (rate limit)"
    return None


async def _check_one(eng, acc, model, sem):
    acc_id = acc.account_id
    async with sem:
        for _ in range(120):  # កំពុងឆ្លើយសំណើផ្សេង → រង់ចាំ (≤2 នាទី)
            if not acc.is_busy:
                break
            await asyncio.sleep(1.0)
        else:
            return _mark(acc_id, "ok", "កំពុងឆ្លើយសំណើផ្សេង (ដំណើរការ)")
        acc.is_busy = True
        rl = eng.request_logger
        entry = await rl.log_start(f"check-{uuid.uuid4().hex[:10]}", "/check", acc_id, model, CHECK_PROMPT)
        t0 = time.time()
        try:
            text = await eng.execute_gemini_chat(acc, CHECK_PROMPT, audio_path=None, model=model, timeout=120)
            if eng.is_gemini_rate_limit_error(text):
                raise RuntimeError(f"Rate limit: {text.strip()[:80]}")
            if eng.is_gemini_internal_error(text):
                raise RuntimeError(f"Gemini Web error: {text.strip()[:80]}")
            if not (text or "").strip():
                raise RuntimeError("ចម្លើយទទេ")
            dt = time.time() - t0
            await rl.log_finish(entry, "success", 200, dt, response_preview=text[:300], response_full=text)
            _mark(acc_id, "ok", " ".join(text.split())[:40], dt)
        except Exception as e:  # noqa: BLE001
            dt = time.time() - t0
            msg = str(e) or type(e).__name__
            await rl.log_finish(entry, "error", 504 if isinstance(e, TimeoutError) else 502, dt, error_message=msg[:300])
            _mark(acc_id, "fail", msg[:160], dt)
        finally:
            acc.is_busy = False


async def _check_all(eng):
    """សាកគណនីនីមួយៗដោយសំណើខ្លីផ្ទាល់ទៅគណនីនោះ (ស្របគ្នា ≤3) → សង្ខេប"""
    am = eng.account_manager
    ids = {m for m, _ in models()}
    model = "gemini-3.8-flash" if "gemini-3.8-flash" in ids else getattr(am, "preferred_fast_model", "") or RECOVERY_MODEL
    sem, tasks, order = asyncio.Semaphore(3), [], []
    for acc in list(am.accounts.values()):
        order.append(acc.account_id)
        try:
            pre = _precheck(acc.to_dict())
        except Exception as e:  # noqa: BLE001
            pre = ("fail", str(e)[:80])
        if pre:
            _mark(acc.account_id, *pre)
        else:
            _mark(acc.account_id, "testing")
            tasks.append(_check_one(eng, acc, model, sem))
    await asyncio.gather(*tasks, return_exceptions=True)
    return _check_summary(order)


def _check_all_http():
    """Router server ខាងក្រៅ: វាជ្រើសគណនីឯង → ផ្ញើសំណើខ្លីស្របគ្នា (មួយក្នុងមួយគណនី) រួចអាន Request Log
    ថាសំណើណាទៅគណនីណា · គណនីដែលមិនទាន់ត្រូវជ្រើស → ជុំបន្ទាប់ (≤3 ជុំ)"""
    accs = accounts()
    order = [a["account_id"] for a in accs]
    pending = []
    for a in accs:
        pre = _precheck(a)
        if pre:
            _mark(a["account_id"], *pre)
        else:
            _mark(a["account_id"], "testing")
            pending.append(a["account_id"])
    for _ in range(3):
        if not pending:
            break
        tag = f"healthcheck-{uuid.uuid4().hex[:8]}"
        payload = {"model": RECOVERY_MODEL, "messages": [{"role": "user", "content": f"[{tag}] {CHECK_PROMPT}"}]}
        errs = []

        def send():
            try:
                _http_post(payload, 150)
            except Exception as e:  # noqa: BLE001
                errs.append(str(e)[:160])
        threads = [threading.Thread(target=send, daemon=True) for _ in pending]
        for th in threads:
            th.start()
        for th in threads:
            th.join(170)
        hit = {}
        for e in logs(300):
            if tag in str(e.get("prompt_full") or e.get("prompt") or "") and e.get("status") in ("success", "error"):
                cur = hit.get(e.get("account_id"))
                if cur is None or e.get("status") == "success":
                    hit[e.get("account_id")] = e
        for acc_id, e in hit.items():
            if acc_id in pending:
                if e.get("status") == "success":
                    _mark(acc_id, "ok", "OK", float(e.get("latency_seconds") or 0))
                else:
                    _mark(acc_id, "fail", str(e.get("error_message") or "error")[:160], float(e.get("latency_seconds") or 0))
                pending.remove(acc_id)
        if not hit and errs:  # server មិនឆ្លើយទាល់តែសោះ
            for acc_id in pending:
                _mark(acc_id, "fail", errs[0])
            pending = []
    for acc_id in pending:
        _mark(acc_id, "skip", "Router មិនបានបញ្ជូនសំណើសាកមកគណនីនេះ")
    return _check_summary(order)
