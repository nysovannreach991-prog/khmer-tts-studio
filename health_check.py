"""🩺 ពិនិត្យថាប្រើបាន — ចុចម្តង សាកគ្រប់ផ្នែក (អ៊ីនធឺណិត, ffmpeg, GPU, Whisper, Edge TTS, key បកប្រែនីមួយៗ,
Google Drive, ថត Output) ស្របគ្នា ហើយបង្ហាញ ✓ / ⚠ / ✕ ជាមួយមូលហេតុជាភាសាខ្មែរ"""
import os
import random
import subprocess
import tempfile
import threading
import time
import urllib.request

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

import app as backend
import ffmpeg_setup
import gdrive
import overlay
import setup_check
import theme
import whisper_srt

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Warn(Exception):
    """ប្រើបាន តែមានចំណុចគួរដឹង (⚠)"""


def _human(msg):
    """កំហុសបច្ចេកទេស → មូលហេតុដែលងាយយល់"""
    low = msg.lower()
    if "Tailscale" in msg:  # សារពី app.py សម្រាប់ Server ផ្ទាល់ខ្លួន — ច្បាស់ជាងរួចហើយ
        return "Server ម្ចាស់ភ្ជាប់មិនបាន — កុំព្យូទ័រម្ចាស់បើក share_start.bat? Tailscale ភ្ជាប់?"
    if "no audio" in low:
        return "Microsoft មិនផ្ញើសំឡេងមកវិញ (ជួនកាលមួយភ្លែត) — ចុច ↻ សាកម្តងទៀត"
    if any(k in low for k in ("getaddrinfo", "10060", "10061", "timed out", "urlopen error", "unreachable")):
        return "ភ្ជាប់មិនបាន (គ្មានអ៊ីនធឺណិត ឬ server បិទ)"
    if "401" in msg or "invalid" in low and "key" in low:
        return "Key ខុស ឬត្រូវបិទ — " + msg[-90:]
    if "429" in msg or "quota" in low or "rate" in low and "limit" in low:
        return "អស់ quota / ប្រើច្រើនពេក — " + msg[-90:]
    if "402" in msg or "balance" in low or "insufficient" in low:
        return "អស់លុយ / អស់ credit — " + msg[-90:]
    return msg.strip().splitlines()[-1][:140] if msg.strip() else "បរាជ័យ"


# ---------------- ការពិនិត្យនីមួយៗ: ត្រឡប់អក្សរ (✓) · raise Warn (⚠) · raise ផ្សេង (✕) ----------------

def c_internet():
    t = time.time()
    urllib.request.urlopen("https://www.google.com/generate_204", timeout=10).read()
    return f"ភ្ជាប់បាន ({(time.time() - t) * 1000:.0f} ms)"


def c_ffmpeg():
    if not ffmpeg_setup.available():
        raise RuntimeError("មិនទាន់មាន ffmpeg — បិទ ហើយបើកកម្មវិធីវិញ ដើម្បីដំឡើងដោយស្វ័យប្រវត្តិ")
    out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, creationflags=_NO_WINDOW).stdout
    return out.split("Copyright")[0].replace("ffmpeg version", "").strip()[:40]


def c_encoder():
    enc = overlay.pick_encoder()
    name = {"h264_nvenc": "NVIDIA NVENC", "h264_qsv": "Intel QSV", "h264_amf": "AMD AMF"}.get(enc)
    if name:
        return f"⚡ {name} (GPU)"
    if overlay.NVENC_DRIVER:
        raise Warn(f"💻 CPU (យឺត) — Update Driver NVIDIA ≥ {overlay.NVENC_DRIVER.split('.')[0]}")
    raise Warn("💻 CPU x264 (យឺតជាង GPU)")


def c_whisper():
    if not whisper_srt.installed():
        raise Warn("មិនទាន់ដំឡើង — នឹងដំឡើងពេលបើកកម្មវិធី / ចុច ▶")
    gpu = "⚡ GPU" if whisper_srt.has_nvidia() and whisper_srt.gpu_ready() else "💻 CPU"
    if not setup_check.model_ok():
        raise Warn(f"{gpu} · Model turbo មិនទាន់ទាញយក (~1.6 GB នៅពេលប្រើលើកដំបូង)")
    if gpu.endswith("CPU") and whisper_srt.has_nvidia():
        raise Warn("💻 CPU — មានកាត NVIDIA តែមិនទាន់បើក GPU (ចុច ⚡ បើក GPU លើរបារខាងក្រោម)")
    return f"{gpu} · Model turbo ✓"


def c_edge(voice):
    def run():
        for attempt in range(2):  # Microsoft ម្តងម្កាល "No audio" មួយភ្លែត → សាកម្តងទៀតមុននឹងថាខូច
            t = time.time()
            try:
                mp3 = backend.edge_audio(f"សួស្តី {random.randint(1000, 9999)}", voice)  # លេខចៃដន្យ → មិនប្រើ cache
                return f"{voice.split('-')[-1].replace('Neural', '')} · {len(mp3) // 1024} KB · {time.time() - t:.1f}s"
            except RuntimeError:
                if attempt:
                    raise
                time.sleep(2)
    return run


def c_key(key):
    def run():
        t = time.time()
        data = backend.gemini_post(key, "gemini-3.8-flash", {
            "contents": [{"parts": [{"text": "Translate to Khmer. Reply with only the translation: 谢谢你"}]}]})
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return f"\"谢谢你\" → \"{text[:30]}\" · {time.time() - t:.1f}s"
    return run


def c_drive(url, secret):
    def run():
        gdrive.check(url, secret)
        return "ភ្ជាប់បាន — Upload ទៅ Folder របស់ម្ចាស់"
    return run


def c_output(folder):
    def run():
        os.makedirs(folder, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, delete=True) as f:
            f.write(b"ok")
        gb = setup_check.free_gb()
        if gb < 5:
            raise Warn(f"សរសេរបាន តែ Disk សល់តែ {gb:.1f} GB")
        return f"សរសេរបាន · Disk ទំនេរ {gb:.0f} GB"
    return run


def key_kind(key):
    if key.upper().startswith("AT1-"):
        return "ម៉ាស៊ីនបកប្រែ (License)"
    if key.startswith("bayon_sk_"):
        return "Bayon"
    if key.startswith("AIza"):
        return "Google Gemini"
    return "Server ផ្ទាល់ខ្លួន" if backend._custom_url() else "Bayon"


def build(keys, voice, gd=None, out_dir=""):
    checks = [("🌐 អ៊ីនធឺណិត", c_internet), ("🎬 ffmpeg", c_ffmpeg), ("⚡ Encode វីដេអូ", c_encoder),
              ("🎙 ស្តាប់សំឡេង (Whisper)", c_whisper), ("🔊 Edge TTS (បញ្ចូលសំឡេង)", c_edge(voice))]
    if not keys:
        checks.append(("🔑 Key បកប្រែ", lambda: (_ for _ in ()).throw(
            Warn("មិនទាន់មាន key — ត្រូវការតែរឿងដែលគ្មាន SRT ខ្មែរ"))))
    for k in keys:
        checks.append((f"🔑 {key_kind(k)} · {backend.key_label(k)}", c_key(k)))
    if gd:
        checks.append(("☁ Google Drive", c_drive(*gd)))
    if out_dir:
        checks.append(("📁 ថត Output", c_output(out_dir)))
    return checks


class CheckDialog(QDialog):
    def __init__(self, parent, checks):
        super().__init__(parent)
        self.setWindowTitle("🩺 ពិនិត្យថាប្រើបាន")
        self.setMinimumWidth(680)
        self.checks = checks
        self.res = [("run", "កំពុងសាក...")] * len(checks)
        c = theme.C
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(8)
        head = QLabel("🩺 ពិនិត្យថាប្រើបាន")
        head.setStyleSheet("font-size: 14pt; font-weight: bold;")
        lay.addWidget(head)
        self.rows = []
        for name, _ in checks:
            box = QFrame()
            box.setObjectName("checkRow")
            box.setStyleSheet(f"QFrame#checkRow {{ background: {c['card']}; border: 1px solid {c['border']}; "
                              f"border-radius: 6px; }}")
            bl = QHBoxLayout(box)
            bl.setContentsMargins(12, 7, 12, 7)
            icon = QLabel("⏳")
            icon.setFixedWidth(24)
            lab = QLabel(name)
            lab.setMinimumWidth(230)
            detail = QLabel("កំពុងសាក...")
            detail.setWordWrap(True)
            detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
            bl.addWidget(icon)
            bl.addWidget(lab)
            bl.addWidget(detail, 1)
            lay.addWidget(box)
            self.rows.append((icon, detail))
        foot = QHBoxLayout()
        self.summary = QLabel("")
        foot.addWidget(self.summary, 1)
        self.btn_again = QPushButton("↻ សាកម្តងទៀត")
        self.btn_again.clicked.connect(self.start)
        close = QPushButton("បិទ")
        close.clicked.connect(self.close)
        foot.addWidget(self.btn_again)
        foot.addWidget(close)
        lay.addLayout(foot)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.start()

    def start(self):
        self.res = [("run", "កំពុងសាក...")] * len(self.checks)
        self.btn_again.setEnabled(False)
        for i, (_, fn) in enumerate(self.checks):  # ស្របគ្នា — key/server យឺតមិនរារាំងផ្សេងទៀត
            threading.Thread(target=self._one, args=(i, fn), daemon=True).start()
        self.timer.start(200)

    def _one(self, i, fn):
        try:
            self.res[i] = ("ok", fn())
        except Warn as w:
            self.res[i] = ("warn", str(w))
        except Exception as e:  # noqa: BLE001
            self.res[i] = ("fail", _human(str(e)))

    def _refresh(self):
        c = theme.C
        colors = {"ok": "#1f9d55", "warn": c["accent"], "fail": c["error"], "run": c["muted"]}
        for (state, text), (icon, detail) in zip(self.res, self.rows):
            icon.setText({"ok": "✅", "warn": "⚠️", "fail": "❌", "run": "⏳"}[state])
            detail.setText(text)
            detail.setStyleSheet(f"color: {colors[state]};")
        states = [s for s, _ in self.res]
        if "run" not in states:
            self.timer.stop()
            self.btn_again.setEnabled(True)
            bad, warn = states.count("fail"), states.count("warn")
            self.summary.setText("✅ ប្រើបានទាំងអស់!" if not bad and not warn else
                                 f"✅ {states.count('ok')} · ⚠ {warn} · ❌ {bad}")
