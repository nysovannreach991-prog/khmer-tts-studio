"""Upload វីដេអូដែលរួចរាល់ ទៅ Google Drive របស់ម្ចាស់ (Folder ដែលបានបង្កើតរួច)
គ្មាន key/token ក្នុងកម្មវិធីទេ: Google Apps Script របស់ម្ចាស់ (drive_upload.gs) ពិនិត្យពាក្យសម្ងាត់
ហើយបង្កើត "resumable upload session" ក្នុង Folder → កម្មវិធីបញ្ជូនឯកសារផ្ទាល់ទៅ Google ជាដុំៗ
(បាត់អ៊ីនធឺណិតពាក់កណ្តាលទី → បន្តពីកន្លែងដែលដាច់) · Link + ពាក្យសម្ងាត់ នៅក្នុង QSettings តែប៉ុណ្ណោះ"""
import html
import http.client
import json
import os
import queue
import re
import threading
import time
import urllib.parse
import urllib.request

CHUNK = 32 * 1024 * 1024  # ត្រូវតែជាពហុគុណនៃ 256 KB (លក្ខខណ្ឌរបស់ Google)
RETRIES = 8


def _session(script_url, secret, name, size, sub):
    """សុំ upload session ពី Apps Script របស់ម្ចាស់ → URL សម្រាប់ PUT ឯកសារ"""
    body = json.dumps({"secret": secret, "name": name, "size": size, "mime": "video/mp4", "sub": sub}).encode()
    req = urllib.request.Request(script_url.strip(), data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:  # Apps Script → redirect ទៅលទ្ធផល (urllib តាមដោយខ្លួនឯង)
        text = r.read().decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        # Apps Script បរាជ័យ → ទំព័រ HTML មាន "Exception: …" (ឧ. FOLDER_ID ខុស) — បង្ហាញសារពិត
        page = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", text, flags=re.S)
        m = re.search(r"Exception:.*", html.unescape(re.sub(r"\s+", " ", page)))
        raise RuntimeError(f"Apps Script: {m.group(0).strip()[:300]}" if m else
                           "Link Google Drive មិនត្រឹមត្រូវ (មិនមែន Apps Script Web app)") from None
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data["url"]


def check(script_url, secret):
    """សាកភ្ជាប់ (មិនបង្កើតឯកសារទេ — session ដែលមិនបានបញ្ជូនអ្វី ផុតកំណត់ដោយខ្លួនឯង)"""
    _session(script_url, secret, "_test.mp4", 1, "")


class _Reader:
    """file-like ដែល http.client អានម្តង 8 KB → រាប់ bytes ដែលបានផ្ញើ (progress រលូន)"""

    def __init__(self, f, length, on_bytes, stop):
        self.f, self.left, self.on_bytes, self.stop = f, length, on_bytes, stop

    def read(self, n=-1):
        if self.stop.is_set():
            raise InterruptedError
        n = self.left if n < 0 else min(n, self.left)
        data = self.f.read(n)
        self.left -= len(data)
        self.on_bytes(len(data))
        return data


def _put(url, headers, body=None, length=0):
    u = urllib.parse.urlsplit(url)
    conn = (http.client.HTTPConnection if u.scheme == "http" else http.client.HTTPSConnection)(u.netloc, timeout=120)
    try:
        conn.request("PUT", f"{u.path}?{u.query}", body=body, headers=dict(headers, **{"Content-Length": str(length)}))
        r = conn.getresponse()
        return r.status, r.getheader("Range"), r.read()
    finally:
        conn.close()


def _offset(range_header):
    """"bytes=0-1048575" → 1048576 (bytes ដែល Google ទទួលរួច)"""
    return int(range_header.rsplit("-", 1)[1]) + 1 if range_header else 0


def upload(path, script_url, secret, sub="", progress=None, stop=None):
    """→ id ឯកសារលើ Drive · progress(done_bytes, total_bytes)"""
    stop = stop or threading.Event()
    size = os.path.getsize(path)
    url = _session(script_url, secret, os.path.basename(path), size, sub)
    sent = [0]

    def on_bytes(n):
        sent[0] += n
        if progress:
            progress(min(sent[0], size), size)

    offset, fails = 0, 0
    with open(path, "rb") as f:
        while True:
            if stop.is_set():
                raise InterruptedError
            end = min(offset + CHUNK, size) - 1
            f.seek(offset)
            sent[0] = offset
            try:
                status, rng, body = _put(url, {"Content-Range": f"bytes {offset}-{end}/{size}"},
                                         _Reader(f, end - offset + 1, on_bytes, stop), end - offset + 1)
            except InterruptedError:
                raise
            except OSError as e:  # បណ្តាញដាច់ → សួរ Google ថាទទួលបានដល់ណា ហើយបន្ត
                status, rng, body = None, None, str(e).encode()
            if status in (200, 201):
                if progress:
                    progress(size, size)
                return json.loads(body or b"{}").get("id")
            if status == 308:
                offset, fails = _offset(rng), 0
                continue
            if status in (400, 401, 403, 404, 410):
                raise RuntimeError(f"Google Drive បដិសេធ ({status}): {body[:200].decode('utf-8', 'replace')}")
            fails += 1
            if fails > RETRIES:
                raise RuntimeError(f"Upload មិនបាន (បណ្តាញ): {body[:200].decode('utf-8', 'replace')}")
            time.sleep(min(2 ** fails, 60))
            try:
                status, rng, body = _put(url, {"Content-Range": f"bytes */{size}"})
                if status in (200, 201):
                    return json.loads(body or b"{}").get("id")
                if status == 308:
                    offset = _offset(rng)
            except OSError:
                pass


class Uploader:
    """ជួរ upload នៅខាងក្រោយ (ម្តងមួយឯកសារ) — Dub រឿងបន្ទាប់បន្តធ្វើស្របពេល"""

    def __init__(self):
        self.q = queue.Queue()
        self.stop = threading.Event()
        self.current = None  # {"name", "done", "total", "speed"}
        self.pending = 0
        self.events = []  # ("done"|"error", ឈ្មោះ, សារ) — GUI យកទៅបង្ហាញ
        threading.Thread(target=self._loop, daemon=True).start()

    def add(self, path, script_url, secret, sub=""):
        self.pending += 1
        self.q.put((path, script_url, secret, sub))

    def busy(self):
        return self.pending > 0

    def _loop(self):
        while True:
            path, script_url, secret, sub = self.q.get()
            name = os.path.basename(path)
            t0, mark = time.time(), [time.time(), 0, 0.0]

            def progress(done, total):
                now = time.time()
                if now - mark[0] >= 1:  # ល្បឿនជាមធ្យមរំកិល ~1 វិនាទី
                    mark[2] = (done - mark[1]) / (now - mark[0])
                    mark[0], mark[1] = now, done
                self.current = {"name": name, "done": done, "total": total, "speed": mark[2]}
            try:
                self.current = {"name": name, "done": 0, "total": os.path.getsize(path), "speed": 0.0}
                upload(path, script_url, secret, sub, progress, self.stop)
                self.events.append(("done", name, f"{os.path.getsize(path) / 2 ** 20:,.0f} MB · "
                                                  f"{time.time() - t0:.0f}s"))
            except InterruptedError:
                self.events.append(("error", name, "បានបញ្ឈប់"))
            except Exception as e:  # noqa: BLE001
                self.events.append(("error", name, str(e)[-300:]))
            finally:
                self.current = None
                self.pending -= 1
