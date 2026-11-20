"""✨ Simple mode — ផ្ទាំងតែមួយ: ទម្លាក់ Folder រឿង → ចុចចាប់ផ្តើម → វីដេអូខ្មែរ។
Merge → SRT (SenseVoice) → បកប្រែខ្មែរ (Bayon/Gemini) → សំឡេង Edge TTS → វីដេអូ (auto_pipeline.py)"""
import os
import time

from PyQt5.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

import auto_pipeline
import theme
import vector_fx
from video_dub import DUCK_LABELS

SPIN = "◐◓◑◒"
LANGS = [("zh", "中文 ចិន"), ("", "🔍 រកឃើញខ្លួនឯង"), ("en", "English"), ("yue", "粵語 កាតាំង"), ("ja", "日本語 ជប៉ុន"),
         ("ko", "한국어 កូរ៉េ")]
VOICES = [("km-KH-SreymomNeural", "👩 ស្រី — Sreymom"), ("km-KH-PisethNeural", "👨 ប្រុស — Piseth")]


def fmt_dur(sec):
    sec = int(max(sec, 0))
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def _fade_in(widget, ms=320):
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))  # កុំទុក effect (ធ្វើឱ្យយឺត/ព្រិល)
    anim.start()


class SmoothBar(QProgressBar):
    """Progress bar ដែលរំកិលទន់ (animation) ជំនួសការលោត"""

    def __init__(self, height=10):
        super().__init__()
        self.setRange(0, 1000)
        self.setTextVisible(False)
        self.setFixedHeight(height)
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(450)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def set_pct(self, pct):
        target = int(max(0, min(100, pct)) * 10)
        if target == self._anim.endValue() or target == self.value():
            return
        self._anim.stop()
        self._anim.setStartValue(self.value())
        self._anim.setEndValue(target)
        self._anim.start()


class _Notice(QLabel):
    """QLabel ដែលប្រាប់ពេលអក្សរប្តូរ — បន្ទះសៀគ្វីបង្ហាញសារដដែល (ការព្រមាន ការដំឡើង...)"""

    def __init__(self, text, on_change):
        super().__init__(text)
        self._on_change = on_change

    def setText(self, text):
        super().setText(text)
        self._on_change()


class StepChips(QWidget):
    """🧩 Merge → 📝 SRT → 🌐 ខ្មែរ → 🔊 សំឡេង → 🎬 វីដេអូ — ជំហានកំពុងធ្វើភ្លឺបញ្ចេញបញ្ចូល"""

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.chips = {}
        self.arrows, self.traces = [], {}  # Vector: ខ្សែសៀគ្វីជំនួស "→"
        for n, (key, label) in enumerate(auto_pipeline.STAGES):
            if n:
                arrow = QLabel("→")
                arrow.setObjectName("simpleArrow")
                lay.addWidget(arrow)
                self.arrows.append(arrow)
                self.traces[key] = vector_fx.TraceArrow()
                lay.addWidget(self.traces[key])
            chip = QLabel(label)
            chip.setObjectName("chip")
            chip.setAlignment(Qt.AlignCenter)
            self.chips[key] = chip
            lay.addWidget(chip)
        lay.addStretch()
        self.active = None
        self._glow = 0.0
        self._pulse = QVariantAnimation(self)
        self._pulse.setStartValue(0.0)
        self._pulse.setKeyValueAt(0.5, 1.0)
        self._pulse.setEndValue(0.0)
        self._pulse.setDuration(1400)
        self._pulse.setLoopCount(-1)
        self._pulse.valueChanged.connect(self._on_pulse)
        self._flow = QTimer(self)
        self._flow.setInterval(70)
        self._flow.timeout.connect(lambda: [t.advance() for t in self.traces.values()])
        self.sync_skin()

    def sync_skin(self):
        vector = theme.mode == "vector"
        for a in self.arrows:
            a.setVisible(not vector)
        for t in self.traces.values():
            t.setVisible(vector)
        self._sync_flow()

    def _sync_flow(self):
        on = theme.mode == "vector" and self.active is not None
        if on and not self._flow.isActive():
            self._flow.start()
        elif not on:
            self._flow.stop()

    def set_stage(self, stage, finished=False):
        order = [k for k, _ in auto_pipeline.STAGES]
        idx = order.index(stage) if stage in order else -1
        for n, key in enumerate(order):
            state = "done" if finished or n < idx else "active" if n == idx else "todo"
            chip = self.chips[key]
            if chip.property("state") != state:
                chip.setProperty("state", state)
                chip.setStyleSheet("")
                chip.style().unpolish(chip)
                chip.style().polish(chip)
            if key in self.traces:  # ខ្សែនៅមុខជំហាន = សញ្ញាហូរចូលជំហាននោះ
                self.traces[key].set_state({"done": "done", "active": "flow"}.get(state, "todo"))
        self.active = None if finished else (stage if idx >= 0 else None)
        if self.active and self._pulse.state() != QVariantAnimation.Running:
            self._pulse.start()
        elif not self.active:
            self._pulse.stop()
        self._sync_flow()

    def _on_pulse(self, v):
        if not self.active:
            return
        c = theme.C
        glow = QColor(c["accent"])
        glow.setAlphaF(0.25 + 0.75 * float(v))
        self.chips[self.active].setStyleSheet(
            f"border: 2px solid rgba({glow.red()},{glow.green()},{glow.blue()},{glow.alphaF():.2f});")


class StoryCard(QFrame):
    remove = pyqtSignal(object)

    def __init__(self, story):
        super().__init__()
        self.story = story
        self.setObjectName("storyCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self.led = QLabel("●")  # Vector: ភ្លើង LED ជំនួសរូប emoji
        self.led.setObjectName("led")
        top.addWidget(self.led)
        self.icon = QLabel("🎬")
        self.icon.setObjectName("storyIcon")
        self.name = QLabel(os.path.basename(story["folder"]))
        self.name.setObjectName("storyName")
        self.name.setToolTip(story["folder"])
        size = sum(os.path.getsize(v) for v in story["videos"] if os.path.exists(v))
        srt = (" · 📄 មាន SRT" if story["srts"] else "")
        self.info = QLabel(f"{len(story['videos'])} Part · {size / 1e9:.2f} GB{srt}" if size > 1e9 else
                           f"{len(story['videos'])} Part · {size / 1e6:.0f} MB{srt}")
        self.info.setObjectName("storyInfo")
        self.chip = QLabel("○ រង់ចាំ")
        self.chip.setObjectName("statusChip")
        self.btn_x = QPushButton("✕")
        self.btn_x.setObjectName("cardX")
        self.btn_x.setFixedSize(28, 28)
        self.btn_x.setCursor(Qt.PointingHandCursor)
        self.btn_x.clicked.connect(lambda: self.remove.emit(self))
        top.addWidget(self.icon)
        top.addWidget(self.name, 1)
        top.addWidget(self.info)
        top.addWidget(self.chip)
        top.addWidget(self.btn_x)
        lay.addLayout(top)
        self.bar = SmoothBar(6)
        self.bar.setObjectName("cardBar")
        lay.addWidget(self.bar)
        self.detail = QLabel("")
        self.detail.setObjectName("storyDetail")
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)
        self.out = None
        self.btn_open = QPushButton("📂 បើក")
        self.btn_open.setObjectName("cardOpen")
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.hide()
        self.btn_open.clicked.connect(lambda: self.out and os.startfile(self.out))
        top.insertWidget(5, self.btn_open)
        self._set_state("waiting")
        self.sync_skin()

    def sync_skin(self):
        vector = theme.mode == "vector"
        self.led.setVisible(vector)
        self.icon.setVisible(not vector)
        self.bar.setFixedHeight(11 if vector else 6)  # Vector: LCD មាន padding → ត្រូវការកម្ពស់សម្រាប់ tick

    def _set_state(self, state):
        if self.property("state") != state:
            self.setProperty("state", state)
            for w in (self, self.chip, self.led):
                w.setProperty("state", state)
                w.style().unpolish(w)
                w.style().polish(w)

    def update_from(self, st, tick):
        status = st["status"]
        self._set_state(status)
        stage_label = dict(auto_pipeline.STAGES).get(st["stage"], "")
        if status == "running":
            self.chip.setText(f"{SPIN[tick % 4]} {stage_label}")
            self.icon.setText("⚙️")
        elif status == "done":
            self.chip.setText("✓ រួចរាល់")
            self.icon.setText("✅")
        elif status == "failed":
            self.chip.setText("✕ បរាជ័យ")
            self.icon.setText("⚠️")
        else:
            self.chip.setText("○ រង់ចាំ" if not st["msg"] else "■ " + st["msg"])
            self.icon.setText("🎬")
        self.led.setText("○" if status == "running" and tick % 4 >= 2 else "●")  # LED ភ្លឹបភ្លែតពេលកំពុងធ្វើ
        self.bar.set_pct(100 if status == "done" else st["pct"])
        took = ""
        if st["t0"]:
            took = f" · ⏱ {fmt_dur((st['t1'] or time.time()) - st['t0'])}"
        text = st["msg"] + took if status in ("running", "done") else st["msg"] if status == "failed" else ""
        idle = time.time() - st.get("since", time.time())
        if status == "running" and idle > 20:  # គ្មានវឌ្ឍនភាពយូរ → បង្ហាញថានៅដំណើរការ (មិនមែនគាំង)
            text += f" · ⏳ រង់ចាំ {fmt_dur(idle)}"
        if st["notes"] and status == "done":
            text += "  ·  " + " · ".join(st["notes"][:2])
        self.detail.setText(text)
        self.detail.setVisible(bool(text))
        self.out = st.get("out")
        self.btn_open.setVisible(status == "done" and bool(self.out))
        self.btn_x.setVisible(status not in ("running",))


class DropZone(QFrame):
    dropped = pyqtSignal(list)
    clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(4)
        self.icon = icon = QLabel("📂")
        icon.setObjectName("dropIcon")
        icon.setAlignment(Qt.AlignCenter)
        title = QLabel("ទម្លាក់ Folder រឿងនៅទីនេះ")
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("ឬចុចដើម្បីជ្រើស · Folder មួយ = រឿងមួយ (Part ច្រើន) · ទម្លាក់ Folder មេ = Folder រងទាំងអស់")
        sub.setObjectName("dropSub")
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        for w in (icon, title, sub):
            lay.addWidget(w)

    def _hover(self, on):
        self.setProperty("hover", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._hover(True)

    def dragLeaveEvent(self, e):
        self._hover(False)

    def dropEvent(self, e):
        self._hover(False)
        self.dropped.emit([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


class SimplePage(QWidget):
    start = pyqtSignal()
    stop = pyqtSignal()
    nav = pyqtSignal()  # ទៅទំព័រ 🏷 Logo & Ads
    SIDE_W = 330

    def __init__(self, scan_folder):
        super().__init__()
        self.scan_folder = scan_folder  # MainWindow._scan_folder → (videos, srts)
        self.cards = []
        self.pipeline = None
        self._tick = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(12)

        # ---- ចំណងជើង + ជំហាន ----
        self.hero = hero = QFrame()
        hero.setObjectName("simpleHero")
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(22, 16, 22, 16)
        hl.setSpacing(8)
        # Vector: ជួរ LED + លេខស៊េរីម៉ាស៊ីន ពីលើចំណងជើង
        self.serial_row = QWidget()
        sr = QHBoxLayout(self.serial_row)
        sr.setContentsMargins(0, 0, 0, 0)
        self.leds = leds = QLabel("■ ■ ■")
        leds.setObjectName("leds")
        self.serial = QLabel("AI-TEAM/01 · THE DUB COMPUTER")
        self.serial.setObjectName("serial")
        self.serial_state = QLabel("● IDLE")
        self.serial_state.setObjectName("serial")
        sr.addWidget(leds)
        sr.addWidget(self.serial)
        sr.addStretch()
        sr.addWidget(self.serial_state)
        hl.addWidget(self.serial_row)
        self.title = QLabel("⚡ Auto — រឿងចិន → វីដេអូខ្មែរ ក្នុងមួយចុច")
        self.title.setObjectName("simpleTitle")
        hl.addWidget(self.title)
        self.steps = StepChips()
        hl.addWidget(self.steps)
        # Vector: បន្ទះសៀគ្វី (module ៤ → កុំព្យូទ័រ → វីដេអូ) ជំនួសជួរជំហាន
        self.board = vector_fx.ProcessBoard({k: lab.split()[0] for k, lab in auto_pipeline.STAGES},
                                            self._board_model)
        self.board.dropped.connect(self.add_paths)
        self.board.action.connect(self._board_action)
        self._who = None
        hl.addWidget(self.board, 1)
        outer.addWidget(hero)

        # ---- ខ្លួន: ឆ្វេង = រឿង · ស្តាំ = ការកំណត់ ----
        body = QHBoxLayout()
        body.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(10)
        self.drop = DropZone()
        self.drop.dropped.connect(self.add_paths)
        self.drop.clicked.connect(self._pick)
        left.addWidget(self.drop)
        row = QHBoxLayout()
        self.count = QLabel("មិនទាន់មានរឿង")
        self.count.setObjectName("simpleMuted")
        row.addWidget(self.count, 1)
        for text, fn in [("🏷 Logo && Ads ›", self.nav.emit), ("➕ Folder", self._pick),
                         ("➕ Folder មេ", self._pick_parent), ("សម្អាត", self.clear)]:
            b = QPushButton(text)
            b.setObjectName("ghost")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            row.addWidget(b)
            if text == "សម្អាត":
                self.btn_clear = b
        left.addLayout(row)
        self.list_box = QWidget()
        self.list_lay = QVBoxLayout(self.list_box)
        self.list_lay.setContentsMargins(0, 0, 6, 0)
        self.list_lay.setSpacing(8)
        self.list_lay.addStretch()
        scroll = QScrollArea()
        scroll.setObjectName("simpleScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.list_box)
        left.addWidget(scroll, 1)
        body.addLayout(left, 3)

        self.side = side = QFrame()
        side.setObjectName("simpleSide")
        side.setParent(self)  # ផ្ទាំងអណ្តែតរំកិលចេញពីស្តាំ — លាក់ រហូតដល់ចុច ⚙ ការកំណត់
        side.setFixedWidth(self.SIDE_W)
        side.hide()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(18, 16, 18, 16)
        sl.setSpacing(8)
        head_row = QHBoxLayout()
        head = QLabel("⚙️ ការកំណត់")
        head.setObjectName("sideHead")
        head_row.addWidget(head, 1)
        close = QPushButton("✕")
        close.setObjectName("cardX")
        close.setFixedSize(28, 28)
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(lambda: self.btn_settings.setChecked(False))
        head_row.addWidget(close)
        sl.addLayout(head_row)
        sl.addWidget(self._label("🔊 សំឡេងខ្មែរ (Edge TTS)"))
        self.voice = QComboBox()
        for key, label in VOICES:
            self.voice.addItem(label, key)
        sl.addWidget(self.voice)
        sl.addWidget(self._label("🎧 ភាសាក្នុងវីដេអូ"))
        self.lang = QComboBox()
        for key, label in LANGS:
            self.lang.addItem(label, key)
        sl.addWidget(self.lang)
        sl.addWidget(self._label("📝 Model ស្តាប់"))
        self.asr = QComboBox()
        self.asr.addItem("🎯 Whisper turbo · GPU (ណែនាំ)", "whisper")
        self.asr.addItem("⚡ SenseVoice (លឿនបំផុត)", "sensevoice")
        self.asr.setToolTip("Whisper turbo លើ GPU: ច្បាស់ជាង មិនបាត់ពាក្យខ្លីៗ (ណែនាំ)\n"
                            "SenseVoice: លឿនជាង ~3 ដង តែអាចខុសពាក្យខ្លះ")
        self.asr.currentIndexChanged.connect(lambda _: self._sync_engine_note())
        sl.addWidget(self.asr)
        sl.addWidget(self._label("✂ កាត់ជាផ្នែក"))
        self.part = QSpinBox()
        self.part.setRange(0, 120)
        self.part.setSuffix(" នាទី")
        self.part.setSpecialValueText("មិនកាត់")
        sl.addWidget(self.part)
        self.use_brand = QCheckBox("🏷 ដាក់ Logo && Ads (តាមការកំណត់)")
        sl.addWidget(self.use_brand)
        self.mute = QCheckBox("🔇 Mute សំឡេងដើម")
        sl.addWidget(self.mute)
        self.novocal = QCheckBox("🎤 លុបតែការនិយាយ (រក្សាភ្លេង && សូរ)")
        self.novocal.setToolTip("លុបសំឡេងនិយាយភាសាដើមចេញ — ភ្លេង និងសូរផ្សេងៗនៅដដែល ជាមួយសំឡេងខ្មែរ")
        self.novocal.toggled.connect(lambda on: self.mute.setEnabled(not on))
        sl.addWidget(self.novocal)
        duck_row = QHBoxLayout()
        duck_row.addWidget(QLabel("🔉 បន្ថយភ្លេងពេលនិយាយ"))
        self.duck = QComboBox()
        self.duck.addItems(DUCK_LABELS)
        self.duck.setToolTip("ភ្លេងចុះដោយស្វ័យប្រវត្តិពេលសំឡេងខ្មែរនិយាយ ហើយឡើងវិញពេលស្ងាត់")
        duck_row.addWidget(self.duck, 1)
        sl.addLayout(duck_row)
        sync = lambda: self.duck.setEnabled(self.novocal.isChecked() or not self.mute.isChecked())  # noqa: E731
        self.mute.toggled.connect(lambda _: sync())
        self.novocal.toggled.connect(lambda _: sync())
        sl.addWidget(self._label("🔑 Bayon / Gemini key (សម្រាប់បកប្រែ)"))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("bayon_sk_... ឬ AIza... (ច្រើន key បំបែកដោយ ,)")
        self.key.setToolTip("key ច្រើន → key មានបញ្ហា ប្តូរទៅ key បន្ទាប់ដោយស្វ័យប្រវត្តិ")
        sl.addWidget(self.key)
        self.key_state = QLabel("")
        self.key_state.setObjectName("simpleMuted")
        self.key_state.setWordWrap(True)
        sl.addWidget(self.key_state)
        sl.addStretch()
        self.engine_note = QLabel()
        self.engine_note.setObjectName("simpleMuted")
        sl.addWidget(self.engine_note)
        self._sync_engine_note()
        self.body_w = QWidget()
        self.body_w.setLayout(body)
        outer.addWidget(self.body_w, 1)

        # ---- បាតខាងក្រោម: progress សរុប + ប៊ូតុង ----
        self.foot = foot = QFrame()
        foot.setObjectName("simpleFoot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(18, 14, 18, 14)
        fl.setSpacing(14)
        prog = QVBoxLayout()
        prog.setSpacing(6)
        self.headline = _Notice("ទម្លាក់ Folder រឿង ហើយចុច ▶ ចាប់ផ្តើម", self._board_refresh)
        self.headline.setObjectName("simpleHeadline")
        self.bar = SmoothBar(14)
        self.bar.setObjectName("bigBar")
        self.sub = QLabel("")
        self.sub.setObjectName("simpleMuted")
        prog.addWidget(self.headline)
        prog.addWidget(self.bar)
        prog.addWidget(self.sub)
        fl.addLayout(prog, 1)
        self.btn_settings = QPushButton("⚙ ការកំណត់")
        self.btn_settings.setObjectName("ghost")
        self.btn_settings.setCheckable(True)
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.toggled.connect(self._toggle_settings)
        fl.addWidget(self.btn_settings)
        self.btn_open = QPushButton("📂 បើកថត Output")
        self.btn_open.setObjectName("ghost")
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.hide()
        fl.addWidget(self.btn_open)
        self.btn_stop = QPushButton("■ បញ្ឈប់")
        self.btn_stop.setObjectName("ghost")
        self.btn_stop.setCursor(Qt.PointingHandCursor)
        self.btn_stop.clicked.connect(self.stop.emit)
        self.btn_stop.hide()
        fl.addWidget(self.btn_stop)
        self.btn_go = QPushButton("▶  ចាប់ផ្តើម")
        self.btn_go.setObjectName("bigGo")
        self.btn_go.setCursor(Qt.PointingHandCursor)
        self.btn_go.setMinimumSize(190, 56)
        self.btn_go.clicked.connect(self.start.emit)
        fl.addWidget(self.btn_go)
        outer.addWidget(foot)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.receipt = vector_fx.Receipt(self)
        self.setAcceptDrops(True)
        self.restyle()

    def _side_geometry(self):
        bottom = self.height() - 6 if theme.mode == "vector" else self.foot.y() - 8
        return 6, max(200, bottom - 6)

    def _toggle_settings(self, on):
        """ផ្ទាំងការកំណត់អណ្តែត រំកិលចេញ/ចូលពីខាងស្តាំ"""
        y, h = self._side_geometry()
        self.side.setFixedHeight(h)
        shown, hidden = QPoint(self.width() - self.SIDE_W - 6, y), QPoint(self.width() + 4, y)
        if on:
            self.side.move(hidden)
            self.side.show()
            self.side.raise_()
        anim = QPropertyAnimation(self.side, b"pos", self)
        anim.setDuration(280)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(self.side.pos())
        anim.setEndValue(shown if on else hidden)
        if not on:
            anim.finished.connect(self.side.hide)
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def show_settings(self):
        self.btn_settings.setChecked(True)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.side.isVisible():
            y, h = self._side_geometry()
            self.side.setFixedHeight(h)
            self.side.move(self.width() - self.SIDE_W - 6, y)
        self._place_receipt()

    def _place_receipt(self):
        r = self.receipt
        if theme.mode == "vector":  # ចេញពីរន្ធក្រោមកុំព្យូទ័រ
            slot = self.board.slot_rect
            top = self.board.mapTo(self, QPoint(int(slot.center().x()), int(slot.top())))
            r.move(top.x() - r.width() // 2, top.y() + 2)
        else:  # ពីលើបាតខាងក្រោម នៅខាងស្តាំ
            r.move(self.foot.geometry().right() - 20 - r.width(), self.foot.y() - r.height() - 6)

    # ---------- បន្ទះសៀគ្វី (Vector) ----------
    def _board_model(self):
        p = self.pipeline
        if p and len(p.state) == len(self.cards):
            stories = [{"name": st["name"], "status": st["status"], "pct": st["pct"], "stage": st["stage"]}
                       for st in p.state]
        else:
            stories = [{"name": os.path.basename(c.story["folder"]), "status": "waiting", "pct": 0, "stage": None}
                       for c in self.cards]
        running = self.running()
        who = self._who if running else None
        lines = ([who["name"], who["msg"], self.sub.text()] if who else
                 [self.headline.text(), self.sub.text()])
        return {"stories": stories, "running": running, "finished": bool(p and p.finished),
                "stage": who["stage"] if who else None, "stage_pct": who.get("stage_pct", 0) if who else 0,
                "overall": p.overall() if p else 0, "lines": [t for t in lines if t],
                "any_done": any(s["status"] == "done" for s in stories),
                "can_start": self.btn_go.isEnabled(), "can_stop": self.btn_stop.isEnabled()}

    def _board_refresh(self):
        if theme.mode == "vector":
            self.board.refresh()

    def _board_action(self, name):
        if name.startswith("remove:"):
            i = int(name.split(":")[1])
            if i < len(self.cards):
                self._remove(self.cards[i])
            return
        {"pick": self._pick, "parent": self._pick_parent, "clear": self.clear,
         "start": self.start.emit, "stop": self.stop.emit, "open": self.btn_open.click,
         "settings": self.btn_settings.toggle, "nav": self.nav.emit}[name]()

    # ទម្លាក់ Folder បានគ្រប់កន្លែងលើផ្ទាំង (ប្រអប់ទម្លាក់លាក់ពេលមានរឿងហើយ)
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def paintEvent(self, e):
        if theme.mode == "vector":  # ផ្ទៃខាងក្រោយបន្ទះសៀគ្វីស្រាលៗ (ឃើញនៅចន្លោះទទេ)
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            vector_fx.paint_backdrop(p, self.rect())
        super().paintEvent(e)

    def boot(self, tries=0):
        """Vector: ចលនាបើកម៉ាស៊ីន (Linking... → ផ្ទាំងឡើងពីក្រោម)"""
        if theme.mode != "vector":
            return
        if not self.isVisible() or self.width() < 200:  # បង្អួចមិនទាន់បង្ហាញ → រង់ចាំបន្តិច
            if tries < 20:
                QTimer.singleShot(150, lambda: self.boot(tries + 1))
            return
        vector_fx.BootOverlay(self)

    @staticmethod
    def _label(text):
        lab = QLabel(text)
        lab.setObjectName("sideLabel")
        return lab

    # ---------- រឿង ----------
    def _pick(self):
        folder = QFileDialog.getExistingDirectory(self, "ជ្រើស Folder រឿង")
        if folder:
            self.add_paths([folder])

    def _pick_parent(self):
        parent = QFileDialog.getExistingDirectory(self, "ជ្រើស Folder មេ (ដែលមាន Folder រឿងនៅខាងក្នុង)")
        if parent:
            self.add_paths([parent])

    def add_paths(self, paths):
        """Folder រឿង → ជួរមួយ · Folder មេ (គ្មានវីដេអូផ្ទាល់) → Folder រងទាំងអស់ · វីដេអូ → Folder របស់វា"""
        if self.running():
            return
        folders = []
        for p in paths:
            p = os.path.normpath(p)
            if os.path.isfile(p):
                p = os.path.dirname(p)
            if not os.path.isdir(p):
                continue
            videos, _ = self.scan_folder(p)
            if videos:
                folders.append(p)
            else:
                subs = sorted((os.path.join(p, d) for d in os.listdir(p) if os.path.isdir(os.path.join(p, d))),
                              key=lambda s: [int(t) if t.isdigit() else t.lower()
                                             for t in __import__("re").split(r"(\d+)", os.path.basename(s))])
                folders += subs
        known = {c.story["folder"] for c in self.cards}
        added = 0
        for folder in dict.fromkeys(folders):
            if folder in known:
                continue
            videos, srts = self.scan_folder(folder)
            if not videos:
                continue
            card = StoryCard({"folder": folder, "videos": videos, "srts": srts})
            card.remove.connect(self._remove)
            self.list_lay.insertWidget(self.list_lay.count() - 1, card)
            _fade_in(card)
            self.cards.append(card)
            added += 1
        self._sync_count()
        if not added and paths:
            self.headline.setText("⚠ រកមិនឃើញ Folder ដែលមានវីដេអូ")

    def _remove(self, card):
        if self.running():
            return
        self.cards.remove(card)
        card.deleteLater()
        self._sync_count()

    def clear(self):
        if self.running():
            return
        for card in self.cards:
            card.deleteLater()
        self.cards = []
        self.pipeline = None
        self._sync_count()
        self.bar.set_pct(0)
        self.steps.set_stage(None)
        self.btn_open.hide()
        self.receipt.hide()

    def _sync_count(self):
        n = len(self.cards)
        parts = sum(len(c.story["videos"]) for c in self.cards)
        self.count.setText(f"{n} រឿង · {parts} Part" if n else "មិនទាន់មានរឿង")
        self.drop.setVisible(n == 0)
        if not self.running():
            self.headline.setText(f"✨ ត្រៀមរួច {n} រឿង — ចុច ▶ ចាប់ផ្តើម" if n else
                                  "ទម្លាក់ Folder រឿង ហើយចុច ▶ ចាប់ផ្តើម")
        self.btn_go.setEnabled(n > 0 and not self.running())
        self._sync_serial()
        self._board_refresh()

    def stories(self):
        return [c.story for c in self.cards]

    # ---------- ដំណើរការ ----------
    def running(self):
        return bool(self.pipeline and not self.pipeline.finished)

    def _sync_engine_note(self):
        asr = ("📝 Whisper turbo · GPU (ច្បាស់ មិនបាត់ពាក្យ)" if self.asr.currentData() == "whisper"
               else "📝 SenseVoice + VAD (លឿនបំផុត)")
        self.engine_note.setText(f"{asr}\n🌐 Gemini 3.8 Flash តាម Bayon\n🔊 Edge TTS ស្របគ្នា 48 បន្ទាត់\n"
                                 "🎬 ធ្វើម្តងមួយរឿង (មិនប្រជែង GPU)")

    def attach(self, pipeline):
        """ចាប់ផ្តើមបង្ហាញវឌ្ឍនភាពនៃ pipeline"""
        self.pipeline = pipeline
        self.btn_go.setEnabled(False)
        self.btn_go.setText("⏳  កំពុងធ្វើ...")
        self.btn_stop.show()
        self.btn_stop.setEnabled(True)
        self.btn_open.hide()
        self.btn_clear.setEnabled(False)
        self.receipt.hide()
        self.timer.start(250)
        self._refresh()

    def _refresh(self):
        p = self.pipeline
        if not p:
            return
        self._tick += 1
        self._sync_serial()
        for card, st in zip(self.cards, p.state):
            card.update_from(st, self._tick)
        running = [st for st in p.state if st["status"] == "running"]
        current = running[-1] if running else None  # រឿងក្រោយបង្អស់ (ជំហានដំបូង) — ឬរឿងតែមួយ
        dub = next((st for st in running if st["stage"] in ("tts", "video")), None)
        self.steps.set_stage((dub or current)["stage"] if (dub or current) else None, finished=p.finished)
        total = p.overall()
        self._who = dub or current
        self.bar.set_pct(total)
        done = sum(st["status"] == "done" for st in p.state)
        failed = sum(st["status"] == "failed" for st in p.state)
        elapsed = time.time() - p.t0
        if p.finished:
            self.timer.stop()
            self.btn_go.setText("▶  ចាប់ផ្តើម")
            self.btn_stop.hide()
            self.btn_clear.setEnabled(True)
            self._sync_count()
            ok = done == len(p.state)
            self.headline.setText((f"🎉 រួចរាល់ទាំង {done} រឿង!" if ok else f"ចប់ — ✓ {done} · ✕ {failed}") +
                                  f"  ⏱ {fmt_dur(elapsed)}")
            self.sub.setText("វីដេអូនៅក្នុងថត Output · SRT ខ្មែរនៅក្នុង Folder រឿងនីមួយៗ")
            self._board_refresh()
            self.btn_open.show()
            self.leds.setText("■ ■ ■")
            if theme.mode == "vector":
                self._place_receipt()
                self.receipt.print_out("Dub Complete" if ok else "Finished", [
                    f"✓ {done} រឿង" + (f" · ✕ {failed}" if failed else ""),
                    f"⏱ {fmt_dur(elapsed)}",
                    f"{sum(len(c.story['videos']) for c in self.cards)} Part → ថត Output"])
            return
        if theme.mode == "vector":  # LED ៣ ដុំរត់ជាជួរ ពេលកំពុងធ្វើ
            lit = self._tick % 3
            self.leds.setText(" ".join(f'<span style="color:{theme.C["accent"] if i == lit else theme.C["faint"]}">'
                                       "■</span>" for i in range(3)))
        who = dub or current
        self.headline.setText(f"{SPIN[self._tick % 4]}  {total}% · រឿង {done + 1}/{len(p.state)}"
                              + (f" — {who['name']}: {who['msg']}" if who else ""))
        eta = ""
        if total >= 3:
            eta = f" · នៅសល់ប្រហែល {fmt_dur(elapsed * (100 - total) / total)}"
        self.sub.setText(f"⏱ {fmt_dur(elapsed)}{eta}" + (f" · ✕ {failed}" if failed else ""))
        self._board_refresh()

    # ---------- រចនាប័ទ្ម ----------
    def _sync_serial(self):
        p = self.pipeline
        state = ("RUN" if self.running() else "DONE" if p and p.finished else "READY" if self.cards else "IDLE")
        self.serial_state.setText(f"● {state}")

    def restyle(self):
        c = theme.C
        grad = theme.GRAD.format(a=c["grad_a"], b=c["grad_b"])
        vector = theme.mode == "vector"
        self.serial_row.setVisible(vector)
        self.board.setVisible(vector)
        # Vector: បន្ទះសៀគ្វីពេញផ្ទាំង (ទម្លាក់ Folder · បញ្ជីរឿង · START ទាំងអស់នៅលើបន្ទះ)
        self.body_w.setVisible(not vector)
        self.foot.setVisible(not vector)
        self.layout().setStretchFactor(self.hero, 1 if vector else 0)
        self._board_refresh()
        self.title.setVisible(not vector)
        self.steps.setVisible(not vector)
        self.drop.icon.setVisible(not vector)  # Vector: ទុកកន្លែងឲ្យបន្ទះសៀគ្វី
        for card in self.cards:
            card.sync_skin()
        self.steps.sync_skin()
        self.leds.setText("■ ■ ■")
        if not vector:
            self.receipt.hide()
        for w in (self.headline, self.serial, self.serial_state):
            w.setFont(theme.mono(w.font()) if vector else self.font())
        self.setStyleSheet(theme.hackify(f"""
QFrame#simpleHero, QFrame#simpleSide, QFrame#simpleFoot {{ background: {c['surface']};
    border: 1px solid {c['border']}; border-radius: 18px; }}
QLabel#simpleTitle {{ font-size: 17pt; font-weight: bold; }}
QLabel#simpleArrow {{ color: {c['faint']}; }}
QLabel#chip {{ background: {c['card']}; color: {c['muted']}; border: 2px solid {c['border']}; border-radius: 13px;
    padding: 5px 12px; font-weight: bold; }}
QLabel#chip[state="active"] {{ background: {c['hover']}; color: {c['accent']}; }}
QLabel#chip[state="done"] {{ background: {c['card']}; color: {c['success']}; border-color: {c['success']}; }}
QFrame#dropZone {{ background: {c['card']}; border: 2px dashed {c['border_hi']}; border-radius: 18px; }}
QFrame#dropZone:hover, QFrame#dropZone[hover="true"] {{ border-color: {c['accent']}; background: {c['hover']}; }}
QLabel#dropIcon {{ font-size: 26pt; }}
QLabel#dropTitle {{ font-size: 13pt; font-weight: bold; }}
QLabel#dropSub, QLabel#simpleMuted, QLabel#storyInfo, QLabel#storyDetail, QLabel#sideLabel {{ color: {c['muted']}; }}
QLabel#sideHead {{ font-size: 12pt; font-weight: bold; }}
QScrollArea#simpleScroll, QScrollArea#simpleScroll > QWidget > QWidget {{ background: transparent; }}
QFrame#storyCard {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 14px; }}
QFrame#storyCard[state="running"] {{ border: 1px solid {c['accent']}; }}
QFrame#storyCard[state="done"] {{ border: 1px solid {c['success']}; }}
QFrame#storyCard[state="failed"] {{ border: 1px solid {c['error']}; }}
QLabel#storyIcon {{ font-size: 14pt; }}
QLabel#storyName {{ font-size: 11pt; font-weight: bold; }}
QLabel#statusChip {{ border-radius: 10px; padding: 3px 10px; background: {c['hover']}; color: {c['muted']};
    font-weight: bold; }}
QLabel#statusChip[state="running"] {{ color: {c['accent']}; }}
QLabel#statusChip[state="done"] {{ color: {c['success']}; }}
QLabel#statusChip[state="failed"] {{ color: {c['error']}; }}
QPushButton#cardX {{ background: transparent; border: none; color: {c['faint']}; padding: 0; }}
QPushButton#cardX:hover {{ color: {c['error']}; }}
QPushButton#cardOpen {{ background: transparent; border: 1px solid {c['border_hi']}; border-radius: 10px;
    padding: 3px 10px; }}
QProgressBar#cardBar, QProgressBar#bigBar {{ background: {c['input']}; border: none; border-radius: 4px; }}
QProgressBar#bigBar {{ border-radius: 7px; }}
QProgressBar#cardBar::chunk, QProgressBar#bigBar::chunk {{ background: {grad}; border-radius: 4px; }}
QProgressBar#bigBar::chunk {{ border-radius: 7px; }}
QLabel#simpleHeadline {{ font-size: 12pt; font-weight: bold; }}
QPushButton#bigGo {{ background: {grad}; color: {c['on_accent']}; border: none; border-radius: 16px;
    font-size: 14pt; font-weight: bold; padding: 8px 22px; }}
QPushButton#bigGo:hover {{ background: {theme.GRAD.format(a=QColor(c['grad_a']).lighter(110).name(),
                                                       b=QColor(c['grad_b']).lighter(112).name())}; }}
QPushButton#bigGo:disabled {{ background: {c['hover']}; color: {c['faint']}; }}
"""))
