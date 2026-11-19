"""🎬 Dub Studio — ទំព័រកែសម្រួល dub (វីដេអូឆ្វេង · Dialogue ស្តាំ · Timeline ក្រោម)
លំហូរ: Upload → Transcribe (SenseVoice / Paraformer / Whisper / Gemini) → Rewrite All (បកប្រែខ្មែរ) → Detect Speakers
(ស្រី/ប្រុស) → Generate Voice (Edge TTS) → Export (ដាក់សំឡេងចូលវីដេអូ) · Auto Dub = ទាំងអស់ក្នុងមួយចុច
ប្រើ backend ដដែលនឹងទំព័រ Auto (funasr_srt · whisper_srt · transcribe · voice_gender · srt_dub · video_dub)។
"""
import os
import shutil
import subprocess
import tempfile
import threading
import time

from PyQt5.QtCore import QPointF, QRectF, QSettings, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtMultimediaWidgets import QVideoWidget
from PyQt5.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QSpinBox, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSizePolicy, QSlider, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

import auto_pipeline
import funasr_srt
import srt_dub
import transcribe
import video_dub
import video_merge
import vocal_remove
import voice_gender
import voxcpm_tts
import whisper_srt

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".flv", ".ts", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
# ពណ៌ទំព័រ (dark navy · cyan · orange · purple)
P = {"bg": "#0B0D1A", "panel": "#12152A", "card": "#1A1E3A", "input": "#0A0C18", "border": "#3D3320", "text": "#F7EBCB",
     "muted": "#C2AD7C", "faint": "#8F8160", "cyan": "#F5C451", "orange": "#FF8A1F", "purple": "#C8102E",
     "amber": "#F5C451", "green": "#4ADE80", "red": "#FF5A5F", "blue": "#5AA9FF", "pink": "#FF7AB8", "kid": "#FFB020"}
ENGINES = [("sensevoice", "⚡ SenseVoice · FREE", "លឿនបំផុត — ចិន អង់គ្លេស កាតាំង ជប៉ុន កូរ៉េ (លើកុំព្យូទ័រ ឥតគិតថ្លៃ)"),
           ("paraformer", "🀄 Paraformer-zh", "សម្រាប់រឿងចិន — ពេលវេលាតាមតួអក្សរ + វណ្ណយុត្តិ (លើកុំព្យូទ័រ ឥតគិតថ្លៃ)"),
           ("whisper", "🅆 Whisper Local · FREE", "Whisper large-v3-turbo លើកុំព្យូទ័រ — ភាសាច្រើន ច្បាស់ (GPU លឿន)"),
           ("gemini", "✦ Gemini · ស្តាប់+បកប្រែ", "Gemini ស្តាប់សំឡេង → អត្ថបទខ្មែរ + ភេទ ក្នុងជំហានតែមួយ (ត្រូវការ key Relay/Bayon/Google)")]
LANGS = [("auto", "🌐 Auto"), ("zh", "🇨🇳 中文"), ("en", "🇺🇸 EN"), ("ja", "🇯🇵 日本語"), ("ko", "🇰🇷 한국어"), ("yue", "粵語")]
FEMALE_VOICES = [("km-KH-SreymomNeural", "Sreymom (ខ្មែរ)"), ("th-TH-PremwadeeNeural", "Premwadee (ថៃ)"),
                 ("en-US-AvaMultilingualNeural", "Ava (Multilingual)")]
MALE_VOICES = [("km-KH-PisethNeural", "Piseth (ខ្មែរ)"), ("th-TH-NiwatNeural", "Niwat (ថៃ)"),
               ("en-US-AndrewMultilingualNeural", "Andrew (Multilingual)")]
KID_HZ = 265  # median F0 ខ្ពស់ជាងនេះ → ក្មេង (ប៉ាន់ស្មាន — ស្ត្រីសំឡេងស្រួចអាចច្រឡំ)
CYCLE = [(None, 1), ("male", 1), ("male", 2), ("female", 1), ("female", 2), ("kid", 1)]
# Edge TTS មានសំឡេងខ្មែរតែ 2 → តួអង្គទី 2 / ក្មេង = សំឡេងដដែល ប្តូរកម្ពស់ (Hz)
EDGE_PITCH = {("male", 2): -14, ("female", 2): -12, ("kid", 1): 45}


# ការកំណត់កម្រិតខ្ពស់របស់ Dub Studio (⚙) — រក្សាក្នុង QSettings "studio/<key>"
CFG_DEFAULT = {"part_min": 0, "duck": 0, "blur_sigma": 20, "edge_parallel": 128, "max_speed": 1.5,
               "vox_steps": 10, "vox_cfg": 2.0, "kid_hz": KID_HZ, "orig_vol": 15, "dub_vol": 100}


def load_cfg(store):
    cfg = dict(CFG_DEFAULT)
    for k, d in CFG_DEFAULT.items():
        v = store.value(f"studio/{k}")
        if v is None:
            continue
        try:
            cfg[k] = (str(v).lower() in ("true", "1")) if isinstance(d, bool) else type(d)(float(v))
        except (TypeError, ValueError):
            pass
    return cfg


class BlurCanvas(QWidget):
    """ស៊ុមវីដេអូ + ប្រអប់ព្រិល — អូស = គូរប្រអប់ថ្មី · ចុចស្តាំលើប្រអប់ = លុប · rects ជាប្រភាគ (x, y, w, h)"""

    def __init__(self, pix, rects):
        super().__init__()
        self.pix, self.rects, self._drag = pix, list(rects), None
        self.setMinimumSize(640, 360)
        self.setCursor(Qt.CrossCursor)
        self.setAccessibleName("Blur canvas")

    def _area(self):
        """កន្លែងដែលរូបលេច (រក្សាសមាមាត្រ កណ្តាល widget)"""
        pw, ph = max(self.pix.width(), 1), max(self.pix.height(), 1)
        k = min(self.width() / pw, self.height() / ph)
        w, h = pw * k, ph * k
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _frac(self, pos):
        a = self._area()
        return (min(max((pos.x() - a.x()) / a.width(), 0), 1), min(max((pos.y() - a.y()) / a.height(), 0), 1))

    def mousePressEvent(self, e):
        fx, fy = self._frac(e.pos())
        if e.button() == Qt.RightButton:
            for k in range(len(self.rects) - 1, -1, -1):
                x, y, w, h = self.rects[k]
                if x <= fx <= x + w and y <= fy <= y + h:
                    del self.rects[k]
                    break
            return self.update()
        self._drag = (fx, fy, fx, fy)

    def mouseMoveEvent(self, e):
        if self._drag:
            self._drag = self._drag[:2] + self._frac(e.pos())
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag:
            x0, y0, x1, y1 = self._drag
            self._drag = None
            if abs(x1 - x0) > 0.02 and abs(y1 - y0) > 0.015:
                self.rects.append((min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))
        a = self._area()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(a, self.pix, QRectF(self.pix.rect()))
        boxes = list(self.rects)
        if self._drag:
            x0, y0, x1, y1 = self._drag
            boxes.append((min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
        for x, y, w, h in boxes:
            src = QRectF(x * self.pix.width(), y * self.pix.height(), w * self.pix.width(), h * self.pix.height()).toRect()
            dst = QRectF(a.x() + x * a.width(), a.y() + y * a.height(), w * a.width(), h * a.height())
            if src.width() > 8 and src.height() > 8:  # មើលជាមុនប្រហាក់ប្រហែល: បង្រួម 12 ដង រួចពង្រីកវិញ
                small = self.pix.copy(src).scaled(max(src.width() // 12, 1), max(src.height() // 12, 1),
                                                  Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                p.drawPixmap(dst, small, QRectF(small.rect()))
            p.setPen(QPen(QColor(P["cyan"]), 2, Qt.DashLine))
            p.drawRect(dst)


class BlurDialog(QDialog):
    def __init__(self, parent, pix, rects, sigma):
        super().__init__(parent)
        self.setWindowTitle("🌫 Blur text — ព្រិលអក្សរលើវីដេអូ")
        lay = QVBoxLayout(self)
        tip = QLabel("អូសលើរូប ដើម្បីគូរប្រអប់ព្រិលលើអក្សរដើម (Subtitle / Logo) · ចុចស្តាំលើប្រអប់ = លុប · គូរបានច្រើនប្រអប់")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.canvas = BlurCanvas(pix, rects)
        lay.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        for text, rect in (("⬇ បាតអេក្រង់ (Subtitle)", (0.05, 0.78, 0.90, 0.14)), ("⬆ លើអេក្រង់", (0.05, 0.04, 0.90, 0.12))):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, r=rect: (self.canvas.rects.append(r), self.canvas.update()))
            row.addWidget(b)
        clear = QPushButton("🗑 លុបទាំងអស់")
        clear.clicked.connect(lambda: (self.canvas.rects.clear(), self.canvas.update()))
        row.addWidget(clear)
        row.addStretch()
        row.addWidget(QLabel("កម្លាំងព្រិល"))
        self.sigma = QSlider(Qt.Horizontal)
        self.sigma.setRange(5, 50)
        self.sigma.setValue(int(sigma))
        self.sigma.setFixedWidth(140)
        row.addWidget(self.sigma)
        lay.addLayout(row)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.resize(900, 620)


class SettingsDialog(QDialog):
    """⚙ ការកំណត់កម្រិតខ្ពស់របស់ Dub Studio"""

    def __init__(self, parent, cfg, open_global):
        super().__init__(parent)
        self.setWindowTitle("⚙ ការកំណត់ Dub Studio (Advanced)")
        self.w = {}
        lay = QVBoxLayout(self)

        def group(title):
            g = QGroupBox(title)
            f = QFormLayout(g)
            lay.addWidget(g)
            return f

        def spin(key, lo, hi, step=1, suffix="", tip=""):
            s = QDoubleSpinBox() if isinstance(CFG_DEFAULT[key], float) else QSpinBox()
            s.setRange(lo, hi)
            s.setSingleStep(step)
            s.setSuffix(suffix)
            s.setToolTip(tip)
            self.w[key] = s
            return s

        f = group("⬆ Export")
        f.addRow("កាត់វីដេអូជាផ្នែក", spin("part_min", 0, 120, 1, " នាទី/ផ្នែក", "0 = មិនកាត់ (វីដេអូតែមួយ)"))
        duck = QComboBox()
        for k, lab in enumerate(("បិទ", "ស្រាល", "មធ្យម", "ខ្លាំង")):
            duck.addItem(lab, k)
        duck.setToolTip("បន្ថយសំឡេងដើម/ភ្លេងដោយស្វ័យប្រវត្តិ ពេលសំឡេង dub កំពុងនិយាយ")
        self.w["duck"] = duck
        f.addRow("បន្ថយភ្លេងពេល dub និយាយ (Ducking)", duck)
        f.addRow("កម្លាំង Blur text", spin("blur_sigma", 5, 50, 1, "", "ខ្ពស់ = ព្រិលខ្លាំង"))
        f.addRow("កម្រិតសំឡេងដើម (លំនាំដើម)", spin("orig_vol", 0, 150, 5, " %"))
        f.addRow("កម្រិតសំឡេង dub (លំនាំដើម)", spin("dub_vol", 0, 200, 5, " %"))
        f = group("🔊 សំឡេង (TTS)")
        f.addRow("Edge TTS ស្របគ្នាអតិបរមា", spin("edge_parallel", 8, 128, 8, "", "ខ្ពស់ = លឿន តែ Microsoft អាចបង្ខាំង · កម្មវិធីបន្ថយដោយខ្លួនឯងពេលត្រូវបង្ខាំង"))
        f.addRow("ល្បឿនអតិបរមារបស់ Auto-Fit", spin("max_speed", 1.0, 2.5, 0.1, " x", "បន្ទាត់វែងជាងពេលវេលា ត្រូវពន្លឿនមិនលើសនេះ"))
        f.addRow("VoxCPM2 គុណភាព (steps)", spin("vox_steps", 4, 30, 1, "", "ខ្ពស់ = ល្អជាង តែយឺតជាង (10 = ស្តង់ដារ)"))
        f.addRow("VoxCPM2 ភាពស្មោះនឹងសំឡេងគំរូ (cfg)", spin("vox_cfg", 1.0, 4.0, 0.1))
        f = group("👥 តួអង្គ")
        f.addRow("កម្ពស់សំឡេងចាត់ជា \"ក្មេង\"", spin("kid_hz", 180, 400, 5, " Hz", "Man / woman / kid: សំឡេងខ្ពស់ជាងនេះ = ក្មេង"))
        row = QHBoxLayout()
        g = QPushButton("🔑 Key · Server · សំឡេងទូទៅ…")
        g.clicked.connect(lambda: (self.reject(), open_global()))
        row.addWidget(g)
        r = QPushButton("↺ តម្លៃដើម")
        r.clicked.connect(lambda: self.fill(CFG_DEFAULT))
        row.addWidget(r)
        row.addStretch()
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        row.addWidget(box)
        lay.addLayout(row)
        self.fill(cfg)

    def fill(self, cfg):
        for k, wdg in self.w.items():
            if isinstance(wdg, QCheckBox):
                wdg.setChecked(bool(cfg[k]))
            elif isinstance(wdg, QComboBox):
                wdg.setCurrentIndex(int(cfg[k]))
            else:
                wdg.setValue(cfg[k])

    def values(self):
        return {k: (wdg.isChecked() if isinstance(wdg, QCheckBox) else wdg.currentData() if isinstance(wdg, QComboBox)
                    else wdg.value()) for k, wdg in self.w.items()}


def voice_label(r):
    g, v = r.get("gender"), r.get("var") or 1
    return {"male": f"♂ Male {v}", "female": f"♀ Female {v}", "kid": "🧒 Kid"}.get(g, "◌ Auto")


def voice_color(r):
    return {"male": "#93C5FD", "female": "#F9A8D4", "kid": "#FCD34D"}.get(r.get("gender"), P["muted"])


def voice_id(r):
    g, v = r.get("gender"), r.get("var") or 1
    return "kid" if g == "kid" else f"{g}{v}" if g in ("male", "female") else "female1"


def _fmt(ms):
    s = int(ms // 1000)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _fmt_ms(ms):
    return f"{_fmt(ms)}.{int(ms % 1000) // 100}"


# ---------------------------------------------------------------- Timeline
class Timeline(QWidget):
    """បន្ទាត់ពេលវេលា: ruler · រូបតូចៗរបស់វីដេអូ · ប្លុកបន្ទាត់ dub (ពណ៌តាមភេទ/ស្ថានភាព) · playhead — ចុច/អូស = រំកិល"""
    seek = pyqtSignal(int)
    LEFT = 14

    def __init__(self):
        super().__init__()
        self.duration, self.position, self.rows, self.thumbs, self.zoom = 0, 0, [], [], 1.0
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("Timeline")

    def _span(self):
        """(ចាប់ផ្តើម ms, ប្រវែង ms ដែលមើលឃើញ) — zoom > 1 → តាម playhead"""
        if self.zoom <= 1 or not self.duration:
            return 0, max(self.duration, 1)
        vis = self.duration / self.zoom
        start = min(max(self.position - vis / 2, 0), self.duration - vis)
        return start, vis

    def _x(self, ms):
        start, vis = self._span()
        return self.LEFT + (ms - start) * (self.width() - 2 * self.LEFT) / vis

    def _ms(self, x):
        start, vis = self._span()
        return int(min(max(start + (x - self.LEFT) * vis / max(self.width() - 2 * self.LEFT, 1), 0), self.duration))

    def mousePressEvent(self, e):
        if self.duration:
            self.seek.emit(self._ms(e.x()))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton and self.duration:
            self.seek.emit(self._ms(e.x()))

    def paintEvent(self, e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(P["panel"]))
        p.setPen(QPen(QColor(P["cyan"]), 2))
        p.drawLine(0, 1, w, 1)
        f = QFont(self.font())
        f.setPointSizeF(max(f.pointSizeF() - 1.5, 7))
        p.setFont(f)
        if not self.duration:
            p.setPen(QColor(P["faint"]))
            p.drawText(self.rect(), Qt.AlignCenter, "Timeline — Upload វីដេអូ ដើម្បីចាប់ផ្តើម")
            return
        start, vis = self._span()
        # ruler
        step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800) if s * 1000 * (w - 28) / vis >= 70), 3600)
        t = int(start // (step * 1000)) * step * 1000
        while t <= start + vis:
            x = self._x(t)
            if x >= self.LEFT - 1:
                p.setPen(QColor(P["border"]))
                p.drawLine(QPointF(x, 22), QPointF(x, 30))
                p.setPen(QColor(P["muted"]))
                p.drawText(QRectF(x + 4, 6, 80, 18), Qt.AlignLeft | Qt.AlignVCenter, _fmt(t))
            t += step * 1000
        # រូបតូចៗ
        ty, th = 36, 56
        p.fillRect(QRectF(self.LEFT, ty, w - 2 * self.LEFT, th), QColor(P["input"]))
        for ms, pm in self.thumbs:
            x = self._x(ms)
            if -pm.width() < x < w:
                p.drawPixmap(int(x), ty, pm)
        p.setPen(QColor("#FFFFFF"))
        p.fillRect(QRectF(self.LEFT + 4, ty + 4, 54, 16), QColor(0, 0, 0, 170))
        p.drawText(QRectF(self.LEFT + 4, ty + 4, 54, 16), Qt.AlignCenter, _fmt(self.duration))
        # ប្លុកបន្ទាត់ dub
        cy, ch = ty + th + 8, 26
        p.fillRect(QRectF(self.LEFT, cy, w - 2 * self.LEFT, ch), QColor(P["input"]))
        p.setPen(Qt.NoPen)
        for r in self.rows:
            x0, x1 = self._x(r["start"]), self._x(r["end"])
            if x1 < 0 or x0 > w:
                continue
            col = QColor({"male": P["blue"], "female": P["pink"], "kid": P["kid"]}.get(r.get("gender"), P["cyan"]))
            col.setAlpha(230 if r.get("state") == "ready" else 120)
            p.setBrush(col)
            p.drawRect(QRectF(x0, cy + 3, max(x1 - x0 - 1, 1.5), ch - 6))
        # playhead
        x = self._x(self.position)
        p.setPen(QPen(QColor(P["orange"]), 2))
        p.drawLine(QPointF(x, 4), QPointF(x, h - 4))
        p.setBrush(QColor(P["orange"]))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(x, 6), 4, 4)


# ---------------------------------------------------------------- ទំព័រ
class StudioPage(QWidget):
    """host = MainWindow (gemini_key · voice_settings · _uses_router · simple_page.model · orig_volume …)"""
    ui = pyqtSignal(object)  # thread → GUI: callable

    def __init__(self, host, backend, output_dir):
        super().__init__()
        self.host, self.backend, self.output_dir = host, backend, output_dir
        self.setObjectName("studioPage")
        self.setAcceptDrops(True)
        self.ui.connect(lambda fn: fn())
        self.video, self.title, self.rows = "", "", []
        self.tmp = tempfile.mkdtemp(prefix="studio_")
        self.busy, self.cancel = "", threading.Event()
        self.pcm, self.pcm_cues, self.dirty, self.dub_file = None, None, True, ""
        self.chain, self._filling, self._job_timer, self._thumb_dir = [], False, None, ""
        self._audio, self._audio_of = None, ""  # សំឡេង 16k mono របស់វីដេអូ (pitch · reference ចម្លងសំឡេង)
        # រឿង Part ច្រើន: មិន Merge ពេលបើក — parts = [(ឯកសារ, ចាប់ផ្តើម ms, ប្រវែង ms)] · Merge តែពេល Export
        self.parts, self.part_i, self._pending, self._files = [], 0, None, {}
        self._files_lock = threading.Lock()
        self.store = getattr(host, "settings", None) or QSettings("KhmerTTS", "Studio")
        self.cfg = load_cfg(self.store)
        self.blur_rects = []  # ប្រអប់ព្រិល (ប្រភាគនៃស៊ុម) — Export ព្រិលតំបន់ទាំងនេះ
        self.bg_file, self.vocals_file, self._vol_before = "", "", None  # Remove vocal: ភ្លេងគ្មានការនិយាយ · សំឡេងនិយាយសុទ្ធ
        self._build()
        self.restyle()
        self._refresh_state()

    # ================= UI =================
    def _btn(self, text, name="", tip="", fn=None, checkable=False):
        b = QPushButton(text)
        if name:
            b.setObjectName(name)
        if tip:
            b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setCheckable(checkable)
        if fn:
            b.clicked.connect(fn)
        return b

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)
        # ---- របារលើ
        top = QHBoxLayout()
        top.setSpacing(8)
        self.lbl_title = QLabel("🎬 Dub Studio")
        self.lbl_title.setObjectName("stTitle")
        top.addWidget(self.lbl_title)
        self.lbl_file = QLabel("មិនទាន់មានវីដេអូ — ចុច Upload ឬអូសវីដេអូ/Folder មកទីនេះ")
        self.lbl_file.setObjectName("stMuted")
        top.addWidget(self.lbl_file, 1)
        self.btn_upload = self._btn("📁 Upload a Folder", "stOrange", "ជ្រើស Folder រឿង (Part ច្រើន → Merge ស្វ័យប្រវត្តិ)", self._pick_folder)
        self.btn_file = self._btn("🎞 Video", "stGhost", "ជ្រើសឯកសារវីដេអូមួយ", self._pick_file)
        self.btn_srt_in = self._btn("📥 SRT", "stGhost", "នាំចូល SRT (ខ្មែរ ឬភាសាដើម) ជំនួសការស្តាប់", self._import_srt)
        self.model = QComboBox()
        self.model.setToolTip("Model បកប្រែ (ដូចទំព័រ Auto)")
        self.model.setMinimumWidth(170)
        self.lang = QComboBox()
        for k, lab in LANGS:
            self.lang.addItem(lab, k)
        self.lang.setCurrentIndex(1)
        self.lang.setToolTip("ភាសាដើមរបស់វីដេអូ")
        self.btn_recap = self._btn("🎞 សង្ខេបរឿង RECAP", "stGold", "AI សង្ខេបរឿង → វីដេអូខ្លី (ឈុតសំខាន់ៗ) + អត្ថបទអ្នកនិទានរឿង", self.recap)
        self.btn_set = self._btn("⚙", "stGhost", "ការកំណត់កម្រិតខ្ពស់ (Export · TTS · តួអង្គ · key)", self.open_settings)
        self.btn_set.setAccessibleName("ការកំណត់")
        self.btn_srt_out = self._btn("💾 SRT", "stGhost", "រក្សាទុក SRT ខ្មែរ ក្បែរវីដេអូ", self._save_srt)
        self.btn_export = self._btn("⬆ Export This", "stOrange", "ដាក់សំឡេង dub ចូលវីដេអូ → ថត Output", self.export)
        for wdg in (self.btn_upload, self.btn_file, self.btn_srt_in, self.model, self.lang, self.btn_recap, self.btn_set,
                    self.btn_srt_out, self.btn_export):
            top.addWidget(wdg)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        # ---- ឆ្វេង: វីដេអូ
        left = QFrame()
        left.setObjectName("stPanel")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(10, 8, 10, 8)
        ll.setSpacing(6)
        head = QHBoxLayout()
        cap = QLabel("ORIGINAL")
        cap.setObjectName("stCaps")
        head.addWidget(cap)
        self.lbl_res = QLabel("—")
        self.lbl_res.setObjectName("stChip")
        head.addWidget(self.lbl_res)
        head.addStretch()
        self.orig_vol = QSlider(Qt.Horizontal)
        self.orig_vol.setRange(0, 150)
        self.orig_vol.setValue(self.cfg["orig_vol"])
        self.orig_vol.setFixedWidth(100)
        self.orig_vol.setToolTip("កម្រិតសំឡេងដើម (ឬភ្លេង ពេល Remove vocal) ក្នុងវីដេអូ Export · 0 = បិទសំឡេងដើម")
        self.orig_vol.setAccessibleName("Original volume")
        self.lbl_ov = QLabel(f"{self.cfg['orig_vol']} %")
        self.lbl_ov.setObjectName("stMuted")
        self.lbl_ov.setMinimumWidth(46)
        self.orig_vol.valueChanged.connect(lambda v: self.lbl_ov.setText(f"{v} %"))
        self.orig_vol.valueChanged.connect(self._monitor_changed)
        ll.addLayout(head)
        self.view = QVideoWidget()
        self.view.setMinimumSize(260, 200)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        ll.addWidget(self.view, 1)
        self.player = QMediaPlayer(self, QMediaPlayer.VideoSurface)
        self.player.setVideoOutput(self.view)
        self.player.setNotifyInterval(80)
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(self._on_dur)
        self.player.stateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_media)
        self.dub_player = QMediaPlayer(self)
        ctl = QHBoxLayout()
        ctl.setSpacing(6)
        self.btn_play = self._btn("▶", "stRound", "Play / Pause (Space)", self.toggle_play)
        self.btn_play.setAccessibleName("Play")
        self.btn_play.setShortcut(Qt.Key_Space)
        self.seek_bar = QSlider(Qt.Horizontal)
        self.seek_bar.setRange(0, 0)
        self.seek_bar.sliderMoved.connect(self.seek)
        self.lbl_time = QLabel("0:00 / 0:00")
        self.lbl_time.setObjectName("stMuted")
        self.speed = QComboBox()
        for s in ("0.5x", "0.75x", "1x", "1.25x", "1.5x", "2x"):
            self.speed.addItem(s, float(s[:-1]))
        self.speed.setCurrentIndex(2)
        self.speed.currentIndexChanged.connect(lambda _: (self.player.setPlaybackRate(self.speed.currentData()),
                                                          self.dub_player.setPlaybackRate(self.speed.currentData())))
        self.btn_dub_mon = self._btn("🎧 Dub", "stToggle", "ស្តាប់សំឡេង dub ជំនួសសំឡេងដើម ពេល Play (ក្រោយ Generate Voice)",
                                     self._monitor_changed, checkable=True)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(90)
        self.vol.setFixedWidth(80)
        self.vol.setToolTip("កម្រិតសំឡេង")
        self.vol.valueChanged.connect(self._monitor_changed)
        ll.addWidget(self.seek_bar)
        for wdg in (self.btn_play, self.lbl_time):
            ctl.addWidget(wdg)
        ctl.addStretch()
        for wdg in (self.speed, self.btn_dub_mon, self.vol):
            ctl.addWidget(wdg)
        ll.addLayout(ctl)
        foot = QHBoxLayout()
        self.lbl_status = QLabel("● 0 lines voiced.")
        self.lbl_status.setObjectName("stMuted")
        foot.addWidget(self.lbl_status, 1)
        foot.addWidget(self._btn("Show result", "stGhost", "បើកវីដេអូ Export ចុងក្រោយ", self._show_result))
        foot.addWidget(self._btn("📂 Show in folder", "stGhost", "បើកថត Output", lambda: self.host._nav_go("output")))
        ll.addLayout(foot)
        split.addWidget(left)

        # ---- ស្តាំ: Dialogue
        right = QFrame()
        right.setObjectName("stPanel")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(10, 8, 10, 8)
        rl.setSpacing(8)
        h1 = QHBoxLayout()
        h1.setSpacing(8)
        cap = QLabel("DIALOGUE")
        cap.setObjectName("stCaps")
        h1.addWidget(cap)
        t = QLabel("Khmer dub")
        t.setObjectName("stTitle")
        h1.addWidget(t)
        h1.addStretch()
        tgt = QLabel("🇰🇭 Khmer")
        tgt.setObjectName("stChip")
        tgt.setToolTip("ភាសាគោលដៅ")
        h1.addWidget(tgt)
        self.btn_novocal = self._btn("🎤 Remove vocal", "stToggle", "លុបសំឡេងនិយាយដើមចេញពីវីដេអូ រក្សាតែភ្លេង/សំឡេងបរិយាកាស (Demucs · GPU) — វីដេអូ Export ប្រើភ្លេងនេះជាមួយសំឡេង dub", self._novocal_toggled, checkable=True)
        h1.addWidget(self.btn_novocal)
        self.btn_blur = self._btn("🌫 Blur text", "stToggle", "ព្រិលអក្សរដើមលើវីដេអូ (Subtitle / Logo) — គូរប្រអប់លើរូប · វីដេអូ Export នឹងព្រិលតំបន់នោះ",
                                  self.pick_blur, checkable=True)
        h1.addWidget(self.btn_blur)
        self.btn_rewrite = self._btn("🪄 Rewrite All", "stGhost", "បកប្រែគ្រប់បន្ទាត់ជាខ្មែរ (ពីអត្ថបទដើម)", self.translate)
        self.btn_auto = self._btn("⚡ Auto Dub", "stPurple", "Transcribe → បកប្រែ → Detect Speakers → Generate Voice ក្នុងមួយចុច", self.auto_dub)
        self.btn_transcribe = self._btn("✦ Transcribe", "stCyan", "ស្តាប់វីដេអូ → បន្ទាត់ Dialogue (engine ខាងក្រោម)", self.transcribe)
        for b in (self.btn_rewrite, self.btn_auto, self.btn_transcribe):
            h1.addWidget(b)
        rl.addLayout(h1)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.engine_group = QButtonGroup(self)
        for k, (key, label, tip) in enumerate(ENGINES):
            b = self._btn(label, "stEngine", tip, checkable=True)
            b.setProperty("engine", key)
            self.engine_group.addButton(b, k)
            chips.addWidget(b, 1)
        self.engine_group.button(0).setChecked(True)
        rl.addLayout(chips)
        tools = QHBoxLayout()
        tools.setSpacing(6)
        tools.addWidget(self._btn("＋ Add Text", "stOutline", "បន្ថែមបន្ទាត់ត្រង់ទីតាំង playhead", self._add_row))
        b = self._btn("🗑", "stGhost", "លុបបន្ទាត់ដែលជ្រើស (Delete)", self._delete_rows)
        b.setAccessibleName("លុបបន្ទាត់")
        tools.addWidget(b)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 ស្វែងរក...")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(150)
        self.search.setMaximumWidth(240)
        self.search.textChanged.connect(self._filter)
        tools.addWidget(self.search)
        for text, d in (("A−", -1), ("A+", 1)):
            b = self._btn(text, "stGhost", "ទំហំអក្សរ", lambda _=False, d=d: self._font(d))
            tools.addWidget(b)
        self.follow = QCheckBox("Follow playback")
        self.follow.setChecked(True)
        tools.addWidget(self.follow)
        tools.addStretch()
        self.male = QComboBox()
        for v, lab in MALE_VOICES:
            self.male.addItem("♂ " + lab, v)
        self.male.setObjectName("stMale")
        self.male.setToolTip("សំឡេងប្រុស")
        self.btn_male = self._btn("♂ Male 1", "stMaleBtn", "កំណត់បន្ទាត់ដែលជ្រើស = ប្រុស", lambda: self._set_gender("male"))
        self.female = QComboBox()
        for v, lab in FEMALE_VOICES:
            self.female.addItem("♀ " + lab, v)
        self.female.setObjectName("stFemale")
        self.female.setToolTip("សំឡេងស្រី")
        self.btn_female = self._btn("♀ Female 1", "stFemaleBtn", "កំណត់បន្ទាត់ដែលជ្រើស = ស្រី", lambda: self._set_gender("female"))
        self.btn_kid = self._btn("🧒 Kid", "stKidBtn", "កំណត់បន្ទាត់ដែលជ្រើស = ក្មេង", lambda: self._set_gender("kid"))
        self.btn_detect = self._btn("⛶ Detect Speakers", "stLink", "ចាប់ភេទ ស្រី/ប្រុស របស់បន្ទាត់នីមួយៗ តាមកម្ពស់សំឡេង (pitch)", self.detect)
        for wdg in (self.btn_male, self.male, self.btn_female, self.female, self.btn_kid):
            tools.addWidget(wdg)
        rl.addLayout(tools)
        spk = QFrame()
        spk.setObjectName("stInfoBar")
        sk = QHBoxLayout(spk)
        sk.setContentsMargins(10, 4, 6, 4)
        self.lbl_speakers = QLabel("👥 Speakers not separated yet")
        self.lbl_speakers.setObjectName("stMuted")
        sk.addWidget(self.lbl_speakers, 1)
        self.btn_match = self._btn("👥 Match voices", "stGhost", "ផ្គូផ្គងសំឡេងនឹងតួអង្គ: បែងចែក Male 1/2 · Female 1/2 · Kid តាមកម្ពស់សំឡេង (តួអង្គផ្សេង = សំឡេងផ្សេង)", lambda: self.analyze("match"))
        self.btn_mwk = self._btn("🙂 Man / woman / kid", "stGhost", "បែងចែក បុរស / ស្ត្រី / ក្មេង តាមកម្ពស់សំឡេង", lambda: self.analyze("mwk"))
        self.btn_clone = self._btn("🧬 Clone voices", "stClone", "ចម្លងសំឡេងតួអង្គដើមពីវីដេអូ — បន្ទាត់នីមួយៗនិយាយខ្មែរដោយសំឡេងតួអង្គនោះ (VoxCPM2 · GPU)",
                                   self._clone_toggled, checkable=True)
        for wdg in (self.btn_detect, self.btn_match, self.btn_mwk, self.btn_clone):
            sk.addWidget(wdg)
        rl.addWidget(spk)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "START", "DUB TEXT", "VOICE PROFILE"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(0, 26)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setWordWrap(True)
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellClicked.connect(self._cell_clicked)
        rl.addWidget(self.table, 1)
        self.empty = QLabel("💬\nNo dialogue yet.\nជ្រើស engine ខាងលើ ហើយចុច Transcribe · នាំចូល SRT · ឬ ＋ Add Text", self.table)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("stEmpty")
        self.empty.setAttribute(Qt.WA_TransparentForMouseEvents)
        legend = QLabel(f"<span style='color:{P['green']}'>●</span> Ready &nbsp; <span style='color:{P['red']}'>●</span> Needs a retry"
                        f" &nbsp; <span style='color:{P['amber']}'>●</span> Changed")
        legend.setObjectName("stMuted")
        rl.addWidget(legend)
        gen = QHBoxLayout()
        gen.setSpacing(8)
        self.tts = QComboBox()
        self.tts.addItem("Edge TTS · FREE", "edge")
        self.tts.addItem("VoxCPM2 AI · GPU", "voxcpm")
        self.tts.currentIndexChanged.connect(self._tts_changed)
        self.tts.setToolTip("Engine សំឡេង: Edge TTS (Internet · លឿន) ឬ VoxCPM2 (AI លើ GPU ផ្ទាល់ខ្លួន · សំឡេង 6 តួ · ចម្លងសំឡេង)")
        self.fit = QComboBox()
        self.fit.addItem("Auto-Fit: Auto speed", True)
        self.fit.addItem("Auto-Fit: Off", False)
        self.fit.setToolTip("ពន្លឿនសំឡេងឱ្យសមនឹងពេលវេលារបស់បន្ទាត់")
        self.btn_gen = self._btn("🔊 Generate Voice", "stCyan", "បង្កើតសំឡេងខ្មែរសម្រាប់គ្រប់បន្ទាត់", self.generate)
        self.lbl_prog = QLabel("0 / 0")
        self.lbl_prog.setObjectName("stMuted")
        self.btn_stop = self._btn("■ Stop", "stGhost", "បញ្ឈប់ការងារកំពុងរត់", self.stop)
        for wdg in (self.tts, self.fit, self.btn_gen, self.lbl_prog):
            gen.addWidget(wdg)
        gen.addStretch()
        gen.addWidget(self.btn_stop)
        rl.addLayout(gen)
        sl = QHBoxLayout()
        sl.setSpacing(8)
        cap = QLabel("🔈 ORIGINAL VOL")
        cap.setObjectName("stCaps")
        for wdg in (cap, self.orig_vol, self.lbl_ov):
            sl.addWidget(wdg)
        self.volume = self._slider(sl, "🔊 DUB VOL", 0, 200, self.cfg["dub_vol"], "{} %", dirty=False)
        self.volume.setToolTip("កម្រិតសំឡេងបកប្រែ (dub) ក្នុងវីដេអូ Export — ប្តូរបានដោយមិនចាំបាច់ Generate ម្តងទៀត")
        self.volume.setAccessibleName("Dub volume")
        self.volume.valueChanged.connect(self._monitor_changed)
        self.pitch = self._slider(sl, "PITCH", -50, 50, 0, "{} Hz")
        self.rate = self._slider(sl, "SPEED", 50, 200, 100, lambda v: f"{v / 100:.2f}")
        sl.addStretch()
        z = QLabel("ZOOM")
        z.setObjectName("stCaps")
        sl.addWidget(z)
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(10, 400)
        self.zoom.setValue(10)
        self.zoom.setFixedWidth(120)
        self.zoom.setToolTip("ពង្រីក Timeline")
        sl.addWidget(self.zoom)
        rl.addLayout(sl)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 7)
        root.addWidget(split, 1)
        # ---- Timeline
        self.timeline = Timeline()
        self.timeline.seek.connect(self.seek)
        self.zoom.valueChanged.connect(lambda v: (setattr(self.timeline, "zoom", v / 10), self.timeline.update()))
        root.addWidget(self.timeline)
        self.table.installEventFilter(self)

    def _slider(self, lay, caption, lo, hi, val, fmt, dirty=True):
        cap = QLabel(caption)
        cap.setObjectName("stCaps")
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        s.setFixedWidth(100)
        s.setToolTip(caption)
        lab = QLabel()
        lab.setObjectName("stMuted")
        lab.setMinimumWidth(52)
        show = (lambda v: lab.setText(fmt(v))) if callable(fmt) else (lambda v: lab.setText(fmt.format(v)))
        show(val)
        s.valueChanged.connect(show)
        if dirty:
            s.valueChanged.connect(lambda _: self._mark_dirty())
        for wdg in (cap, s, lab):
            lay.addWidget(wdg)
        return s

    def restyle(self):
        c = P
        self.setStyleSheet(f"""
QWidget#studioPage {{ background: {c['bg']}; }}
QWidget#studioPage QLabel {{ color: {c['text']}; background: transparent; }}
QFrame#stPanel {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QLabel#stTitle {{ font-size: 15pt; font-weight: bold; }}
QLabel#stCaps {{ color: {c['muted']}; font-weight: bold; font-size: 8.5pt; }}
QLabel#stMuted {{ color: {c['muted']}; }}
QLabel#stChip {{ color: {c['text']}; background: {c['card']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 2px 8px; font-weight: bold; }}
QFrame#stInfoBar {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QLabel#stEmpty {{ color: {c['muted']}; font-size: 11pt; }}
QWidget#studioPage QPushButton {{ color: {c['text']}; background: {c['card']}; border: 1px solid {c['border']}; border-radius: 8px;
    padding: 7px 12px; font-weight: bold; }}
QWidget#studioPage QPushButton:hover {{ border-color: {c['cyan']}; }}
QWidget#studioPage QPushButton:focus {{ border: 2px solid #FFFFFF; }}
QWidget#studioPage QPushButton:disabled {{ color: {c['faint']}; background: {c['input']}; border-color: {c['border']}; }}
QWidget#studioPage QPushButton#stOrange {{ background: {c['orange']}; color: #1A0E03; border: none; }}
QWidget#studioPage QPushButton#stOrange:hover {{ background: #FFA94D; }}
QWidget#studioPage QPushButton#stPurple {{ background: {c['purple']}; color: #FFFFFF; border: none; }}
QWidget#studioPage QPushButton#stPurple:hover {{ background: #E11D48; }}
QWidget#studioPage QPushButton#stCyan {{ background: {c['cyan']}; color: #1A1203; border: none; }}
QWidget#studioPage QPushButton#stCyan:hover {{ background: #FFE08A; }}
QWidget#studioPage QPushButton#stOutline {{ background: transparent; border: 1px solid {c['cyan']}; color: {c['cyan']}; }}
QWidget#studioPage QPushButton#stLink {{ background: transparent; border: none; color: {c['cyan']}; }}
QWidget#studioPage QPushButton#stGold {{ background: transparent; border: 1px solid {c['amber']}; color: {c['amber']}; }}
QWidget#studioPage QPushButton#stGold:hover {{ background: {c['amber']}; color: #1A1203; }}
QWidget#studioPage QPushButton#stClone {{ background: #3A0E1C; border: 1px solid {c['purple']}; color: #FFD0D8; }}
QWidget#studioPage QPushButton#stClone:checked {{ background: {c['purple']}; color: #FFFFFF; border: 1px solid {c['amber']}; }}
QWidget#studioPage QPushButton#stKidBtn {{ background: #40300A; border: 1px solid {c['kid']}; color: #FDE68A; }}
QWidget#studioPage QPushButton#stMaleBtn {{ background: #12305A; border: 1px solid {c['blue']}; color: #BFDBFE; }}
QWidget#studioPage QPushButton#stFemaleBtn {{ background: #4A1230; border: 1px solid {c['pink']}; color: #FBCFE8; }}
QWidget#studioPage QPushButton#stEngine {{ background: {c['input']}; border: 1px solid {c['border']}; color: {c['muted']}; padding: 9px 8px; }}
QWidget#studioPage QPushButton#stEngine:checked {{ background: {c['amber']}; color: #1A1203; border: none; }}
QWidget#studioPage QPushButton#stToggle:checked {{ background: {c['cyan']}; color: #1A1203; border: none; }}
QWidget#studioPage QPushButton#stRound {{ border-radius: 16px; min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; padding: 0; }}
QWidget#studioPage QComboBox, QWidget#studioPage QLineEdit {{ color: {c['text']}; background: {c['input']}; border: 1px solid {c['border']};
    border-radius: 8px; padding: 6px 10px; }}
QWidget#studioPage QComboBox:focus, QWidget#studioPage QLineEdit:focus {{ border-color: {c['cyan']}; }}
QWidget#studioPage QComboBox QAbstractItemView {{ background: {c['card']}; color: {c['text']}; selection-background-color: {c['cyan']};
    selection-color: #1A1203; }}
QWidget#studioPage QCheckBox {{ color: {c['text']}; font-weight: bold; }}
QWidget#studioPage QTableWidget {{ background: {c['input']}; alternate-background-color: {c['panel']}; color: {c['text']};
    border: 1px solid {c['border']}; border-radius: 8px; gridline-color: {c['border']}; selection-background-color: #4A2F12;
    selection-color: #FFFFFF; }}
QWidget#studioPage QHeaderView::section {{ background: {c['panel']}; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 8px 6px; font-weight: bold; }}
QWidget#studioPage QSlider::groove:horizontal {{ height: 4px; background: {c['border']}; border-radius: 2px; }}
QWidget#studioPage QSlider::sub-page:horizontal {{ background: {c['cyan']}; border-radius: 2px; }}
QWidget#studioPage QSlider::handle:horizontal {{ width: 14px; margin: -6px 0; border-radius: 7px; background: {c['cyan']}; }}
QWidget#studioPage QSplitter::handle {{ background: transparent; width: 8px; }}
""")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.empty.setGeometry(0, 40, self.table.width(), max(self.table.height() - 40, 60))

    def showEvent(self, e):
        super().showEvent(e)
        self._sync_models()
        self.empty.setGeometry(0, 40, self.table.width(), max(self.table.height() - 40, 60))

    def eventFilter(self, obj, e):
        if obj is self.table and e.type() == e.KeyPress and e.key() == Qt.Key_Delete \
                and self.table.state() != QAbstractItemView.EditingState:
            self._delete_rows()
            return True
        if obj is self.table and e.type() == e.Resize:
            self.empty.setGeometry(0, 40, self.table.width(), max(self.table.height() - 40, 60))
        return super().eventFilter(obj, e)

    def _sync_models(self):
        """បញ្ជី model = ដូច combo របស់ទំព័រ Auto (ប្តូរតាមប្រភេទ key)"""
        src = getattr(getattr(self.host, "simple_page", None), "model", None)
        if src is None:
            return
        cur = self.model.currentData() or src.currentData()
        self.model.clear()
        for k in range(src.count()):
            self.model.addItem("✦ " + src.itemText(k), src.itemData(k))
        if not self.model.count():
            self.model.addItem("✦ gemini-3.8-flash", "gemini-3.8-flash")
        k = self.model.findData(cur)
        self.model.setCurrentIndex(max(k, 0))

    # ================= ស្ថានភាព =================
    def say(self, text):
        self.lbl_status.setText(text)

    def _set_busy(self, what):
        self.busy = what
        if what:
            self.cancel.clear()
        self._refresh_state()

    def _refresh_state(self):
        has_video, has_rows, idle = bool(self.video), bool(self.rows), not self.busy
        for b, ok in ((self.btn_upload, idle), (self.btn_file, idle), (self.btn_srt_in, idle), (self.btn_transcribe, idle and has_video),
                      (self.btn_auto, idle and has_video), (self.btn_rewrite, idle and has_rows), (self.btn_detect, idle and has_rows and has_video),
                      (self.btn_gen, idle and has_rows), (self.btn_export, idle and has_rows and has_video),
                      (self.btn_srt_out, has_rows), (self.btn_stop, not idle), (self.btn_match, idle and has_rows and has_video),
                      (self.btn_novocal, idle and has_video), (self.btn_blur, idle and has_video),
                      (self.btn_mwk, idle and has_rows and has_video), (self.btn_recap, idle and has_rows and has_video)):
            b.setEnabled(ok)
        self.empty.setVisible(not has_rows)
        voiced = sum(r.get("state") == "ready" for r in self.rows)
        self.lbl_prog.setText(f"{voiced} / {len(self.rows)}")
        if idle:
            self.say(f"<span style='color:{P['green']}'>●</span> {voiced} lines voiced." if has_rows else "● 0 lines voiced.")
        count = {}
        for r in self.rows:
            count[voice_label(r)] = count.get(voice_label(r), 0) + 1
        self.lbl_speakers.setText("👥 " + " · ".join(f"{k} {v}" for k, v in sorted(count.items()))
                                  if any(r.get("gender") for r in self.rows) else "👥 Speakers not separated yet")
        self.timeline.rows = self.rows
        self.timeline.update()

    def running(self):
        return bool(self.busy)

    def stop(self):
        self.cancel.set()
        self.chain = []
        self.say("⏹ កំពុងបញ្ឈប់...")

    def _fail(self, what, err):
        self.chain = []
        self._set_busy("")
        self._fail_novocal()
        if what == "merge":
            self.lbl_file.setText("✕ Merge បរាជ័យ — សាក Upload ម្តងទៀត")
        self.say(f"<span style='color:{P['red']}'>✕</span> {what}: {str(err)[:200]}")
        self.lbl_status.setToolTip(str(err))

    def _thread(self, what, work, done):
        """work() ក្នុង thread → done(result) លើ GUI · កំហុស → _fail"""
        self._set_busy(what)

        def run():
            try:
                res = work()
            except Exception as e:  # noqa: BLE001
                return self.ui.emit(lambda e=e: self._fail(what, e))
            self.ui.emit(lambda: done(res))
        threading.Thread(target=run, daemon=True).start()

    def _poll(self, what, job_id, tick, done):
        """job (srt_dub.jobs) → tick(job) រាល់ 300ms → done(job)"""
        self._set_busy(what)
        timer = QTimer(self)
        self._job_timer = timer

        def step():
            job = srt_dub.jobs[job_id]
            if job["status"] == "error":
                timer.stop()
                return self._fail(what, job["error"])
            if job["status"] == "done":
                timer.stop()
                return done(job)
            if self.cancel.is_set():
                timer.stop()
                self._set_busy("")
                if what == "merge":
                    self.lbl_file.setText("⏹ Merge ត្រូវបានបញ្ឈប់ — Upload ម្តងទៀត")
                return self.say("⏹ បានបញ្ឈប់ (ការងារផ្ទៃខាងក្រោយអាចបន្តបន្តិចទៀត)")
            tick(job)
        timer.timeout.connect(step)
        timer.start(300)

    def _next(self):
        """ជំហានបន្ទាប់របស់ Auto Dub"""
        self._set_busy("")
        if self.chain:
            step = self.chain.pop(0)
            QTimer.singleShot(0, getattr(self, step))

    # ================= ប្រភព =================
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and not self.busy:
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        if paths:
            self.load(paths[0])

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "ជ្រើស Folder រឿង")
        if d:
            self.load(d)

    def _pick_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "ជ្រើសវីដេអូ", "", "Video (*.mp4 *.mkv *.mov *.webm *.avi *.flv *.ts *.m4v)")
        if f:
            self.load(f)

    def load(self, path):
        """ឯកសារវីដេអូ ឬ Folder រឿង (Part ច្រើន → Merge ទៅថតបណ្តោះអាសន្ន)"""
        if self.busy:
            return
        path = os.path.normpath(path)
        if os.path.isdir(path):
            videos = sorted((os.path.join(path, f) for f in os.listdir(path)
                             if os.path.splitext(f)[1].lower() in VIDEO_EXT), key=lambda p: [int(t) if t.isdigit() else t.lower()
                                                                                             for t in __import__("re").split(r"(\d+)", os.path.basename(p))])
            if not videos:
                return self.say("⚠ Folder នេះគ្មានវីដេអូ")
            title = os.path.basename(path)
            if len(videos) > 1:
                # គ្មាន Merge: បង្ហាញភ្លាម · Part ទាំងអស់ត​គ្នាជា Timeline តែមួយ (Merge តែពេល Export)
                self._opened(videos[0], title, path, videos)

                def got(infos):
                    if self.folder != path or not self.parts:
                        return
                    at, parts = 0, []
                    for v, info in zip(videos, infos):
                        d = int(info["duration"] * 1000)
                        parts.append((v, at, d))
                        at += d
                    self.parts = parts
                    self._set_busy("")
                    self._on_dur(0)
                    self._make_thumbs(videos[0])
                    self.say(f"🎞 {len(parts)} Part · {_fmt(at)} — រួចរាល់ (មិន Merge)")
                return self._thread("បើក", lambda: video_merge.probe_all(videos), got)
            return self._opened(videos[0], title, path)
        if os.path.splitext(path)[1].lower() in VIDEO_EXT | AUDIO_EXT:
            return self._opened(path, os.path.splitext(os.path.basename(path))[0], os.path.dirname(path))
        self.say("⚠ មិនមែនវីដេអូ")

    def _opened(self, video, title, folder, videos=None):
        self._set_busy("")
        self.video, self.title, self.folder = video, title, folder
        self.parts = [(v, 0, 0) for v in videos or []]  # ប្រវែងពិតមកក្រោយ probe
        self.part_i, self._pending, self._files = 0, None, {}
        self.rows, self.pcm, self.dirty, self.dub_file = [], None, True, ""
        self._audio = None
        self.bg_file, self.vocals_file = "", ""
        self.btn_novocal.setChecked(False)
        self.btn_novocal.setText("🎤 Remove vocal")
        self.blur_rects = []
        self._blur_label()
        self._fill()
        self.lbl_file.setText(f"🎞 {title}" + (f"  ·  {len(self.parts)} Part" if self.parts else ""))
        self.lbl_file.setToolTip(folder if self.parts else video)
        self.player.setMedia(QMediaContent(QUrl.fromLocalFile(video)))
        self.player.pause()
        self.dub_player.setMedia(QMediaContent())
        self.btn_dub_mon.setChecked(False)
        self._monitor_changed()
        try:
            info = video_merge._probe(video)
            self.lbl_res.setText(f"{info.get('width', '?')}×{info.get('height', '?')}" if info.get("width") else "audio")
        except Exception:  # noqa: BLE001
            self.lbl_res.setText("—")
        if not self.parts:
            self._make_thumbs(video)
        # SRT ក្នុង Folder (ឈ្មោះដូចរឿង) → នាំចូលភ្លាម
        for cand in (os.path.join(folder, title + ".srt"), os.path.splitext(video)[0] + ".srt"):
            if os.path.isfile(cand):
                self._import_srt(cand)
                break
        self._refresh_state()

    def _make_thumbs(self, video):
        """រូបតូចៗសម្រាប់ Timeline (ffmpeg ម្តង → ~60 រូប) ក្នុង thread"""
        self.timeline.thumbs = []
        out = tempfile.mkdtemp(prefix="thumbs_", dir=self.tmp)
        self._thumb_dir = out

        parts = list(self.parts)

        def work_parts():
            """Part ច្រើន: រូបពីកណ្តាលចន្លោះនីមួយៗ (seek លឿន) — ~60 រូបសរុប"""
            from concurrent.futures import ThreadPoolExecutor
            total = max(parts[-1][1] + parts[-1][2], 1)
            shots = []
            for v, at, d in parts:
                n = max(1, round(60 * d / total))
                shots += [(v, at + int(d * k / n), d * k / n / 1000) for k in range(n)]

            def one(k):
                v, _, ss = shots[k]
                f = os.path.join(out, f"t{k:04d}.jpg")
                subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{ss:.2f}", "-i", v, "-an",
                                "-frames:v", "1", "-vf", "scale=-2:56", "-q:v", "6", f],
                               capture_output=True, creationflags=whisper_srt._NO_WINDOW, timeout=60)
                return (shots[k][1], f) if os.path.isfile(f) else None
            with ThreadPoolExecutor(4) as ex:
                return None, [x for x in ex.map(one, range(len(shots))) if x]

        def work():
            if parts:
                return work_parts()
            dur = video_dub.probe(video)[0]
            step = max(dur / 60, 1.0)
            fast = ["-skip_frame", "nokey"] if dur > 600 else []
            subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", *fast, "-i", video, "-an",
                            "-vf", f"fps=1/{step:.3f},scale=-2:56", "-q:v", "6", os.path.join(out, "t%04d.jpg")],
                           capture_output=True, creationflags=whisper_srt._NO_WINDOW, timeout=900)
            return step, sorted(os.listdir(out))

        def done(res):
            step, files = res
            if out != self._thumb_dir:
                return
            self.timeline.thumbs = ([(ms, QPixmap(f)) for ms, f in files] if step is None else
                                    [(int(k * step * 1000), QPixmap(os.path.join(out, f))) for k, f in enumerate(files)])
            self.timeline.update()

        def run():
            try:
                res = work()
            except Exception:  # noqa: BLE001 — គ្មានរូបតូច មិនបញ្ឈប់អ្វីទេ
                return
            self.ui.emit(lambda: done(res))
        threading.Thread(target=run, daemon=True).start()

    def _import_srt(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "នាំចូល SRT", getattr(self, "folder", ""), "SRT (*.srt)")
            if not path:
                return
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                cues = srt_dub.parse_srt(f.read())
        except OSError as e:
            return self.say(f"✕ {e}")
        self.rows = [{"start": c["start"], "end": c["end"], "text": c["text"], "original": "", "gender": c.get("gender"),
                      "state": ""} for c in cues]
        self._mark_dirty()
        self._fill()
        self._refresh_state()
        self.say(f"📥 នាំចូល {len(self.rows)} បន្ទាត់ពី {os.path.basename(path)}")

    def _save_srt(self):
        if not self.rows:
            return
        base = os.path.join(getattr(self, "folder", "") or self.output_dir, (self.title or "dub") + ".srt")
        path, _ = QFileDialog.getSaveFileName(self, "រក្សាទុក SRT", base, "SRT (*.srt)")
        if path:
            auto_pipeline._write_srt(path, self.rows)
            self.say(f"💾 {os.path.basename(path)}")

    # ================= Player =================
    def toggle_play(self):
        if not self.video:
            return
        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _base(self):
        return self.parts[self.part_i][1] if self.parts else 0

    def _gpos(self):
        """ទីតាំងក្នុងរឿងទាំងមូល (ms)"""
        if self._pending:
            return self._base() + self._pending[0]
        return self._base() + self.player.position()

    def _gdur(self):
        return self.parts[-1][1] + self.parts[-1][2] if self.parts else self.player.duration()

    def _goto(self, k, off, play):
        """ប្តូរទៅ Part k ត្រង់ off ms"""
        self.part_i, self._pending = k, (int(off), play)
        self.player.setMedia(QMediaContent(QUrl.fromLocalFile(self.parts[k][0])))
        self.player.pause()
        self._show_pos(self._base() + int(off))

    def _on_media(self, status):
        if status == QMediaPlayer.EndOfMedia and self.parts and self.part_i + 1 < len(self.parts):
            return self._goto(self.part_i + 1, 0, True)
        if self._pending and status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            off, play = self._pending
            self._pending = None
            self.player.setPosition(off)
            if play:
                self.player.play()

    def seek(self, ms):
        if self.parts:
            ms = min(max(int(ms), 0), max(self._gdur() - 1, 0))
            k = next((i for i, (_, at, d) in enumerate(self.parts) if at <= ms < at + d), self.part_i)
            if k != self.part_i or self._pending:
                playing = self.player.state() == QMediaPlayer.PlayingState or bool(self._pending and self._pending[1])
                self._goto(k, ms - self.parts[k][1], playing)
            else:
                self.player.setPosition(ms - self._base())
        else:
            self.player.setPosition(int(ms))
        if self.btn_dub_mon.isChecked():
            self.dub_player.setPosition(int(ms))

    def _monitor_changed(self, *_):
        """🎧 Dub: ស្តាប់ dub ជំនួសសំឡេងដើម"""
        dub = self.btn_dub_mon.isChecked() and bool(self.dub_file)
        if self.btn_dub_mon.isChecked() and not self.dub_file:
            self.btn_dub_mon.setChecked(False)
            self.say("🎧 ចុច Generate Voice សិន ទើបស្តាប់ dub បាន")
        self.player.setVolume(min(100, int(self.vol.value() * (self.orig_vol.value() / 100 if dub else 1))))
        self.dub_player.setVolume(min(100, int(self.vol.value() * self.volume.value() / 100)) if dub else 0)
        if dub:
            self.dub_player.setPosition(self._gpos())
            if self.player.state() == QMediaPlayer.PlayingState:
                self.dub_player.play()
        else:
            self.dub_player.pause()

    def _on_state(self, state):
        playing = state == QMediaPlayer.PlayingState
        self.btn_play.setText("⏸" if playing else "▶")
        if self.btn_dub_mon.isChecked() and self.dub_file:
            if playing:
                self.dub_player.setPosition(self._gpos())
                self.dub_player.play()
            else:
                self.dub_player.pause()

    def _on_dur(self, ms):
        ms = self._gdur() if self.parts else ms
        self.seek_bar.setRange(0, int(ms))
        self.timeline.duration = int(ms)
        self.timeline.update()
        self._show_pos(self._gpos())

    def _on_pos(self, ms):
        if not self._pending:  # កំពុងប្តូរ Part → player រាយការណ៍ 0 បណ្តោះអាសន្ន
            self._show_pos(self._base() + ms)

    def _show_pos(self, ms):
        if not self.seek_bar.isSliderDown():
            self.seek_bar.setValue(int(ms))
        self.lbl_time.setText(f"{_fmt(ms)} / {_fmt(self._gdur())}"
                              + (f"  ·  Part {self.part_i + 1}/{len(self.parts)}" if self.parts else ""))
        self.timeline.position = int(ms)
        self.timeline.update()
        if self.btn_dub_mon.isChecked() and self.dub_file and abs(self.dub_player.position() - ms) > 350:
            self.dub_player.setPosition(int(ms))  # កុំឱ្យ dub រអិលចេញពីវីដេអូ
        if self.follow.isChecked() and self.rows and self.player.state() == QMediaPlayer.PlayingState \
                and self.table.state() != QAbstractItemView.EditingState:
            k = next((i for i, r in enumerate(self.rows) if r["start"] <= ms < r["end"]), -1)
            if k >= 0 and k != self.table.currentRow():
                self.table.setCurrentCell(k, 2)
                self.table.scrollToItem(self.table.item(k, 2), QAbstractItemView.PositionAtCenter)

    # ================= តារាង =================
    def _dot(self, r):
        return {"ready": P["green"], "retry": P["red"], "changed": P["amber"]}.get(r.get("state"), P["faint"])

    def _fill(self):
        self._filling = True
        t = self.table
        t.setRowCount(len(self.rows))
        for k, r in enumerate(self.rows):
            dot = QTableWidgetItem("●")
            dot.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            dot.setTextAlignment(Qt.AlignCenter)
            dot.setForeground(QColor(self._dot(r)))
            st = QTableWidgetItem(_fmt_ms(r["start"]))
            st.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            st.setForeground(QColor(P["cyan"]))
            st.setToolTip(f"{_fmt_ms(r['start'])} → {_fmt_ms(r['end'])} · ចុច = រំកិលទៅ")
            tx = QTableWidgetItem(r["text"])
            tx.setToolTip(("ដើម: " + r["original"] + "\n" if r.get("original") else "") + "ចុចពីរដង = កែអត្ថបទ")
            vp = QTableWidgetItem(voice_label(r))
            vp.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            vp.setForeground(QColor(voice_color(r)))
            vp.setToolTip("ចុច = ប្តូរ Auto → Male 1 → Male 2 → Female 1 → Female 2 → Kid"
                          + (f"\nកម្ពស់សំឡេង ~{r['hz']:.0f} Hz" if r.get("hz") else ""))
            for col, item in enumerate((dot, st, tx, vp)):
                t.setItem(k, col, item)
        t.resizeRowsToContents()
        self._filling = False
        self._filter(self.search.text())

    def _update_row(self, k):
        r = self.rows[k]
        self._filling = True
        self.table.item(k, 0).setForeground(QColor(self._dot(r)))
        vp = self.table.item(k, 3)
        vp.setText(voice_label(r))
        vp.setForeground(QColor(voice_color(r)))
        self._filling = False

    def _mark_dirty(self, rows=None):
        """អត្ថបទ/សំឡេងប្តូរ → dub ដែលបង្កើតរួចលែងត្រូវ"""
        self.dirty = True
        for k in rows if rows is not None else range(len(self.rows)):
            if self.rows[k].get("state") == "ready":
                self.rows[k]["state"] = "changed"
                if k < self.table.rowCount() and self.table.item(k, 0):
                    self._update_row(k)

    def _item_changed(self, item):
        if self._filling or item.column() != 2:
            return
        k = item.row()
        if self.rows[k]["text"] != item.text():
            self.rows[k]["text"] = item.text()
            self._mark_dirty([k])
            if self.rows[k].get("state") != "changed":
                self.rows[k]["state"] = ""
            self._update_row(k)
            self.table.resizeRowToContents(k)
            self._refresh_state()

    def _cell_clicked(self, row, col):
        if col == 1:
            self.seek(self.rows[row]["start"])
        elif col == 3:
            cur = (self.rows[row].get("gender"), self.rows[row].get("var") or 1 if self.rows[row].get("gender") in ("male", "female") else 1)
            g, v = CYCLE[(CYCLE.index(cur) + 1) % len(CYCLE)] if cur in CYCLE else CYCLE[0]
            self.rows[row].update(gender=g, var=v)
            self._mark_dirty([row])
            self._update_row(row)
            self._refresh_state()

    def _selected(self):
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def _set_gender(self, g):
        sel = self._selected()
        if not sel:
            return self.say("ជ្រើសបន្ទាត់ក្នុងតារាងសិន (Ctrl/Shift + ចុច = ច្រើនបន្ទាត់)")
        for k in sel:  # ចុចម្តងទៀតលើភេទដដែល → ប្តូរ 1 ↔ 2
            r = self.rows[k]
            r["var"] = (2 if (r.get("var") or 1) == 1 else 1) if r.get("gender") == g and g != "kid" and len(sel) == 1 else 1
            r["gender"] = g
            self._update_row(k)
        self._mark_dirty(sel)
        self._refresh_state()

    def _add_row(self):
        pos = self._gpos()
        k = next((i for i, r in enumerate(self.rows) if r["start"] > pos), len(self.rows))
        end = min(pos + 2500, self.rows[k]["start"] if k < len(self.rows) else pos + 2500)
        self.rows.insert(k, {"start": pos, "end": max(end, pos + 500), "text": "", "original": "", "gender": None, "state": ""})
        self._mark_dirty()
        self._fill()
        self._refresh_state()
        self.table.setCurrentCell(k, 2)
        self.table.editItem(self.table.item(k, 2))

    def _delete_rows(self):
        sel = self._selected()
        if not sel or self.busy:
            return
        if len(sel) > 3 and QMessageBox.question(self, "លុបបន្ទាត់", f"លុប {len(sel)} បន្ទាត់?") != QMessageBox.Yes:
            return
        for k in reversed(sel):
            del self.rows[k]
        self._mark_dirty()
        self._fill()
        self._refresh_state()

    def _filter(self, text):
        text = (text or "").strip().lower()
        for k, r in enumerate(self.rows):
            self.table.setRowHidden(k, bool(text) and text not in r["text"].lower() and text not in (r.get("original") or "").lower())

    def _font(self, d):
        f = self.table.font()
        f.setPointSizeF(min(max(f.pointSizeF() + d, 8), 22))
        self.table.setFont(f)
        self.table.resizeRowsToContents()

    # ================= ជំហាន =================
    def engine(self):
        return self.engine_group.checkedButton().property("engine")

    def _key_model(self):
        key = self.host.gemini_key()
        model = (self.model.currentData() if self.host._uses_router() or self.host._uses_openai() else None) or "gemini-3.8-flash"
        return key, model

    def _ensure(self, ok, packages, label, then):
        """Engine មិនទាន់ដំឡើង → សួរ → pip install ក្នុង thread → then()"""
        if ok:
            return True
        self.chain_keep = list(self.chain)
        if QMessageBox.question(self, f"ដំឡើង {label}", f"{label} ត្រូវដំឡើងម្តង (ទាញយកពី Internet)។\n\nដំឡើងឥឡូវនេះ?") != QMessageBox.Yes:
            self.chain = []
            return False
        last = {"t": 0}

        def on_line(line):
            if time.time() - last["t"] > 0.3:
                last["t"] = time.time()
                self.ui.emit(lambda line=line: self.say(f"⬇ {label}: {str(line)[-90:]}"))
        chain = list(self.chain)
        self.chain = []

        def done(_):
            self._set_busy("")
            self.chain = chain
            then()
        self._thread(f"ដំឡើង {label}", (lambda: packages(on_line)) if callable(packages)
                     else (lambda: whisper_srt.pip_install(packages, on_line)), done)
        return False

    def transcribe(self):
        if not self.video or self.busy:
            return
        eng, lang = self.engine(), self.lang.currentData()
        lang = "" if lang == "auto" else lang
        out = os.path.join(self.tmp, "orig.srt")
        if os.path.exists(out):
            os.remove(out)
        if eng == "gemini":
            key, model = self._key_model()
            if not key or not self.backend.hears_audio(key):
                self.chain = []
                return self.say("⚠ Gemini ស្តាប់សំឡេង ត្រូវការ key Relay / Bayon / Google (⚙) — ឬជ្រើស SenseVoice / Whisper")
            src = os.path.join(self.tmp, "gemini_src.mp3")

            def start():
                audio = self._listen()
                transcribe._extract_chunk(audio, 0, transcribe._duration(audio) + 1, src)
                return transcribe.start_job(src, key, model, True, "km", self.tmp, "gemini", self.backend.gemini_post)

            def got(job):
                self._poll("transcribe", job, lambda j: self.say(f"✦ Gemini ស្តាប់ + បកប្រែ {j['done']}/{j['total']} ដុំ"),
                           lambda j: self._transcribed([dict(c) for c in j.get("cues") or []], translated=True))
            return self._thread("transcribe", start, got)
        if eng == "whisper":
            if not self._ensure(whisper_srt.installed(), whisper_srt.WHISPER_PACKAGES, "Whisper", self.transcribe):
                return
            start = lambda src: whisper_srt.start_job(src, out, auto_pipeline.WHISPER_MODEL, lang, True)  # noqa: E731
        else:
            if not self._ensure(funasr_srt.installed(), funasr_srt.PACKAGES, "SenseVoice / Paraformer", self.transcribe):
                return
            funasr_srt.prewarm(eng)
            start = lambda src: funasr_srt.start_job(src, out, eng, lang)  # noqa: E731

        def tick(j):
            if j["status"] == "download":
                self.say(f"⬇ ទាញយក model (ម្តងគត់) {j['done']}%")
            elif j["status"] == "load":
                self.say("⏳ កំពុងបើក model ស្តាប់...")
            else:
                self.say(f"✦ កំពុងស្តាប់ {100 * j['done'] // max(j['total'], 1)}% "
                         f"({'GPU' if 'cuda' in (j.get('device') or '') else 'CPU'})")
        self.say("🎧 កំពុងរៀបចំសំឡេង..." if self.parts else "✦ កំពុងស្តាប់...")
        self._thread("transcribe", lambda: start(self._listen()), lambda job: self._poll(
            "transcribe", job, tick, lambda j: self._transcribed(auto_pipeline._read_srt(out), translated=False)))

    def _transcribed(self, cues, translated):
        if not cues:
            return self._fail("Transcribe", "រកមិនឃើញការនិយាយក្នុងវីដេអូ")
        self.rows = [{"start": c["start"], "end": c["end"], "text": c["text"], "original": c.get("original") or ("" if translated else c["text"]),
                      "gender": c.get("gender"), "state": "", "km": translated} for c in cues]
        self._mark_dirty()
        self._fill()
        self.say(f"✓ {len(self.rows)} បន្ទាត់")
        self._next()

    def translate(self):
        """Rewrite All: អត្ថបទដើម → ខ្មែរ"""
        if not self.rows or self.busy:
            return
        key, model = self._key_model()
        if not key:
            self.chain = []
            return self.say("⚠ ត្រូវការ key បកប្រែ (⚙ ការកំណត់) ឬបើក Gemini Router Direct")
        src = [r.get("original") or r["text"] for r in self.rows]
        total = -(-len(src) // transcribe.TRANSLATE_BATCH)
        count = [0]

        def on_batch():
            count[0] += 1
            self.ui.emit(lambda n=count[0]: self.say(f"🪄 កំពុងបកប្រែ {n}/{total} ដុំ"))
        workers = transcribe.WORKERS if self.backend.hears_audio(key) else auto_pipeline.ROUTER_WORKERS
        self.say(f"🪄 កំពុងបកប្រែ {len(src)} បន្ទាត់...")

        def done(texts):
            for r, s, t in zip(self.rows, src, texts):
                r["original"], r["text"], r["km"] = s, t, True
            self._mark_dirty()
            self._fill()
            self.say(f"✓ បកប្រែរួច {len(texts)} បន្ទាត់")
            self._next()
        self._thread("បកប្រែ", lambda: transcribe.translate_lines(src, "km", key, model, self.backend.gemini_post, on_batch=on_batch,
                                                                    workers=workers, title=self.title), done)

    def _listen(self, hq=False):
        """ប្រភពសំឡេងរបស់រឿង (ហៅក្នុង thread) — វីដេអូតែមួយ: ឯកសារនោះ · Part ច្រើន: សំឡេងត​គ្នា (គ្មាន Merge រូបភាព)
        hq = stereo 48k (សម្រាប់លុបការនិយាយ) · បើមិនដូច្នេះ mono 16k"""
        if not self.parts:
            return self.video
        key = "hq" if hq else "lq"
        with self._files_lock:
            if key not in self._files:
                page, tmp = self, tempfile.mkdtemp(prefix="listen_", dir=self.tmp)

                class Shim:  # auto_pipeline.Pipeline._listen_audio ប្រើតែរបស់ទាំងនេះ
                    opts, stop, state = {"novocal": hq}, self.cancel, [{"notes": []}]

                    def _set(self, i, stage, pct, text=""):
                        page.ui.emit(lambda: page.say(f"🎧 {text}") if text and page.busy else None)
                out = auto_pipeline.Pipeline._listen_audio(Shim(), 0, [p[0] for p in self.parts], tmp)
                self._files[key] = out or self._full_locked()
            return self._files[key]

    def _full_locked(self, job=None):
        if "full" not in self._files:
            videos = [p[0] for p in self.parts]
            m = srt_dub.jobs[video_merge.start_job(videos, self.tmp, "studio", False, [], faststart=False)]
            while m["status"] not in ("done", "error"):
                if job is not None:
                    job["merging"] = m["done"]
                else:
                    self.ui.emit(lambda d=m["done"]: self.say(f"🧩 Merge {len(videos)} Part... {d}%") if self.busy else None)
                time.sleep(0.3)
            if job is not None:
                job.pop("merging", None)
            if m["status"] == "error":
                raise RuntimeError(f"Merge: {m['error']}")
            self._files["full"] = os.path.join(self.tmp, m["file"])
        return self._files["full"]

    def _full(self, job=None):
        """វីដេអូពេញមួយឯកសារ (ហៅក្នុង thread) — Part ច្រើន: Merge ម្តង (ពេល Export / RECAP)"""
        if not self.parts:
            return self.video
        with self._files_lock:
            return self._full_locked(job)

    def _load_audio(self):
        """សំឡេង 16 kHz mono របស់វីដេអូ (cache) — ហៅក្នុង thread"""
        src = self.vocals_file if self.vocals_file and os.path.isfile(self.vocals_file) else self._listen()
        if self._audio is None or self._audio_of != src:
            audio = whisper_srt._load_audio(src)
            if isinstance(audio, str):
                raise RuntimeError("អានសំឡេងពីវីដេអូមិនបាន (ffmpeg)")
            self._audio, self._audio_of = audio, src
        return self._audio

    def detect(self):
        """Detect Speakers: ស្រី/ប្រុស តាម pitch (Auto Dub: តែបន្ទាត់ដែលមិនទាន់កំណត់)"""
        self.analyze("detect")

    def analyze(self, mode):
        """detect = ស្រី/ប្រុស · mwk = បុរស/ស្ត្រី/ក្មេង · match = បែងចែកតួអង្គ 1/2 ក្នុងភេទនីមួយៗ (សំឡេងខុសគ្នា)"""
        if not self.rows or not self.video or self.busy:
            return
        only_missing = mode == "detect" and "generate" in self.chain
        self.say({"detect": "⛶ កំពុងចាប់ភេទ ស្រី/ប្រុស...", "mwk": "🙂 កំពុងបែងចែក បុរស / ស្ត្រី / ក្មេង...",
                  "match": "👥 កំពុងផ្គូផ្គងសំឡេងនឹងតួអង្គ..."}[mode])
        rows = self.rows

        def work():
            audio = self._load_audio()
            hz = []
            for r in rows:
                a, b = int(r["start"] * 16), int(r["end"] * 16)
                hz.append(voice_gender.pitch(audio[max(0, a):max(a, b)]) if b > a else None)
            return hz, voice_gender.detect(audio, rows)

        def done(res):
            hz, genders = res
            for r, h, g in zip(rows, hz, genders):
                r["hz"] = h
                if mode != "detect" and h and h >= self.cfg["kid_hz"]:
                    g = "kid"
                if mode == "match" and r.get("gender"):
                    continue  # match: រក្សាភេទដែលកំណត់រួច — បែងចែកតែ 1/2
                if g and (r.get("gender") is None or not only_missing):
                    r.update(gender=g, var=1)
            if mode == "match":
                for g in ("male", "female"):  # សំឡេងទាប = តួអង្គទី 2 (ចាស់ទុំ) · ខ្ពស់ = ទី 1 (ក្មេងជាង)
                    grp = sorted(r["hz"] for r in rows if r.get("gender") == g and r.get("hz"))
                    if len(grp) < 6 or grp[int(len(grp) * 0.85)] - grp[int(len(grp) * 0.15)] < 22:
                        continue  # សំឡេងស្រដៀងគ្នា → តួអង្គតែមួយ
                    cut = grp[len(grp) // 2]
                    for r in rows:
                        if r.get("gender") == g and r.get("hz"):
                            r["var"] = 2 if r["hz"] < cut else 1
            self._mark_dirty()
            self._fill()
            self._next()
        self._thread({"detect": "Detect Speakers", "mwk": "Man / woman / kid", "match": "Match voices"}[mode], work, done)

    # ================= ⚙ ការកំណត់ · 🌫 Blur =================
    def open_settings(self):
        dlg = SettingsDialog(self, self.cfg, lambda: self.host._nav_go("settings"))
        if dlg.exec_() != QDialog.Accepted:
            return
        new = dlg.values()
        if new["orig_vol"] != self.cfg["orig_vol"]:
            self.orig_vol.setValue(new["orig_vol"])
        if new["dub_vol"] != self.cfg["dub_vol"]:
            self.volume.setValue(new["dub_vol"])
        self.cfg.update(new)
        for k, v in self.cfg.items():
            self.store.setValue(f"studio/{k}", v)
        self._mark_dirty() if self.rows else None  # ល្បឿន / គុណភាពសំឡេងអាចប្តូរ
        self.say("⚙ បានរក្សាទុកការកំណត់")

    def _blur_label(self):
        n = len(self.blur_rects)
        self.btn_blur.setChecked(bool(n))
        self.btn_blur.setText(f"🌫 Blur text ✓ {n}" if n else "🌫 Blur text")

    def _frame(self):
        """ស៊ុមវីដេអូត្រង់ទីតាំងបច្ចុប្បន្ន → QPixmap (ឬ None)"""
        src = self.parts[self.part_i][0] if self.parts else self.video
        out = os.path.join(self.tmp, "blur_frame.jpg")
        for at in (self.player.position() / 1000, 0):
            try:
                subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.2f}", "-i", src,
                                "-frames:v", "1", "-q:v", "3", out], capture_output=True, creationflags=whisper_srt._NO_WINDOW, timeout=30)
            except (OSError, subprocess.SubprocessError):
                continue
            pix = QPixmap(out)
            if not pix.isNull():
                return pix
        return None

    def pick_blur(self, *_):
        self._blur_label()  # កុំឱ្យការចុចប្តូរស្ថានភាពប៊ូតុងដោយខ្លួនឯង
        if not self.video or self.busy:
            return
        pix = self._frame()
        if pix is None:
            return self.say("⚠ អានរូបពីវីដេអូមិនបាន (ឯកសារសំឡេង?)")
        dlg = BlurDialog(self, pix, self.blur_rects, self.cfg["blur_sigma"])
        if dlg.exec_() != QDialog.Accepted:
            return
        self.blur_rects = list(dlg.canvas.rects)
        self.cfg["blur_sigma"] = dlg.sigma.value()
        self.store.setValue("studio/blur_sigma", self.cfg["blur_sigma"])
        self._blur_label()
        self.say(f"🌫 Blur {len(self.blur_rects)} តំបន់ — នឹងព្រិលក្នុងវីដេអូ Export (Export យឺតជាងបន្តិច ព្រោះ encode ឡើងវិញ)"
                 if self.blur_rects else "🌫 Blur បិទ")

    # ================= Remove vocal =================
    def _novocal_toggled(self, on):
        """បើក → បំបែកសំឡេងនិយាយចេញពីភ្លេង (ម្តងក្នុងមួយវីដេអូ) · បិទ → Export ប្រើសំឡេងដើមវិញ"""
        if not on:
            if self._vol_before is not None:
                self.orig_vol.setValue(self._vol_before)
                self._vol_before = None
            return self.say("🎤 Remove vocal បិទ — Export ប្រើសំឡេងដើម (កម្រិត BG)")
        if self.bg_file and os.path.isfile(self.bg_file):
            return self._novocal_ready()
        self.remove_vocal()

    def _novocal_ready(self):
        self.btn_novocal.setChecked(True)
        self.btn_novocal.setText("🎤 Vocal removed ✓")
        if self._vol_before is None:  # ភ្លេងគ្មានការនិយាយ → ឮពេញ (មិនមែន 15% ដូចសំឡេងដើម)
            self._vol_before = self.orig_vol.value()
            self.orig_vol.setValue(100)
        self._audio = None  # លើកក្រោយអានពីសំឡេងនិយាយសុទ្ធ

    def remove_vocal(self):
        if not self.video or self.busy:
            self.btn_novocal.setChecked(bool(self.bg_file))
            return
        if not self._ensure(vocal_remove.installed(), vocal_remove.install, "Demucs (លុបការនិយាយ)", self.remove_vocal):
            if not self.busy:
                self.btn_novocal.setChecked(False)
            return
        voxcpm_tts.stop()          # ដោះ VRAM ឱ្យ Demucs
        funasr_srt.stop_servers()
        bg = os.path.join(self.tmp, f"novocal_{int(time.time())}.flac")
        self.say("🎤 កំពុងលុបសំឡេងនិយាយ...")

        def tick(j):
            if j["status"] == "download":
                self.say(f"⬇ ទាញយក model បំបែកសំឡេង (ម្តងគត់) {j['done']}%")
            elif j["status"] == "running":
                self.say(f"🎤 កំពុងលុបសំឡេងនិយាយ {100 * j['done'] // max(j['total'], 1)}%"
                         f" ({'GPU' if 'cuda' in (j.get('device') or '') else 'CPU'})")
            else:
                self.say("⏳ កំពុងបើក model បំបែកសំឡេង...")

        def done(j):
            self.bg_file, self.vocals_file = bg, os.path.splitext(bg)[0] + "_vocals.wav"
            self._novocal_ready()
            self._set_busy("")
            self.say(f"<span style='color:{P['green']}'>✓</span> លុបសំឡេងនិយាយរួច — Export នឹងប្រើភ្លេងគ្មានការនិយាយ (BG {self.orig_vol.value()}%)")
            if self.chain:
                self._next()
        self._thread("Remove vocal", lambda: vocal_remove.start_job(self._listen(True), bg, keep_vocals=True),
                     lambda job: self._poll("Remove vocal", job, tick, done))

    def _fail_novocal(self):
        self.btn_novocal.setChecked(bool(self.bg_file and os.path.isfile(self.bg_file)))

    def _tts_changed(self, *_):
        if self.tts.currentData() != "voxcpm" and self.btn_clone.isChecked():
            self.btn_clone.setChecked(False)
        self._mark_dirty()

    def _clone_toggled(self, on):
        if on and self.tts.currentData() != "voxcpm":
            self.tts.setCurrentIndex(self.tts.findData("voxcpm"))  # ចម្លងសំឡេង ត្រូវការ VoxCPM2
        self._mark_dirty()
        self.say("🧬 Clone voices បើក — បន្ទាត់នីមួយៗនឹងប្រើសំឡេងតួអង្គដើមពីវីដេអូ (ចុច Generate Voice)" if on
                 else "🧬 Clone voices បិទ — ប្រើសំឡេង preset")

    def _make_refs(self):
        """ឯកសារយោងសម្រាប់ចម្លងសំឡេង: សំឡេងដើមរបស់បន្ទាត់នីមួយៗ (≥1.8វិ) · ខ្លីពេក → បន្ទាត់វែងជិតបំផុតរបស់តួអង្គដដែល
        → {row: wav} · ហៅក្នុង thread"""
        import numpy as np
        import soundfile as sf
        audio = self._load_audio()
        folder = os.path.join(self.tmp, "refs")
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder)
        rows, refs = self.rows, {}
        dur = [r["end"] - r["start"] for r in rows]
        for k, r in enumerate(rows):
            src = k
            if dur[k] < 1800:
                near = [j for j in range(max(0, k - 8), min(len(rows), k + 9))
                        if dur[j] >= 1800 and (rows[j].get("gender"), rows[j].get("var") or 1) == (r.get("gender"), r.get("var") or 1)]
                if near:
                    src = min(near, key=lambda j: (abs(j - k), -dur[j]))
                elif dur[k] < 800:
                    continue  # ខ្លីពេក គ្មានបន្ទាត់ជំនួស → preset
            a = int(rows[src]["start"] * 16)
            b = int(min(rows[src]["end"], rows[src]["start"] + 10000) * 16)
            clip = audio[max(0, a):b]
            if len(clip) < 8000 or float(np.abs(clip).max()) < 0.02:
                continue
            path = os.path.join(folder, f"r{src}.wav")
            if not os.path.exists(path):
                sf.write(path, clip / max(float(np.abs(clip).max()), 0.25) * 0.9, 16000, subtype="PCM_16")
            refs[k] = path
        return refs

    def _dub_args(self, refs=None):
        vox = self.tts.currentData() == "voxcpm"
        p = self.host.voice_settings()
        p.update(engine="edge", voice=self.female.currentData(), auto_gender=True, api_key="",
                 voice_female=self.female.currentData(), voice_male=self.male.currentData(),
                 rate=self.rate.value() - 100, pitch=self.pitch.value(), volume=0,
                 fit=bool(self.fit.currentData()), max_speed=float(self.cfg["max_speed"]), format="wav",
                 workers=min(max(self.host.workers.value(), 48), int(self.cfg["edge_parallel"])))
        srt_dub.EDGE_MAX_PARALLEL = int(self.cfg["edge_parallel"])
        cues = []
        for k, r in enumerate(self.rows):
            if not r["text"].strip():
                continue
            g = r.get("gender")
            cues.append({"index": len(cues) + 1, "start": r["start"], "end": r["end"], "text": r["text"], "row": k,
                         "gender": "female" if g == "kid" else g, "vid": voice_id(r),
                         "pitch_add": 0 if vox else EDGE_PITCH.get((g, r.get("var") or 1), 0)})
        args = self.backend._dub_setup(dict(p, cues=cues))
        if vox:  # VoxCPM2: សំឡេង preset តាមតួអង្គ ឬចម្លងពីសំឡេងដើម · GPU ម្តងមួយបន្ទាត់
            refs = refs or {}
            for c in args["cues"]:
                c["voice"] = f"clone:{refs[c['row']]}" if c["row"] in refs else f"preset:{c['vid']}"
            args.update(engine="voxcpm", workers=1,
                        opts={"voice": "preset:female1", "synth": voxcpm_tts.synth, "steps": int(self.cfg["vox_steps"]),
                              "cfg": float(self.cfg["vox_cfg"]), "tmp": self.tmp})
        return args

    def generate(self, refs=None):
        if not self.rows or self.busy:
            return
        vox = self.tts.currentData() == "voxcpm"
        if vox:
            if not self._ensure(voxcpm_tts.installed(), voxcpm_tts.install, "VoxCPM2", self.generate):
                return
            if not voxcpm_tts.model_ready() and QMessageBox.question(
                    self, "VoxCPM2", "Model VoxCPM2 (~4.7 GB) នឹងទាញយកម្តងគត់ពេលចាប់ផ្តើម។\n\nបន្ត?") != QMessageBox.Yes:
                self.chain = []
                return
            funasr_srt.stop_servers()  # ដោះ VRAM ឱ្យ VoxCPM2 (~6 GB)
            if self.btn_clone.isChecked() and refs is None:
                if not self.video:
                    return self._fail("Clone voices", "ត្រូវការវីដេអូដើម")
                self.say("🧬 កំពុងរៀបចំសំឡេងយោងរបស់តួអង្គ...")

                def got(found):
                    self._set_busy("")
                    self.generate(found)
                return self._thread("Clone voices", self._make_refs, got)
        try:
            args = self._dub_args(refs)
        except Exception as e:  # noqa: BLE001
            return self._fail("Generate Voice", e)
        box = {}
        cloned = sum(str(c.get("voice", "")).startswith("clone:") for c in args["cues"]) if vox else 0

        def post(job, pcm):
            box["pcm"] = pcm
            box["file"] = srt_dub.write_output(pcm, self.tmp, f"dub_{int(time.time())}", "wav")
        job = srt_dub.start_job(**args, post=post)
        total = len(args["cues"])
        self.say(f"🔊 កំពុងបង្កើតសំឡេង 0/{total}")

        def tick(j):
            if j["status"] != "running":
                return self.say("🔊 កំពុងតម្រឹមពេលវេលា...")
            if vox and not j["done"] and voxcpm_tts.state() != "ready":
                return self.say("⏳ កំពុងបើក VoxCPM2 លើ GPU..." if voxcpm_tts.model_ready()
                                else f"⬇ ទាញយក VoxCPM2 (ម្តងគត់) {voxcpm_tts.download_pct()}%")
            self.say(f"🔊 {'VoxCPM2' if vox else 'Edge TTS'}: {j['done']}/{j['total']}" + (f" · 🧬 ចម្លងសំឡេង {cloned} បន្ទាត់" if cloned else "")
                     + (f" · ស្របគ្នា {j['parallel']}" if j.get("parallel") and not vox else "")
                     + (f" · សាកម្តងទៀត {j['retrying']} បន្ទាត់" if j.get("retrying") else ""))

        def done(j):
            self.pcm, self.pcm_cues, self.dirty = box.get("pcm"), args["cues"], False
            self.dub_file = os.path.join(self.tmp, box["file"])
            voiced = {c.get("row") for c in args["cues"]}
            silent = " ".join(j.get("warnings", []))
            for k, r in enumerate(self.rows):
                r["state"] = "ready" if k in voiced else "retry" if r["text"].strip() else ""
            for c in args["cues"]:  # បន្ទាត់ដែល engine ទុកស្ងាត់ → Needs a retry
                if f"បន្ទាត់ទី {c['index']} " in silent or f"បន្ទាត់ទី {c['index']}:" in silent:
                    self.rows[c["row"]]["state"] = "retry"
            self._fill()
            self.dub_player.setMedia(QMediaContent(QUrl.fromLocalFile(self.dub_file)))
            self.btn_dub_mon.setChecked(True)
            self._monitor_changed()
            warn = [w for w in j.get("warnings", []) if "ត្រូវបានពន្យារ" not in w]
            self.lbl_status.setToolTip("\n".join(warn))
            self._next()
            if warn and not self.busy:
                self.say(f"<span style='color:{P['amber']}'>●</span> សំឡេងរួច · {len(warn)} ការព្រមាន (hover មើល)")
        self._poll("Generate Voice", job, tick, done)

    def export(self):
        """ដាក់ dub ចូលវីដេអូ → Output/<រឿង>_dub_<ពេល>/ · សំឡេងមិនទាន់បង្កើត/ប្តូរ → Generate សិន"""
        if not self.rows or not self.video or self.busy:
            return
        if self.pcm is None or self.dirty:
            self.chain = ["export"]
            return self.generate()
        name = f"{self.backend.safe_name(self.title) or 'video'}_dub_{time.strftime('%Y%m%d_%H%M%S')}"
        job = {"status": "video", "stage_pct": 0, "warnings": []}
        vol, gain, cfg = self.orig_vol.value() / 100, self.volume.value() / 100, dict(self.cfg)
        blur = {"rects": list(self.blur_rects), "sigma": cfg["blur_sigma"]} if self.blur_rects else None
        bg = self.bg_file if self.btn_novocal.isChecked() and self.bg_file and os.path.isfile(self.bg_file) else None
        watch = QTimer(self)
        watch.timeout.connect(lambda: self.say(f"🧩 Merge {len(self.parts)} Part... {job['merging']}%" if "merging" in job
                                               else f"⬆ កំពុងដាក់សំឡេងចូលវីដេអូ {job.get('stage_pct', 0)}%"))
        watch.start(400)

        def work():
            pcm = self.pcm
            if gain != 1:  # កម្រិតសំឡេង dub
                import numpy as np
                pcm = np.clip(pcm.astype(np.float32) * gain, -32768, 32767).astype(np.int16)
            folder, parts = video_dub.mix_and_split(self._full(job), pcm, self.pcm_cues, self.output_dir, name, vol,
                                                    int(cfg["part_min"]) * 60, job, bg_audio=bg, duck=int(cfg["duck"]), blur=blur)
            return folder, parts

        def done(res):
            watch.stop()
            folder, parts = res
            self.result = os.path.join(self.output_dir, parts[0]["path"]) if parts else os.path.join(self.output_dir, folder)
            self._set_busy("")
            self.say(f"<span style='color:{P['green']}'>✓</span> Export រួច → {os.path.basename(self.result)}")
            self.lbl_status.setToolTip(self.result)

        def run():
            try:
                res = work()
            except Exception as e:  # noqa: BLE001
                return self.ui.emit(lambda e=e: (watch.stop(), self._fail("Export", e)))
            self.ui.emit(lambda: done(res))
        self._set_busy("export")
        threading.Thread(target=run, daemon=True).start()

    def _show_result(self):
        path = getattr(self, "result", "")
        if path and os.path.exists(path):
            os.startfile(path)
        else:
            self.say("មិនទាន់មានលទ្ធផល — ចុច Export This សិន")

    def auto_dub(self):
        """Transcribe → (បកប្រែ) → Detect Speakers → Generate Voice"""
        if not self.video or self.busy:
            return
        steps = []
        if not self.rows:
            steps.append("transcribe")
        if self.engine() != "gemini" or self.rows:
            if not self.rows or not all(r.get("km") for r in self.rows):
                steps.append("translate")
        steps += ["detect", "generate"]
        if self.rows and "translate" in steps and auto_pipeline._KHMER.search("".join(r["text"] for r in self.rows[:30])):
            steps.remove("translate")  # SRT ខ្មែរនាំចូល → មិនបកប្រែម្តងទៀត
        self.chain = steps[1:]
        getattr(self, steps[0])()

    # ================= RECAP =================
    def recap(self):
        """សង្ខេបរឿង: AI ជ្រើសឈុតសំខាន់ៗ + សរសេរអត្ថបទអ្នកនិទានរឿង (ខ្មែរ) → វីដេអូខ្លី បើកជា project ថ្មី"""
        if not self.rows or not self.video or self.busy:
            return
        key, model = self._key_model()
        if not key:
            return self.say("⚠ RECAP ត្រូវការ key AI (⚙ ការកំណត់) ឬបើក Gemini Router Direct")
        total_s = max(self._gdur() / 1000, self.rows[-1]["end"] / 1000)
        mins, ok = QInputDialog.getInt(self, "សង្ខេបរឿង RECAP", f"រឿងនេះវែង {_fmt(total_s * 1000)} — RECAP ប៉ុន្មាននាទី?",
                                       max(1, min(3, int(total_s / 300) or 1)), 1, 20)
        if not ok:
            return
        target = min(mins * 60, total_s * 0.8)
        rows, title, folder = list(self.rows), self.title, getattr(self, "folder", "")
        self.say("🎞 RECAP: AI កំពុងអានរឿង ហើយជ្រើសឈុតសំខាន់ៗ...")

        def work():
            listing = "\n".join(f"{k}|{_fmt(r['start'])}|{(r['end'] - r['start']) / 1000:.1f}s|{' '.join((r['text'] or r.get('original') or '').split())}"
                                 for k, r in enumerate(rows))
            n = max(4, min(40, int(target / 8)))
            prompt = (
                f"Below are the {len(rows)} dialogue lines of a drama as 'line number|start time|duration|text'. "
                f"Make a recap of about {int(target)} seconds. Choose about {n} key moments in chronological order that together "
                "tell the whole story (setup, main conflict, turning points, climax, ending). For each moment give 'from' and "
                "'to' = first and last line number of a short consecutive range (about 4 to 14 seconds of video), and 'text' = "
                "narration in Khmer, told in third person by a storyteller, that explains what happens in that moment and connects "
                "it to the story. The narration is read aloud over that clip, so its length must fit the clip: about 12 Khmer "
                "characters per second of the range. Write natural spoken Khmer. Do not overlap ranges.\n\n" + listing)
            schema = {"type": "OBJECT", "properties": {"segments": {"type": "ARRAY", "items": {
                "type": "OBJECT", "properties": {"from": {"type": "INTEGER"}, "to": {"type": "INTEGER"}, "text": {"type": "STRING"}},
                "required": ["from", "to", "text"]}}}, "required": ["segments"]}
            data = transcribe._ask_json([{"text": prompt}], schema, key, model, self.backend.gemini_post)
            segs, last = [], -1
            for sg in sorted((data or {}).get("segments") or [], key=lambda x: int(x.get("from", 0))):
                a, b = int(sg.get("from", -1)), int(sg.get("to", -1))
                text = " ".join(str(sg.get("text") or "").split())
                if not text or a <= last or a >= len(rows):
                    continue
                b = min(max(b, a), len(rows) - 1)
                start = max(rows[a]["start"] / 1000 - 0.25, 0)
                end = min(rows[b]["end"] / 1000 + 0.35, start + 25)
                if end - start < 1.5:
                    end = start + 1.5
                segs.append((start, end, text))
                last = b
            if len(segs) < 2:
                raise RuntimeError("AI មិនបានផ្តល់ឈុតគ្រប់គ្រាន់ — សាកម្តងទៀត ឬប្តូរ model")
            video = self._full()
            out_dir = tempfile.mkdtemp(prefix="recap_", dir=self.tmp)
            parts = []
            for i, (start, end, _) in enumerate(segs):
                if self.cancel.is_set():
                    raise RuntimeError("បានបញ្ឈប់")
                self.ui.emit(lambda i=i: self.say(f"🎞 RECAP: កំពុងកាត់ឈុត {i + 1}/{len(segs)}"))
                part = os.path.join(out_dir, f"c{i:03d}.mp4")
                video_dub.trim_video(video, start, end, part, True)
                parts.append(part)
            self.ui.emit(lambda: self.say("🎞 RECAP: កំពុងភ្ជាប់ឈុត..."))
            job = srt_dub.jobs[video_merge.start_job(parts, out_dir, "recap", False, [], faststart=False)]
            while job["status"] not in ("done", "error"):
                time.sleep(0.2)
            if job["status"] == "error":
                raise RuntimeError(job["error"])
            new_rows, cursor = [], 0.0
            for part, (_, _, text) in zip(parts, segs):
                d = video_dub.probe(part)[0]
                new_rows.append({"start": int(cursor * 1000 + 120), "end": int((cursor + d) * 1000 - 120), "text": text, "original": "",
                                 "gender": "male", "var": 2, "state": "", "km": True})
                cursor += d
            return os.path.join(out_dir, job["file"]), new_rows

        def done(res):
            path, new_rows = res
            self._opened(path, f"{title}_recap", folder)
            self.rows = new_rows
            self.orig_vol.setValue(20)
            self._mark_dirty()
            self._fill()
            self._refresh_state()
            self.say(f"🎞 RECAP រួច: {len(new_rows)} ឈុត · {_fmt(new_rows[-1]['end'])} — ពិនិត្យអត្ថបទ រួចចុច Generate Voice → Export")
        self._thread("RECAP", work, done)

    def shutdown(self):
        voxcpm_tts.stop()
        self.player.stop()
        self.dub_player.stop()
        self.player.setMedia(QMediaContent())
        self.dub_player.setMedia(QMediaContent())
        shutil.rmtree(self.tmp, ignore_errors=True)
