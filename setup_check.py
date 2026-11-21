"""🛠 រៀបចំកុំព្យូទ័រពេលបើកលើកដំបូង — ពិនិត្យ និងដំឡើងអ្វីៗដែលខ្វះ ម្តងគត់ ដើម្បីឱ្យ ▶ ដំបូងដំណើរការភ្លាម
(កុំព្យូទ័រថ្មីស្អាត: launcher.ps1 ដំឡើង Python + library មូលដ្ឋានរួចហើយ — នៅសល់ផ្នែកខាងក្រោម)
VC++ Runtime → ffmpeg → ម៉ាស៊ីនស្តាប់ (Whisper/SenseVoice) → GPU (NVIDIA) → model ស្តាប់ → Demucs (បើប្រើ)"""
import glob
import importlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

import ffmpeg_setup
import funasr_srt
import theme
import vocal_remove
import whisper_srt
from simple_ui import SmoothBar

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VCREDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"  # Microsoft ផ្លូវការ
VC_DLLS = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
TURBO = "large-v3-turbo"
NEED_GB = 8  # ទំហំទំនេរដែលណែនាំ (CUDA + model + វីដេអូបណ្តោះអាសន្ន)


def vc_ok():
    sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    return all(os.path.isfile(os.path.join(sys32, d)) for d in VC_DLLS)


def model_ok(name=TURBO):
    return bool(glob.glob(os.path.join(whisper_srt.MODEL_DIR, f"models--*{name}", "snapshots", "*", "model.bin")))


def free_gb():
    try:
        return shutil.disk_usage(whisper_srt.DATA_DIR if os.path.isdir(whisper_srt.DATA_DIR)
                                 else os.path.expanduser("~")).free / 2 ** 30
    except OSError:
        return 999


class Step:
    def __init__(self, key, title, size, run):
        self.key, self.title, self.size, self.run = key, title, size, run
        self.state, self.detail, self.frac = "wait", "", 0.0  # wait / run / ok / fail / skip


# ---------- អ្នកដំឡើងនីមួយៗ: run(report) · report(frac 0..1 ឬ -1, អក្សរ) ----------

def _download(url, dest, report, label):
    req = urllib.request.Request(url, headers={"User-Agent": "AITeam1"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total, done = int(r.headers.get("Content-Length") or 0), 0
        while True:
            block = r.read(1 << 16)
            if not block:
                break
            f.write(block)
            done += len(block)
            report(done / total if total else -1, f"{label} {done / 2 ** 20:,.0f}/{total / 2 ** 20:,.0f} MB")


def run_vcredist(report):
    tmp = tempfile.mkdtemp(prefix="vcredist_")
    try:
        exe = os.path.join(tmp, "vc_redist.x64.exe")
        _download(VCREDIST_URL, exe, report, "ទាញយក")
        report(-1, "កំពុងដំឡើង — ចុច Yes ពេល Windows សួរ")
        # ត្រូវការសិទ្ធិ Admin → Windows សួរ (UAC) · 1638 = មានកំណែថ្មីជាងរួច · 3010 = ត្រូវ restart (ប្រើបានហើយ)
        ps = (f"$p = Start-Process -FilePath '{exe}' -ArgumentList '/install','/passive','/norestart' "
              f"-Verb RunAs -Wait -PassThru; exit $p.ExitCode")
        code = subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=_NO_WINDOW).returncode
        if code not in (0, 1638, 3010) and not vc_ok():
            raise RuntimeError(f"ដំឡើងមិនបាន (code {code}) — ប្រហែលចុច No ពេល Windows សួរ")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_ffmpeg(report):
    ffmpeg_setup.download(lambda d, t: report(d / t if t else -1, f"{d / 2 ** 20:,.0f}/{t / 2 ** 20:,.0f} MB"))


def _pip(packages):
    def run(report):
        stop = threading.Event()

        def watch():  # វឌ្ឍនភាពទាញយករបស់ pip (whisper_srt.pip_progress)
            while not stop.is_set():
                p = whisper_srt.pip_progress
                if p and p["total"]:
                    report(p["done"] / p["total"],
                           f"{p['name']} {p['done'] / 2 ** 20:,.0f}/{p['total'] / 2 ** 20:,.0f} MB")
                time.sleep(0.3)
        threading.Thread(target=watch, daemon=True).start()
        try:
            for pk in packages:
                pk(lambda line: report(-1, line[:70])) if callable(pk) else \
                    whisper_srt.pip_install(pk, lambda line: report(-1, line[:70]))
        finally:
            stop.set()
            importlib.invalidate_caches()  # package ទើបដំឡើង → find_spec ឃើញ
    return run


def run_model(report):
    """ទាញយក model Whisper turbo ជាមុន (ក្នុង process កូន) — ការងារដំបូងមិនចាំបាច់រង់ចាំ"""
    size = dict((m, s) for m, _, s in whisper_srt.MODELS)[TURBO]
    before = whisper_srt._model_dir_mb()
    code = ("import sys; from faster_whisper.utils import download_model; "
            f"download_model({TURBO!r}, cache_dir=sys.argv[1])")
    proc = subprocess.Popen([whisper_srt._python(), "-c", code, whisper_srt.MODEL_DIR], stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
    while proc.poll() is None:
        grown = whisper_srt._model_dir_mb() - before
        report(min(grown / size, 0.99), f"{grown:,.0f}/{size:,} MB")
        time.sleep(0.5)
    if proc.returncode != 0 or not model_ok():
        err = proc.stderr.read().decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(err[-1] if err else f"exit {proc.returncode}")


def plan(asr="whisper", novocal=False):
    """បញ្ជីអ្វីដែលខ្វះ (ហៅនៅខាងក្រោយ — nvidia-smi ចំណាយពេលបន្តិច)"""
    steps = []
    if not vc_ok():
        steps.append(Step("vc", "🧩 Microsoft VC++ Runtime", "~25 MB", run_vcredist))
    if not ffmpeg_setup.available():
        steps.append(Step("ffmpeg", "🎬 ffmpeg (សំឡេង និងវីដេអូ)", "~115 MB", run_ffmpeg))
    nvidia = whisper_srt.has_nvidia()
    if asr in ("whisper", "gemini"):
        if not whisper_srt.installed():
            steps.append(Step("whisper", "🎙 ម៉ាស៊ីនស្តាប់ Whisper", "~100 MB", _pip([whisper_srt.WHISPER_PACKAGES])))
        if nvidia and not whisper_srt.gpu_ready():
            steps.append(Step("gpu", "⚡ GPU NVIDIA (CUDA)", "~1.5 GB", _pip([whisper_srt.GPU_PACKAGES])))
        if not model_ok():
            steps.append(Step("model", "🧠 Model ស្តាប់ (Whisper turbo)", "~1.6 GB", run_model))
    elif asr != "gemini_only" and not funasr_srt.installed():  # Gemini តែមួយ → មិនត្រូវការ model ស្តាប់ទេ
        steps.append(Step("sensevoice", "🎙 ម៉ាស៊ីនស្តាប់ SenseVoice", "~300 MB",
                          _pip([funasr_srt.PACKAGES] + ([funasr_srt.ensure_gpu_torch] if nvidia else []))))
    if novocal and not vocal_remove.installed():
        steps.append(Step("demucs", "🎤 លុបការនិយាយ (Demucs)", "~100 MB+", _pip([vocal_remove.install])))
    return steps


class SetupDialog(QDialog):
    """បញ្ជីពិនិត្យ — ធ្វើម្តងមួយៗនៅខាងក្រោយ · បិទផ្ទាំងបាន (ការងារនៅតែបន្ត ហើយបង្ហាញលើរបារខាងក្រោម)"""

    def __init__(self, parent, steps, on_done):
        super().__init__(parent)
        self.steps, self.on_done = steps, on_done
        self.cur, self.finished = 0, False
        self.setWindowTitle("🛠 រៀបចំកុំព្យូទ័រ (ម្តងគត់)")
        self.setMinimumWidth(620)
        c = theme.C
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        head = QLabel("🛠 កំពុងរៀបចំកុំព្យូទ័រសម្រាប់ AI Team #1")
        head.setStyleSheet("font-size: 14pt; font-weight: bold;")
        lay.addWidget(head)
        sub = QLabel("ធ្វើតែម្តងគត់ · ត្រូវការអ៊ីនធឺណិត · អាចបិទផ្ទាំងនេះបាន (វានៅតែធ្វើនៅខាងក្រោយ)")
        sub.setStyleSheet(f"color: {c['muted']};")
        lay.addWidget(sub)
        gb = free_gb()
        if gb < NEED_GB:
            warn = QLabel(f"⚠ Disk សល់តែ {gb:.1f} GB — ណែនាំយ៉ាងតិច {NEED_GB} GB (CUDA + model + វីដេអូ)")
            warn.setStyleSheet(f"color: {c['error']}; font-weight: bold;")
            lay.addWidget(warn)
        self.rows = []
        for st in steps:
            box = QFrame()
            box.setObjectName("setupRow")
            box.setStyleSheet(f"QFrame#setupRow {{ background: {c['card']}; border: 1px solid {c['border']}; "
                              f"border-radius: 6px; }}")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(12, 8, 12, 8)
            bl.setSpacing(4)
            top = QHBoxLayout()
            icon = QLabel("○")
            icon.setFixedWidth(22)
            name = QLabel(f"{st.title}  <span style='color:{c['faint']}'>{st.size}</span>")
            name.setTextFormat(Qt.RichText)
            detail = QLabel("")
            detail.setStyleSheet(f"color: {c['muted']};")
            top.addWidget(icon)
            top.addWidget(name, 1)
            top.addWidget(detail)
            bl.addLayout(top)
            bar = SmoothBar(6)
            bar.setObjectName("cardBar")
            bar.setFixedHeight(8)
            bl.addWidget(bar)
            lay.addWidget(box)
            self.rows.append((icon, detail, bar))
        self.total = SmoothBar(12)
        self.total.setObjectName("bigBar")
        lay.addWidget(self.total)
        btns = QHBoxLayout()
        self.note = QLabel("")
        self.note.setWordWrap(True)
        btns.addWidget(self.note, 1)
        self.btn = QPushButton("ធ្វើនៅខាងក្រោយ")
        self.btn.clicked.connect(self.hide)
        btns.addWidget(self.btn)
        lay.addLayout(btns)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(250)
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        for i, st in enumerate(self.steps):
            self.cur = i
            st.state = "run"

            def report(frac, text, st=st):
                st.frac, st.detail = frac, text
            try:
                st.run(report)
                st.state, st.frac, st.detail = "ok", 1.0, "✓ រួចរាល់"
            except Exception as e:  # noqa: BLE001 — ធ្វើជំហានបន្ទាប់ទៀត (▶ នឹងសាកម្តងទៀតពេលក្រោយ)
                msg = str(e).strip()
                if any(k in msg for k in ("getaddrinfo", "urlopen error", "10060", "10054", "timed out",
                                          "Max retries", "ConnectionError", "NameResolution")):
                    msg = "គ្មានអ៊ីនធឺណិត / ភ្ជាប់មិនបាន"
                elif "No space" in msg or "Errno 28" in msg:
                    msg = "Disk ពេញ"
                st.state, st.detail = "fail", msg.splitlines()[-1][:90] if msg else "បរាជ័យ"
        self.finished = True

    def progress(self):
        """(frac, អក្សរ) សម្រាប់របារខាងក្រោម"""
        if self.finished:
            return None
        st = self.steps[self.cur]
        part = max(st.frac, 0.0)
        return (self.cur + part) / len(self.steps), f"🛠 {self.cur + 1}/{len(self.steps)} {st.title}  {st.detail[:40]}"

    def _refresh(self):
        for st, (icon, detail, bar) in zip(self.steps, self.rows):
            icon.setText({"wait": "○", "run": "⏳", "ok": "✅", "fail": "❌"}[st.state])
            detail.setText(st.detail[:60] if st.state != "wait" else "")
            bar.set_pct(100 * st.frac if st.frac >= 0 else (50 if st.state == "run" else 0))
        p = self.progress()
        self.total.set_pct(100 if p is None else 100 * p[0])
        if self.finished:
            self.timer.stop()
            bad = [s for s in self.steps if s.state == "fail"]
            self.note.setText("✅ រួចរាល់ទាំងអស់ — អាចប្រើកម្មវិធីបានហើយ!" if not bad else
                              f"⚠ {len(bad)} ធាតុមិនបាន — ពិនិត្យអ៊ីនធឺណិត · កម្មវិធីនឹងសាកម្តងទៀតពេលបើកលើកក្រោយ")
            self.btn.setText("បិទ")
            self.on_done(not bad)
