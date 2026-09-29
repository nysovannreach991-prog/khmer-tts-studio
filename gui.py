"""Khmer TTS Studio — កម្មវិធី GUI (PyQt5)

ប្រើ engine ដដែលនឹងកំណែ web: edge-tts / Gemini TTS, SRT → សំឡេង/វីដេអូ,
សំឡេង → SRT (បកប្រែ + ចាប់ភេទ), Merge វីដេអូ, Mute វីដេអូ។
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

from PyQt5.QtCore import QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontDatabase
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSlider, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import app as backend
import srt_dub
import transcribe
import video_dub
import updater
import video_merge

OUTPUT_DIR = backend.OUTPUT_DIR
GENDER_KM = {"female": "ស្រី", "male": "ប្រុស"}
GENDER_COLOR = {"female": QColor("#b0417a"), "male": QColor("#2f6fb0")}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".ts"}
VIDEO_FILTER = "វីដេអូ (*.mp4 *.mkv *.mov *.avi *.webm *.flv *.m4v *.ts);;ឯកសារទាំងអស់ (*)"
MEDIA_FILTER = ("សំឡេង/វីដេអូ (*.mp3 *.wav *.m4a *.aac *.ogg *.flac *.mp4 *.mkv *.mov *.avi *.webm);;"
                "ឯកសារទាំងអស់ (*)")
FALLBACK_EDGE_VOICES = [
    {"name": "km-KH-SreymomNeural", "locale": "km-KH", "gender": "Female"},
    {"name": "km-KH-PisethNeural", "locale": "km-KH", "gender": "Male"},
]


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def fmt_ms(ms):
    s, ms = divmod(int(ms), 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_size(b):
    return f"{b / 1e9:.2f} GB" if b > 1e9 else f"{b / 1e6:.1f} MB"


def fmt_dur(sec):
    m, s = divmod(int(round(sec)), 60)
    return f"{m}:{s:02d}"


def open_path(path):
    if os.path.exists(path):
        os.startfile(path)


def reveal(path):
    """បើក Explorer ហើយជ្រើសឯកសារ"""
    if os.path.isfile(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif os.path.isdir(path):
        os.startfile(path)


def cues_to_srt(cues):
    blocks = []
    for i, c in enumerate(cues, 1):
        label = GENDER_KM.get(c.get("gender") or "")
        text = f"({label}) {c['text']}" if label else c["text"]
        blocks.append(f"{i}\n{fmt_ms(c['start'])} --> {fmt_ms(c['end'])}\n{text}\n")
    return "\n".join(blocks)


class Worker(QThread):
    """ដំណើរការមុខងារយឺតនៅ background ដើម្បីកុំឱ្យផ្ទាំងកម្មវិធីគាំង"""
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as e:  # noqa: BLE001 — បង្ហាញកំហុសទៅអ្នកប្រើ
            self.failed.emit(str(e) or type(e).__name__)


class FileListBox(QWidget):
    """បញ្ជីឯកសារដែលអាចបន្ថែម លុប និងអូសប្តូរលំដាប់ (សម្រាប់ Merge / Mute)"""

    def __init__(self, dialog_title, sort=True):
        super().__init__()
        self.dialog_title, self.sort = dialog_title, sort
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        buttons = QHBoxLayout()
        for text, fn in [("➕ បន្ថែម", self.add), ("↑", lambda: self.move(-1)), ("↓", lambda: self.move(1)),
                         ("✕ លុប", self.remove), ("តម្រៀបតាមឈ្មោះ", self.sort_items), ("សម្អាត", self.list.clear)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            buttons.addWidget(b)
        buttons.addStretch()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(buttons)
        lay.addWidget(self.list)

    def paths(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def add_paths(self, paths):
        existing = set(self.paths())
        for p in sorted(paths, key=lambda p: natural_key(os.path.basename(p))) if self.sort else paths:
            if p not in existing:
                self.list.addItem(os.path.basename(p))
                self.list.item(self.list.count() - 1).setData(Qt.UserRole, p)
                self.list.item(self.list.count() - 1).setToolTip(p)

    def add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, self.dialog_title, "", VIDEO_FILTER)
        self.add_paths(paths)

    def move(self, step):
        row = self.list.currentRow()
        if row < 0 or not 0 <= row + step < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(row + step, item)
        self.list.setCurrentRow(row + step)

    def remove(self):
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))

    def sort_items(self):
        paths = self.paths()
        self.list.clear()
        self.add_paths(paths)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        version = updater.local_version()
        self.setWindowTitle("Khmer TTS Studio" + (f"  v{version}" if version != "0" else ""))
        self.resize(1280, 820)
        self.settings = QSettings("KhmerTTS", "Studio")
        self.edge_voices = list(FALLBACK_EDGE_VOICES)
        self.cues = []
        self.last_result = None
        self.busy = False
        self.run_buttons = []
        self._workers = []
        self._job = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_job)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_text_tab(), "អត្ថបទ")
        self.tabs.addTab(self._build_srt_tab(), "SRT → វីដេអូ")
        self.tabs.addTab(self._build_stt_tab(), "សំឡេង → SRT")
        self.tabs.addTab(self._build_merge_tab(), "Merge")
        self.tabs.addTab(self._build_batch_tab(), "⚡ Auto")
        self.files_tab = self._build_files_tab()
        self.tabs.addTab(self.files_tab, "ឯកសារ")
        self.tabs.currentChanged.connect(
            lambda i: self.refresh_files() if self.tabs.widget(i) is self.files_tab else None)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self._build_voice_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([820, 440])

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addWidget(splitter, 1)
        lay.addLayout(self._build_status_bar())
        self.setCentralWidget(central)

        self._load_settings()
        self._update_engine()
        self.load_edge_voices()
        self._build_menu()
        QTimer.singleShot(3000, lambda: self.check_update(silent=True))  # ពិនិត្យ Update ស្ងាត់ៗ

    # ================= UI: voice settings (right) =================
    def _build_voice_panel(self):
        panel = QWidget()
        lay = QVBoxLayout(panel)

        engine_box = QGroupBox("Engine")
        el = QHBoxLayout(engine_box)
        self.rb_edge = QRadioButton("Edge TTS (ឥតគិតថ្លៃ)")
        self.rb_gemini = QRadioButton("Gemini TTS")
        self.rb_edge.setChecked(True)
        self.engine_group = QButtonGroup(self)
        for rb in (self.rb_edge, self.rb_gemini):
            self.engine_group.addButton(rb)
            el.addWidget(rb)
            rb.toggled.connect(self._update_engine)
        lay.addWidget(engine_box)

        # Edge
        self.edge_box = QGroupBox("Edge TTS")
        ef = QFormLayout(self.edge_box)
        self.edge_lang = QComboBox()
        self.edge_voice = QComboBox()
        self.edge_lang.currentIndexChanged.connect(self._fill_edge_voices)
        ef.addRow("ភាសា", self.edge_lang)
        ef.addRow("សំឡេង", self.edge_voice)
        self.rate = self._slider(-50, 100, "%")
        self.pitch = self._slider(-50, 50, "Hz")
        self.volume = self._slider(-50, 50, "%")
        ef.addRow("ល្បឿន", self.rate[0])
        ef.addRow("Pitch", self.pitch[0])
        ef.addRow("Volume", self.volume[0])
        lay.addWidget(self.edge_box)

        # Gemini
        self.gemini_box = QGroupBox("Gemini TTS")
        gf = QFormLayout(self.gemini_box)
        self.gemini_model = QComboBox()
        self.gemini_model.addItems(backend.GEMINI_MODELS)
        self.gemini_voice = QComboBox()
        for v in backend.GEMINI_VOICES:
            self.gemini_voice.addItem(f"{v['name']} ({GENDER_KM[v['gender'].lower()]})", v["name"])
        self.gemini_style = QLineEdit()
        self.gemini_style.setPlaceholderText("ឧ. Read warmly and slowly, like a storyteller")
        gf.addRow("Model", self.gemini_model)
        gf.addRow("សំឡេង", self.gemini_voice)
        gf.addRow("រចនាប័ទ្ម", self.gemini_style)
        lay.addWidget(self.gemini_box)

        # Auto gender
        gender_box = QGroupBox("សំឡេងស្រី/ប្រុស (SRT)")
        gl = QFormLayout(gender_box)
        self.auto_gender = QCheckBox("ជ្រើសដោយស្វ័យប្រវត្តិតាម (ស្រី)/(ប្រុស)")
        self.auto_gender.setChecked(True)
        self.voice_female = QComboBox()
        self.voice_male = QComboBox()
        self.auto_gender.toggled.connect(lambda on: (self.voice_female.setEnabled(on), self.voice_male.setEnabled(on)))
        gl.addRow(self.auto_gender)
        gl.addRow("សំឡេងស្រី", self.voice_female)
        gl.addRow("សំឡេងប្រុស", self.voice_male)
        # ចងចាំសំឡេងដែលបានជ្រើសភ្លាមៗ (ដាច់ដោយឡែកសម្រាប់ Edge និង Gemini)
        for gender, combo in (("female", self.voice_female), ("male", self.voice_male)):
            combo.currentIndexChanged.connect(
                lambda _, g=gender, c=combo: c.currentData() and self.settings.setValue(
                    f"voice_{g}_{self.engine()}", c.currentData()))
        self.edge_voice.currentIndexChanged.connect(
            lambda _: self.edge_voice.currentData() and self.settings.setValue("edge_voice", self.edge_voice.currentData()))
        lay.addWidget(gender_box)

        # Gemini key (TTS + សំឡេង → SRT)
        key_box = QGroupBox("Gemini API Key")
        kl = QHBoxLayout(key_box)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("ប្រើ GEMINI_API_KEY" if os.environ.get("GEMINI_API_KEY") else "AIza... / AQ...")
        show = QPushButton("👁")
        show.setFixedWidth(36)
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.api_key.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        kl.addWidget(self.api_key)
        kl.addWidget(show)
        lay.addWidget(key_box)

        self.strip_parens = QCheckBox("លុបអត្ថបទក្នុង ( ) — ឈ្មោះតួអង្គ")
        self.strip_parens.setChecked(True)
        lay.addWidget(self.strip_parens)
        lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(430)  # Kantumruy Pro ធំជាង Khmer UI
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        return scroll

    def _slider(self, lo, hi, unit):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        label = QLabel(f"0{unit}")
        label.setMinimumWidth(52)
        s.valueChanged.connect(lambda v: label.setText(f"{v:+d}{unit}"))
        h.addWidget(s)
        h.addWidget(label)
        return w, s

    # ================= UI: tabs =================
    def _run_button(self, text, fn):
        b = QPushButton(text)
        b.setMinimumHeight(38)
        b.setStyleSheet("QPushButton{background:#2f5d8a;color:white;font-weight:bold;border-radius:6px;padding:0 18px}"
                        "QPushButton:disabled{background:#9db0c4}")
        b.clicked.connect(fn)
        self.run_buttons.append(b)
        return b

    def _file_row(self, placeholder, dialog_filter, on_pick=None, save=False):
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        btn = QPushButton("ជ្រើស...")

        def pick():
            if save:
                path, _ = QFileDialog.getSaveFileName(self, placeholder, edit.text() or OUTPUT_DIR, dialog_filter)
            else:
                path, _ = QFileDialog.getOpenFileName(self, placeholder, os.path.dirname(edit.text()), dialog_filter)
            if path:
                edit.setText(path)
                if on_pick:
                    on_pick(path)
        btn.clicked.connect(pick)
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        row.addWidget(btn)
        return edit, row

    def _build_text_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("បញ្ចូលអត្ថបទរឿងនៅទីនេះ...")
        self.text_count = QLabel("0 តួអក្សរ")
        self.text_edit.textChanged.connect(
            lambda: self.text_count.setText(f"{len(self.text_edit.toPlainText())} តួអក្សរ"))
        lay.addWidget(self.text_edit, 1)
        row = QHBoxLayout()
        row.addWidget(self._run_button("🔊 បង្កើតសំឡេង", self.run_text))
        row.addStretch()
        row.addWidget(self.text_count)
        lay.addLayout(row)
        return w

    def _build_srt_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.srt_path, srt_row = self._file_row("ឯកសារ .srt", "SRT (*.srt);;ឯកសារទាំងអស់ (*)", self.load_srt_file)
        form.addRow("SRT", srt_row)
        self.video_path, video_row = self._file_row("វីដេអូ (ជាជម្រើស) — ដាក់សំឡេងចូល ហើយកាត់ជាផ្នែកៗ",
                                                    VIDEO_FILTER)
        clear_video = QPushButton("✕")
        clear_video.setFixedWidth(32)
        clear_video.clicked.connect(self.video_path.clear)
        video_row.addWidget(clear_video)
        form.addRow("វីដេអូ", video_row)
        lay.addLayout(form)

        self.cue_info = QLabel("ចុចពីរដងលើ 'ភេទ' ដើម្បីប្តូរ · អាចកែអត្ថបទក្នុងតារាងបាន")
        self.cue_info.setStyleSheet("color:#6b6b66")
        lay.addWidget(self.cue_info)
        self.cue_table = QTableWidget(0, 5)
        self.cue_table.setHorizontalHeaderLabels(["#", "ពេល", "ភេទ", "អត្ថបទ", "ភាសាដើម"])
        hdr = self.cue_table.horizontalHeader()
        for col, mode in [(0, QHeaderView.ResizeToContents), (1, QHeaderView.ResizeToContents),
                          (2, QHeaderView.ResizeToContents), (3, QHeaderView.Stretch), (4, QHeaderView.Stretch)]:
            hdr.setSectionResizeMode(col, mode)
        self.cue_table.verticalHeader().setVisible(False)
        self.cue_table.itemChanged.connect(self._cue_edited)
        self.cue_table.cellDoubleClicked.connect(self._cycle_gender)
        lay.addWidget(self.cue_table, 1)

        opts = QGroupBox("ការកំណត់")
        g = QFormLayout(opts)
        self.fit = QCheckBox("ពន្លឿនសំឡេងឱ្យត្រូវនឹងពេល")
        self.fit.setChecked(True)
        self.max_speed = QDoubleSpinBox()
        self.max_speed.setRange(1.0, 2.5)
        self.max_speed.setSingleStep(0.1)
        self.max_speed.setValue(1.5)
        self.max_speed.setSuffix(" x")
        self.workers = QSpinBox()
        self.workers.setRange(1, 16)
        self.workers.setValue(12)
        self.audio_format = QComboBox()
        self.audio_format.addItems(["mp3", "wav"])
        row1 = QHBoxLayout()
        for widget in (self.fit, QLabel("ល្បឿនអតិបរមា"), self.max_speed, QLabel("ដំណើរការស្របគ្នា"),
                       self.workers, QLabel("ទម្រង់សំឡេង"), self.audio_format):
            row1.addWidget(widget)
        row1.addStretch()
        g.addRow(row1)

        self.orig_volume = QSpinBox()
        self.orig_volume.setRange(0, 100)
        self.orig_volume.setValue(15)
        self.orig_volume.setSuffix(" %")
        self.orig_mute = QCheckBox("🔇 Mute សំឡេងដើម (ឮតែសំឡេង TTS)")
        self.orig_mute.toggled.connect(lambda on: self.orig_volume.setEnabled(not on))
        self.orig_mute.setChecked(True)
        self.part_minutes = QSpinBox()
        self.part_minutes.setRange(0, 120)
        self.part_minutes.setValue(10)
        self.part_minutes.setSuffix(" នាទី")
        self.part_minutes.setSpecialValueText("មិនកាត់")
        row2 = QHBoxLayout()
        for widget in (QLabel("វីដេអូ — សំឡេងដើម"), self.orig_volume, self.orig_mute,
                       QLabel("កាត់ជាផ្នែក"), self.part_minutes):
            row2.addWidget(widget)
        row2.addStretch()
        g.addRow(row2)
        lay.addWidget(opts)

        row = QHBoxLayout()
        row.addWidget(self._run_button("🔊 បង្កើតសំឡេង / ដាក់ចូលវីដេអូ", self.run_srt))
        save = QPushButton("💾 រក្សាទុក SRT ដែលបានកែ")
        save.clicked.connect(self.save_srt)
        row.addWidget(save)
        row.addStretch()
        lay.addLayout(row)
        return w

    def _build_stt_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.stt_path, stt_row = self._file_row("ឯកសារសំឡេង ឬវីដេអូ (ភាសាបរទេស)", MEDIA_FILTER)
        form.addRow("ឯកសារ", stt_row)
        self.stt_model = QComboBox()
        self.stt_model.addItems(transcribe.MODELS)
        form.addRow("Model", self.stt_model)
        self.stt_target = QComboBox()
        for code, label in [("km", "ភាសាខ្មែរ"), ("", "មិនបកប្រែ (រក្សាភាសាដើម)"), ("en", "English"),
                            ("th", "ថៃ"), ("vi", "វៀតណាម"), ("zh", "ចិន")]:
            self.stt_target.addItem(label, code)
        form.addRow("បកប្រែទៅជា", self.stt_target)
        self.stt_gender = QCheckBox("ចាប់ភេទអ្នកនិយាយ (ស្រី/ប្រុស)")
        self.stt_gender.setChecked(True)
        form.addRow(self.stt_gender)
        self.stt_dub = QCheckBox("បង្កើតសំឡេង TTS ភ្លាមបន្ទាប់ពីបាន SRT (ប្រើការកំណត់សំឡេងខាងស្តាំ)")
        self.stt_dub.setChecked(True)
        form.addRow(self.stt_dub)
        self.stt_video = QCheckBox("បើជាវីដេអូ — ដាក់សំឡេងខ្មែរចូលវីដេអូ ហើយកាត់ជាផ្នែកៗ (តាមការកំណត់ក្នុងផ្ទាំង SRT)")
        self.stt_video.setChecked(True)
        form.addRow(self.stt_video)
        lay.addLayout(form)
        note = QLabel("Gemini ស្តាប់ បកប្រែ និងចាប់ភេទក្នុងពេលតែមួយ។ សំឡេងវែងត្រូវបំបែកជាផ្នែក 10 នាទី។\n"
                      "ឯកសារដើមរបស់អ្នកមិនត្រូវបានកែប្រែ ឬលុបទេ។")
        note.setStyleSheet("color:#6b6b66")
        lay.addWidget(note)
        row = QHBoxLayout()
        row.addWidget(self._run_button("🎧 បម្លែងទៅជា SRT", self.run_stt))
        row.addStretch()
        lay.addLayout(row)
        lay.addStretch()
        return w

    def _build_merge_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("វីដេអូដែលត្រូវបញ្ចូលគ្នា — អូស ឬប្រើ ↑ ↓ ដើម្បីប្តូរលំដាប់"))
        self.merge_list = FileListBox("ជ្រើសវីដេអូ")
        lay.addWidget(self.merge_list, 1)
        self.merge_reencode = QCheckBox("Encode ឡើងវិញ (យឺត — ប្រើតែពេលលទ្ធផលមានបញ្ហា)")
        lay.addWidget(self.merge_reencode)
        note = QLabel("វីដេអូដែលមានទម្រង់ដូចគ្នាត្រូវចម្លងផ្ទាល់ — លឿនបំផុត ហើយគុណភាពមិនបាត់បង់។")
        note.setStyleSheet("color:#6b6b66")
        lay.addWidget(note)
        row = QHBoxLayout()
        row.addWidget(self._run_button("🎬 Merge វីដេអូ", self.run_merge))
        row.addStretch()
        lay.addLayout(row)
        return w

    # ================= Auto (Folder) =================
    B_ON, B_NAME, B_VIDEOS, B_SRT, B_STATUS = range(5)

    def _build_batch_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        intro = QLabel("Folder នីមួយៗ = រឿងមួយ (វីដេអូ Part + .srt) → Merge → ដាក់សំឡេង TTS → កាត់ជាផ្នែក")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        buttons = QHBoxLayout()
        for text, fn in [("➕ បន្ថែម Folder", self._batch_add_folder),
                         ("➕ បន្ថែម Folder មេ (Folder រងទាំងអស់)", self._batch_add_parent),
                         ("✕ លុប", self._batch_remove), ("សម្អាត", self._batch_clear)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            buttons.addWidget(b)
        buttons.addStretch()
        lay.addLayout(buttons)

        self.batch_rows = []
        t = self.batch_table = QTableWidget(0, 5)
        t.setHorizontalHeaderLabels(["", "រឿង (Folder)", "វីដេអូ", "SRT (ចុចពីរដងដើម្បីប្តូរ)", "ស្ថានភាព"])
        hdr = t.horizontalHeader()
        for col, mode in [(0, QHeaderView.ResizeToContents), (1, QHeaderView.Stretch),
                          (2, QHeaderView.ResizeToContents), (3, QHeaderView.Stretch), (4, QHeaderView.Stretch)]:
            hdr.setSectionResizeMode(col, mode)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(30)
        t.setMinimumHeight(180)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.cellDoubleClicked.connect(self._batch_pick_srt)
        lay.addWidget(t, 1)

        self.batch_keep_merged = QCheckBox("រក្សាទុកវីដេអូដែលបាន Merge (មុនដាក់សំឡេង) ក្នុង outputs/")
        lay.addWidget(self.batch_keep_merged)
        note = QLabel("ប្រើការកំណត់សំឡេងខាងស្តាំ និងផ្ទាំង SRT (Mute សំឡេងដើម, កាត់ជាផ្នែក) · "
                      "SRT មួយក្នុងមួយ Part → បញ្ចូលគ្នាដោយស្វ័យប្រវត្តិ")
        note.setStyleSheet("color:#6b6b66")
        note.setWordWrap(True)
        lay.addWidget(note)

        row = QHBoxLayout()
        row.addWidget(self._run_button("⚡ ចាប់ផ្តើម Auto", self.run_batch))
        self.btn_batch_stop = QPushButton("■ បញ្ឈប់បន្ទាប់ពី Folder នេះ")
        self.btn_batch_stop.setEnabled(False)
        self.btn_batch_stop.clicked.connect(self._batch_request_stop)
        row.addWidget(self.btn_batch_stop)
        self.batch_summary = QLabel()
        row.addWidget(self.batch_summary)
        row.addStretch()
        lay.addLayout(row)

        self.batch_active = False
        self.batch_queue, self.batch_cur, self.batch_tmp = [], None, None
        return w

    @staticmethod
    def _scan_folder(folder):
        files = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
        videos = sorted((os.path.join(folder, f) for f in files if os.path.splitext(f)[1].lower() in VIDEO_EXT),
                        key=lambda p: natural_key(os.path.basename(p)))
        srts = sorted((os.path.join(folder, f) for f in files if f.lower().endswith(".srt")),
                      key=lambda p: natural_key(os.path.basename(p)))
        return videos, srts

    def _batch_add(self, folders):
        known = {r["folder"] for r in self.batch_rows}
        added = 0
        for folder in folders:
            folder = os.path.normpath(folder)
            if folder in known:
                continue
            videos, srts = self._scan_folder(folder)
            if not videos:
                continue
            self.batch_rows.append({"folder": folder, "videos": videos, "srts": srts, "status": "waiting", "msg": ""})
            added += 1
        self._batch_render()
        self.set_status(f"បានបន្ថែម {added} Folder" if added else "រកមិនឃើញ Folder ដែលមានវីដេអូ", not added)

    def _batch_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "ជ្រើស Folder រឿង")
        if folder:
            self._batch_add([folder])

    def _batch_add_parent(self):
        parent = QFileDialog.getExistingDirectory(self, "ជ្រើស Folder មេ (ដែលមាន Folder រឿងនៅខាងក្នុង)")
        if not parent:
            return
        subs = sorted((os.path.join(parent, d) for d in os.listdir(parent) if os.path.isdir(os.path.join(parent, d))),
                      key=lambda p: natural_key(os.path.basename(p)))
        self._batch_add([parent] + subs)

    def _batch_remove(self):
        if self.batch_active:
            return
        rows = sorted({i.row() for i in self.batch_table.selectedIndexes()}, reverse=True)
        for r in rows:
            del self.batch_rows[r]
        self._batch_render()

    def _batch_clear(self):
        if not self.batch_active:
            self.batch_rows = []
            self._batch_render()

    def _batch_srt_text(self, row):
        srts, n = row["srts"], len(row["videos"])
        if not srts:
            return "⚠ គ្មាន SRT — ចុចពីរដងដើម្បីជ្រើស"
        if len(srts) == 1:
            return os.path.basename(srts[0])
        if len(srts) == n:
            return f"{len(srts)} ឯកសារ → បញ្ចូលគ្នាតាម Part"
        return f"⚠ {len(srts)} ឯកសារ (≠ {n} វីដេអូ) — ប្រើ {os.path.basename(srts[0])}"

    _STATUS = {"waiting": ("○ រង់ចាំ", "#6b6b66"), "running": ("● កំពុងដំណើរការ...", "#2f5d8a"),
               "done": ("✓ រួចរាល់", "#2e7d32"), "failed": ("✕ បរាជ័យ", "#b3261e"), "skipped": ("— រំលង", "#8a5a00")}

    def _batch_render(self):
        t = self.batch_table
        checked = [t.item(r, 0).checkState() == Qt.Checked if t.item(r, 0) else True for r in range(t.rowCount())]
        t.setRowCount(len(self.batch_rows))
        for r, row in enumerate(self.batch_rows):
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            on.setCheckState(Qt.Checked if (checked[r] if r < len(checked) else True) else Qt.Unchecked)
            size = sum(os.path.getsize(v) for v in row["videos"] if os.path.exists(v))
            text, color = self._STATUS[row["status"]]
            items = [on, QTableWidgetItem(os.path.basename(row["folder"])),
                     QTableWidgetItem(f"{len(row['videos'])} Part · {fmt_size(size)}"),
                     QTableWidgetItem(self._batch_srt_text(row)),
                     QTableWidgetItem(f"{text} {row['msg']}".strip())]
            items[1].setToolTip(row["folder"])
            items[2].setToolTip("\n".join(os.path.basename(v) for v in row["videos"]))
            items[3].setToolTip("\n".join(row["srts"]))
            items[4].setForeground(QColor(color))
            items[4].setToolTip(row["msg"])
            for col, item in enumerate(items):
                t.setItem(r, col, item)
        done = sum(r["status"] == "done" for r in self.batch_rows)
        failed = sum(r["status"] == "failed" for r in self.batch_rows)
        self.batch_summary.setText(f"{len(self.batch_rows)} រឿង · ✓ {done} · ✕ {failed}" if self.batch_rows else "")

    def _batch_pick_srt(self, r, col):
        if col != self.B_SRT or self.batch_active:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "ជ្រើស SRT (មួយ ឬមួយក្នុងមួយ Part)",
                                                self.batch_rows[r]["folder"], "SRT (*.srt)")
        if paths:
            self.batch_rows[r]["srts"] = sorted(paths, key=lambda p: natural_key(os.path.basename(p)))
            self._batch_render()

    def _batch_request_stop(self):
        self.batch_stop = True
        self.btn_batch_stop.setEnabled(False)
        self.set_status("នឹងបញ្ឈប់បន្ទាប់ពី Folder នេះចប់")

    @staticmethod
    def _batch_load_cues(row):
        """អាន SRT — បើមាន SRT មួយក្នុងមួយ Part បញ្ចូលគ្នាដោយបន្ថែមរយៈពេលនៃ Part មុនៗ"""
        def read(path):
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                return srt_dub.parse_srt(f.read())
        srts, videos, notes = row["srts"], row["videos"], []
        if len(srts) > 1 and len(srts) == len(videos):
            cues, offset = [], 0
            for srt, video in zip(srts, videos):
                for c in read(srt):
                    cues.append(dict(c, start=c["start"] + offset, end=c["end"] + offset, index=len(cues) + 1))
                offset += int(video_dub.probe(video)[0] * 1000)
            notes.append(f"បញ្ចូល SRT {len(srts)} ឯកសារ")
        else:
            if len(srts) > 1:
                notes.append(f"SRT {len(srts)} ≠ វីដេអូ {len(videos)} — ប្រើតែ {os.path.basename(srts[0])}")
            cues = read(srts[0])
        if not cues:
            raise RuntimeError("SRT គ្មាន subtitle")
        return cues, notes

    def run_batch(self):
        if self.busy or self.batch_active or not self._check_ffmpeg():
            return
        t = self.batch_table
        queue = []
        for r, row in enumerate(self.batch_rows):
            if t.item(r, 0).checkState() != Qt.Checked:
                continue
            row["videos"] = [v for v in row["videos"] if os.path.exists(v)]
            if not row["videos"] or not row["srts"]:
                row.update(status="skipped", msg="គ្មានវីដេអូ" if not row["videos"] else "គ្មាន SRT")
                continue
            row.update(status="waiting", msg="")
            queue.append(r)
        self._batch_render()
        if not queue:
            return self.set_status("គ្មាន Folder ដែលត្រៀមរួច (ត្រូវមានវីដេអូ និង SRT)", True)
        self.batch_queue, self.batch_total = queue, len(queue)
        self.batch_active, self.batch_stop = True, False
        self.btn_batch_stop.setEnabled(True)
        self.batch_t0 = time.time()
        self._log(f"⚡ Auto — {len(queue)} Folder")
        self._batch_next()

    def _batch_next(self):
        if self.batch_stop or not self.batch_queue:
            return self._batch_finish()
        r = self.batch_cur = self.batch_queue.pop(0)
        row = self.batch_rows[r]
        row.update(status="running", msg="")
        self._batch_render()
        name = os.path.basename(row["folder"])
        base = backend.safe_name(name) or "video"
        n = self.batch_total - len(self.batch_queue)
        dub = self.dub_stages(True)
        merge = len(row["videos"]) > 1
        self.begin_stages(f"Auto {n}/{self.batch_total}: {name}",
                          ["អាន SRT"] + (["Merge វីដេអូ"] if merge else []) + dub)
        self.enter_stage("អាន SRT")

        def start_dub(video, cues):
            try:
                args = self._dub_args(cues)
            except backend.BadRequest as e:
                return self.fail(str(e))
            self._start_video_dub(args, video, f"{base}_dub_{time.strftime('%Y%m%d_%H%M%S')}", dub, done)

        def after_cues(result):
            cues, notes = result
            for note in notes:
                self._log(f"   {note}")
            if not merge:
                return start_dub(row["videos"][0], cues)
            out_dir = OUTPUT_DIR if self.batch_keep_merged.isChecked() else tempfile.mkdtemp(prefix="auto_merge_")
            self.batch_tmp = None if out_dir == OUTPUT_DIR else out_dir
            job = video_merge.start_job(row["videos"], out_dir, base, False, [])
            self.enter_stage("Merge វីដេអូ")
            self.set_busy(True)
            self.watch_job(job, lambda s: f"{name}: កំពុង Merge {len(row['videos'])} Part {s['done']}%...",
                           lambda s: start_dub(os.path.join(out_dir, s["file"]), cues), lambda s: "Merge វីដេអូ")

        def done(s):
            row.update(status="done", msg=f"{len(s['parts'])} ផ្នែក → outputs/{s['folder']}")
            self._batch_cleanup_tmp()
            self._batch_render()
            self.set_result(os.path.join(OUTPUT_DIR, s["folder"]), f"{name}: រួចរាល់ {len(s['parts'])} ផ្នែក")
            QTimer.singleShot(200, self._batch_next)

        self.run_sync(lambda: self._batch_load_cues(row), f"{name}: កំពុងអាន SRT...", after_cues)

    def _batch_failed(self, msg):
        """ហៅពី fail() — សម្គាល់ Folder នេះថាបរាជ័យ ហើយបន្តទៅ Folder បន្ទាប់"""
        if self.batch_cur is not None:
            self.batch_rows[self.batch_cur].update(status="failed", msg=msg)
        self._batch_cleanup_tmp()
        self._batch_render()
        QTimer.singleShot(200, self._batch_next)

    def _batch_cleanup_tmp(self):
        if self.batch_tmp:
            shutil.rmtree(self.batch_tmp, ignore_errors=True)
            self.batch_tmp = None

    def _batch_finish(self):
        self.batch_active, self.batch_cur = False, None
        self.btn_batch_stop.setEnabled(False)
        rows = self.batch_rows
        done = sum(r["status"] == "done" for r in rows)
        failed = sum(r["status"] == "failed" for r in rows)
        left = sum(r["status"] == "waiting" for r in rows)
        msg = (f"Auto ចប់ — ✓ {done} · ✕ {failed}" + (f" · បញ្ឈប់ (នៅសល់ {left})" if left else "") +
               f" · សរុប {fmt_dur(time.time() - self.batch_t0)}")
        self._log(f"⚡ {msg}")
        self.set_status(msg, failed > 0)
        self._batch_render()

    def _build_files_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.files_tree = QTreeWidget()
        self.files_tree.setHeaderLabels(["ឈ្មោះ", "ប្រភេទ", "ទំហំ", "ពេលវេលា"])
        self.files_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.files_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files_tree.itemDoubleClicked.connect(lambda item, _: open_path(item.data(0, Qt.UserRole)))
        lay.addWidget(self.files_tree, 1)
        row = QHBoxLayout()
        for text, fn in [("↻ ផ្ទុកឡើងវិញ", self.refresh_files), ("▶ បើក", self._files_open),
                         ("📂 បង្ហាញក្នុងថត", self._files_reveal), ("🎬 Merge", self._files_merge),
                         ("🔇 Mute", self._files_mute), ("📝 ប្រើ SRT នេះ", self._files_use_srt),
                         ("📁 ថត outputs", lambda: open_path(OUTPUT_DIR))]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        return w

    def _build_status_bar(self):
        box = QGroupBox("ដំណើរការ")
        lay = QVBoxLayout(box)

        # ជួរដំណាក់កាល: ✓ រួច → ● កំពុងធ្វើ → ○ រង់ចាំ   ·   ⏱ ពេលវេលា
        top = QHBoxLayout()
        self.stage_label = QLabel()
        self.stage_label.setTextFormat(Qt.RichText)
        self.stage_label.setWordWrap(True)
        top.addWidget(self.stage_label, 1)
        self.elapsed = QLabel()
        self.elapsed.setStyleSheet("color:#6b6b66")
        top.addWidget(self.elapsed)
        lay.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setValue(0)
        lay.addWidget(self.progress)

        row = QHBoxLayout()
        self.status = QLabel("រួចរាល់")
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        self.btn_log = QPushButton("📜 កំណត់ហេតុ")
        self.btn_log.setCheckable(True)
        self.btn_log.setChecked(True)
        row.addWidget(self.btn_log)
        self.btn_open_last = QPushButton("▶ បើកលទ្ធផល")
        self.btn_reveal_last = QPushButton("📂 បង្ហាញក្នុងថត")
        for b in (self.btn_open_last, self.btn_reveal_last):
            b.setEnabled(False)
            row.addWidget(b)
        self.btn_open_last.clicked.connect(lambda: open_path(self.last_result))
        self.btn_reveal_last.clicked.connect(lambda: reveal(self.last_result))
        lay.addLayout(row)

        self.warnings = QLabel()
        self.warnings.setStyleSheet("color:#8a5a00")
        self.warnings.setWordWrap(True)
        self.warnings.hide()
        lay.addWidget(self.warnings)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(96)
        self.log.setPlaceholderText("កំណត់ហេតុ — ដំណាក់កាលនីមួយៗ និងរយៈពេលរបស់វានឹងបង្ហាញនៅទីនេះ")
        self.log.setStyleSheet("font-size:9pt")
        self.btn_log.toggled.connect(self.log.setVisible)
        lay.addWidget(self.log)

        self.stage_names, self.stage_idx, self.stage_state = [], -1, "idle"
        self.run_t0 = self.stage_t0 = 0.0
        self.clock = QTimer(self)
        self.clock.timeout.connect(self._tick)
        self._render_stages()

        outer = QVBoxLayout()
        outer.addWidget(box)
        return outer

    # ================= stages (ដំណាក់កាល) =================
    def begin_stages(self, title, names):
        """ចាប់ផ្តើមការងារថ្មី — names = បញ្ជីដំណាក់កាលតាមលំដាប់"""
        self.stage_names, self.stage_idx, self.stage_state = list(names), -1, "running"
        self.run_t0 = self.stage_t0 = time.time()
        self._log(f"▶ {title}")
        self.clock.start(1000)
        self._render_stages()
        self._tick()

    def enter_stage(self, name):
        if self.stage_state != "running" or name not in self.stage_names:
            return
        idx = self.stage_names.index(name)
        if idx <= self.stage_idx:
            return
        if self.stage_idx >= 0:
            self._log(f"   ✓ {self.stage_names[self.stage_idx]} ({time.time() - self.stage_t0:.1f}s)")
        for skipped in self.stage_names[self.stage_idx + 1:idx]:  # ដំណាក់កាលដែលចប់លឿនពេក (ឧ. cache)
            self._log(f"   ✓ {skipped} (< 0.5s)")
        self.stage_idx, self.stage_t0 = idx, time.time()
        self._log(f"   ● {name}...")
        self._render_stages()

    def finish_stages(self, ok, msg=""):
        if self.stage_state != "running":
            return
        if ok:
            if self.stage_idx >= 0:
                self._log(f"   ✓ {self.stage_names[self.stage_idx]} ({time.time() - self.stage_t0:.1f}s)")
            self.stage_idx = len(self.stage_names)
            self._log(f"✔ រួចរាល់ — សរុប {time.time() - self.run_t0:.1f}s" + (f" · {msg}" if msg else ""))
        else:
            self._log(f"✕ បរាជ័យ: {msg}")
        self.stage_state = "done" if ok else "failed"
        self.clock.stop()
        self._tick()
        self._render_stages()

    def _render_stages(self):
        if not self.stage_names:
            self.stage_label.setText("<span style='color:#9a9a94'>មិនទាន់មានការងារ</span>")
            return
        parts = []
        for i, name in enumerate(self.stage_names):
            if i < self.stage_idx:
                parts.append(f"<span style='color:#2e7d32'>✓ {name}</span>")
            elif i == self.stage_idx and self.stage_state == "failed":
                parts.append(f"<b style='color:#b3261e'>✕ {name}</b>")
            elif i == self.stage_idx:
                parts.append(f"<b style='color:#2f5d8a'>● {name}</b>")
            elif self.stage_state == "failed" and self.stage_idx < 0 and i == 0:
                parts.append(f"<b style='color:#b3261e'>✕ {name}</b>")
            else:
                parts.append(f"<span style='color:#9a9a94'>○ {name}</span>")
        step = min(self.stage_idx + 1, len(self.stage_names))
        head = f"<b>ដំណាក់កាល {step}/{len(self.stage_names)}</b> &nbsp; " if self.stage_state == "running" else ""
        self.stage_label.setText(head + " <span style='color:#9a9a94'>→</span> ".join(parts))

    def _tick(self):
        if not self.stage_names:
            self.elapsed.setText("")
            return
        total = fmt_dur(time.time() - self.run_t0)
        if self.stage_state == "running" and 0 <= self.stage_idx < len(self.stage_names):
            self.elapsed.setText(f"⏱ {total} · ដំណាក់កាលនេះ {fmt_dur(time.time() - self.stage_t0)}")
        else:
            self.elapsed.setText(f"⏱ {total}")

    def _log(self, line):
        self.log.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {line}")

    # ================= voices =================
    def engine(self):
        return "gemini" if self.rb_gemini.isChecked() else "edge"

    def load_edge_voices(self):
        def done(voices):
            self.edge_voices = voices
            self._fill_edge_langs()
        worker = Worker(backend.get_edge_voices)
        worker.done.connect(done)
        worker.failed.connect(lambda e: (self.set_status(f"មិនអាចទាញយកបញ្ជីសំឡេង Edge ({e}) — ប្រើសំឡេងខ្មែរ", True),
                                         self._fill_edge_langs()))
        self._start_worker(worker)

    def _fill_edge_langs(self):
        langs = sorted({v["locale"] for v in self.edge_voices}, key=lambda l: (not l.startswith("km"), l))
        self.edge_lang.blockSignals(True)
        self.edge_lang.clear()
        self.edge_lang.addItems(langs)
        saved = self.settings.value("edge_lang", "km-KH")
        self.edge_lang.setCurrentText(saved if saved in langs else langs[0])
        self.edge_lang.blockSignals(False)
        self._fill_edge_voices()

    def _voice_label(self, v, prefix=""):
        return f"{v['name'].replace(prefix, '')} ({GENDER_KM.get(v['gender'].lower(), '?')})"

    def _fill_edge_voices(self):
        lang = self.edge_lang.currentText()
        self.edge_voice.blockSignals(True)  # កុំឱ្យការបំពេញបញ្ជីសរសេរជាន់សំឡេងដែលបានរក្សាទុក
        self.edge_voice.clear()
        for v in self.edge_voices:
            if v["locale"] == lang:
                self.edge_voice.addItem(self._voice_label(v, lang + "-"), v["name"])
        self._select_data(self.edge_voice, self.settings.value("edge_voice"))
        self.edge_voice.blockSignals(False)
        self._fill_gender_voices()

    def _fill_gender_voices(self):
        engine = self.engine()
        if engine == "edge":
            lang = self.edge_lang.currentText()
            voices, prefix = [v for v in self.edge_voices if v["locale"] == lang], lang + "-"
        else:
            voices, prefix = backend.GEMINI_VOICES, ""
        for gender, combo in (("female", self.voice_female), ("male", self.voice_male)):
            combo.blockSignals(True)
            combo.clear()
            matching = [v for v in voices if v["gender"].lower() == gender] or voices
            for v in matching:
                combo.addItem(self._voice_label(v, prefix), v["name"])
            self._select_data(combo, self.settings.value(f"voice_{gender}_{engine}"))
            combo.blockSignals(False)

    @staticmethod
    def _select_data(combo, value):
        if value is not None:
            i = combo.findData(value)
            if i >= 0:
                combo.setCurrentIndex(i)

    def _update_engine(self):
        gemini = self.engine() == "gemini"
        self.edge_box.setVisible(not gemini)
        self.gemini_box.setVisible(gemini)
        self._fill_gender_voices()

    def voice_settings(self):
        """ការកំណត់ដូច payload របស់កំណែ web (សម្រាប់ backend._dub_setup)"""
        p = {"engine": self.engine(), "strip_parens": self.strip_parens.isChecked(),
             "auto_gender": self.auto_gender.isChecked(),
             "voice_female": self.voice_female.currentData(), "voice_male": self.voice_male.currentData(),
             "api_key": self.api_key.text().strip()}
        if p["engine"] == "edge":
            p.update(voice=self.edge_voice.currentData() or "km-KH-SreymomNeural",
                     rate=self.rate[1].value(), pitch=self.pitch[1].value(), volume=self.volume[1].value())
        else:
            p.update(voice=self.gemini_voice.currentData(), model=self.gemini_model.currentText(),
                     style=self.gemini_style.text())
        return p

    def gemini_key(self):
        return self.api_key.text().strip() or os.environ.get("GEMINI_API_KEY", "")

    # ================= busy / status / jobs =================
    def set_status(self, msg, error=False):
        self.status.setText(msg)
        self.status.setStyleSheet("color:#b3261e" if error else "")

    def set_busy(self, busy, msg=None):
        self.busy = busy
        for b in self.run_buttons:
            b.setEnabled(not busy)
        if busy:
            self.warnings.hide()
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        if msg:
            self.set_status(msg)

    def fail(self, msg):
        self.timer.stop()
        self.set_busy(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.set_status(f"កំហុស: {msg}", True)
        self.finish_stages(False, msg)
        if self.batch_active:  # Auto: Folder នេះបរាជ័យ → បន្ត Folder បន្ទាប់
            self._batch_failed(msg)

    def set_result(self, path, msg, finish=True):
        """finish=False — ការងារបន្តទៅដំណាក់កាលបន្ទាប់ (ឧ. SRT → បង្កើតសំឡេង)"""
        if finish:
            self.finish_stages(True, os.path.basename(path) if path else "")
        self.progress.setFormat("%p%")
        self.last_result = path
        for b in (self.btn_open_last, self.btn_reveal_last):
            b.setEnabled(bool(path))
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.set_status(msg)
        self.refresh_files()

    def show_warnings(self, warnings):
        if warnings:
            shown = warnings[:15] + ([f"... និង {len(warnings) - 15} ទៀត"] if len(warnings) > 15 else [])
            self.warnings.setText("\n".join(shown))
            self.warnings.show()

    def _start_worker(self, worker):
        self._workers.append(worker)
        worker.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        worker.start()

    def run_sync(self, fn, msg, on_done):
        """ដំណើរការ fn នៅ background ជាមួយ progress bar មិនកំណត់"""
        self.set_busy(True, msg)
        self.progress.setRange(0, 0)
        worker = Worker(fn)

        def ok(result):
            self.set_busy(False)
            self.progress.setRange(0, 100)
            try:
                on_done(result)
            except Exception as e:  # noqa: BLE001
                self.fail(str(e))
        worker.done.connect(ok)
        worker.failed.connect(self.fail)
        self._start_worker(worker)

    def watch_job(self, job_id, label, on_done, stage_of=None, unit=None):
        """តាមដាន job របស់ backend (srt_dub.jobs) រហូតដល់ចប់។
        stage_of(s) → ឈ្មោះដំណាក់កាលបច្ចុប្បន្ន, unit → បង្ហាញ "done/total unit" លើ progress bar (None = %)"""
        self._job = (job_id, label, on_done, stage_of, unit)
        self.progress.setRange(0, 100)
        self.timer.start(400)

    def _poll_job(self):
        job_id, label, on_done, stage_of, unit = self._job
        s = srt_dub.jobs.get(job_id)
        if s is None:
            return self.fail("រកមិនឃើញការងារ")
        if stage_of:
            self.enter_stage(stage_of(s))
        if s["status"] == "mixing":  # មិនដឹងភាគរយ → progress bar រត់ទៅមក
            self.progress.setRange(0, 0)
        elif s["status"] == "video":
            self.progress.setRange(0, 100)
            self.progress.setValue(int(s.get("stage_pct", 0)))
            self.progress.setFormat("វីដេអូ %p%")
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(100 * s["done"] / max(s["total"], 1)))
            self.progress.setFormat(f"{s['done']}/{s['total']} {unit} · %p%" if unit else "%p%")
        if s["status"] == "error":
            self.show_warnings(s.get("warnings"))
            return self.fail(s["error"])
        if s["status"] == "done":
            self.timer.stop()
            self.set_busy(False)
            self.progress.setRange(0, 100)
            self.show_warnings(s.get("warnings"))
            if s.get("warnings"):
                self._log(f"   ⚠ ការព្រមាន {len(s['warnings'])}")
            try:
                on_done(s)
            except Exception as e:  # noqa: BLE001
                self.fail(str(e))
            return
        self.set_status({"mixing": "កំពុងតម្រឹមពេលវេលា (ពន្លឿនសំឡេងដែលវែងពេក)...",
                         "video": "កំពុងដាក់សំឡេងចូលវីដេអូ និងកាត់ជាផ្នែកៗ..."}.get(s["status"]) or label(s))

    def _check_ffmpeg(self):
        if not shutil.which("ffmpeg"):
            QMessageBox.warning(self, "ffmpeg", "រកមិនឃើញ ffmpeg — សូមដំឡើង ffmpeg ហើយដាក់ក្នុង PATH")
            return False
        return True

    # ================= actions =================
    def run_text(self):
        if self.busy:
            return
        text = self.text_edit.toPlainText().strip()
        if self.strip_parens.isChecked():
            text = backend.strip_parens(text)
        if not text:
            return self.set_status("សូមបញ្ចូលអត្ថបទ", True)
        p = self.voice_settings()
        if p["engine"] == "edge":
            fn = lambda: backend.edge_tts_generate(text, p["voice"], p["rate"], p["pitch"], p["volume"])  # noqa: E731
        else:
            key = self.gemini_key()
            if not key:
                return self.set_status("សូមបញ្ចូល Gemini API Key", True)
            fn = lambda: backend.gemini_tts_generate(text, key, p["model"], p["voice"], p["style"])  # noqa: E731
        t0 = time.time()
        stage = f"បង្កើតសំឡេង ({'Edge' if p['engine'] == 'edge' else 'Gemini'} · {len(text)} តួអក្សរ)"
        self.begin_stages("អត្ថបទ → សំឡេង", [stage])
        self.enter_stage(stage)
        self.run_sync(fn, "កំពុងបង្កើតសំឡេង...",
                      lambda name: self.set_result(os.path.join(OUTPUT_DIR, name),
                                                   f"រួចរាល់ ({time.time() - t0:.1f}s) — {name}"))

    # ---- SRT ----
    def load_srt_file(self, path):
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                cues = srt_dub.parse_srt(f.read())
        except OSError as e:
            return self.set_status(f"មិនអាចបើក SRT: {e}", True)
        if not cues:
            return self.set_status("មិនមាន subtitle ក្នុងឯកសារនេះទេ", True)
        self.set_cues(cues)
        self.set_status(f"{len(cues)} បន្ទាត់ · {fmt_ms(cues[-1]['end'])}")

    def set_cues(self, cues):
        self.cues = [dict(c, gender=c.get("gender") if c.get("gender") in GENDER_KM else None) for c in cues]
        t = self.cue_table
        t.blockSignals(True)
        t.setRowCount(len(self.cues))
        for r, c in enumerate(self.cues):
            items = [QTableWidgetItem(str(c.get("index", r + 1))), QTableWidgetItem(fmt_ms(c["start"])),
                     QTableWidgetItem(), QTableWidgetItem(c["text"]),
                     QTableWidgetItem(c["original"] if c.get("original") not in (None, c["text"]) else "")]
            for col, item in enumerate(items):
                if col != 3:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                t.setItem(r, col, item)
            self._paint_gender(r)
        t.blockSignals(False)
        self._update_cue_info()

    def _paint_gender(self, row):
        g = self.cues[row].get("gender")
        item = self.cue_table.item(row, 2)
        item.setText(GENDER_KM.get(g or "", "—"))
        item.setForeground(GENDER_COLOR.get(g or "", QColor("#6b6b66")))

    def _cycle_gender(self, row, col):
        if col != 2:
            return
        order = [None, "female", "male"]
        self.cues[row]["gender"] = order[(order.index(self.cues[row].get("gender")) + 1) % 3]
        self.cue_table.blockSignals(True)
        self._paint_gender(row)
        self.cue_table.blockSignals(False)
        self._update_cue_info()

    def _cue_edited(self, item):
        if item.column() == 3 and item.row() < len(self.cues):
            self.cues[item.row()]["text"] = item.text().strip()

    def _update_cue_info(self):
        f = sum(c.get("gender") == "female" for c in self.cues)
        m = sum(c.get("gender") == "male" for c in self.cues)
        self.cue_info.setText(f"{len(self.cues)} បន្ទាត់ · ស្រី {f} · ប្រុស {m} · មិនស្គាល់ {len(self.cues) - f - m} "
                              "— ចុចពីរដងលើ 'ភេទ' ដើម្បីប្តូរ · អាចកែអត្ថបទបាន")

    def save_srt(self):
        if not self.cues:
            return self.set_status("មិនមាន subtitle", True)
        base = os.path.splitext(os.path.basename(self.srt_path.text()))[0] or "subtitle"
        path, _ = QFileDialog.getSaveFileName(self, "រក្សាទុក SRT", os.path.join(OUTPUT_DIR, f"{base}_edited.srt"),
                                              "SRT (*.srt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(cues_to_srt(self.cues))
            self.srt_path.setText(path)
            self.set_result(path, f"បានរក្សាទុក {os.path.basename(path)}")

    # ឈ្មោះដំណាក់កាលនៃការបង្កើតសំឡេងពី SRT
    S_TTS, S_MIX, S_MIX_SAVE = "បង្កើតសំឡេង", "តម្រឹមពេលវេលា", "តម្រឹមពេលវេលា & រក្សាទុក"

    def dub_stages(self, with_video):
        if not with_video:
            return [self.S_TTS, self.S_MIX_SAVE]
        return [self.S_TTS, self.S_MIX,
                "ដាក់ចូលវីដេអូ & កាត់ជាផ្នែក" if self.part_minutes.value() else "ដាក់ចូលវីដេអូ"]

    def _dub_args(self, cues):
        """arguments សម្រាប់ srt_dub.start_job ពីការកំណត់បច្ចុប្បន្ន (raise backend.BadRequest)"""
        p = self.voice_settings()
        p.update(cues=[dict(c) for c in cues if c["text"]], fit=self.fit.isChecked(),
                 max_speed=self.max_speed.value(), format=self.audio_format.currentText(),
                 workers=self.workers.value())
        p["auto_gender"] = p["auto_gender"] and any(c.get("gender") for c in cues)
        if p["engine"] == "gemini" and not p["api_key"]:
            p["api_key"] = self.gemini_key()
        return backend._dub_setup(p)

    def _start_video_dub(self, args, video, name_base, stages, on_done):
        """បង្កើតសំឡេង → ដាក់ចូលវីដេអូ → កាត់ជាផ្នែក (stages = ឈ្មោះ 3 ដំណាក់កាល)"""
        orig = 0.0 if self.orig_mute.isChecked() else self.orig_volume.value() / 100
        part_sec = self.part_minutes.value() * 60

        def post(job, pcm):
            job["folder"], job["parts"] = video_dub.mix_and_split(
                video, pcm, args["cues"], OUTPUT_DIR, name_base, orig, part_sec, job)

        job = srt_dub.start_job(**args, post=post)
        self.enter_stage(stages[0])
        self.set_busy(True, "កំពុងចាប់ផ្តើម...")
        self.watch_job(job, lambda s: f"កំពុងបង្កើតសំឡេង {s['done']}/{s['total']} បន្ទាត់...", on_done,
                       lambda s: {"running": stages[0], "mixing": stages[1]}.get(s["status"], stages[2]), "បន្ទាត់")

    def run_srt(self, chained=False):
        """chained=True — បន្តពី "សំឡេង → SRT" (ដំណាក់កាលត្រូវបានកំណត់រួចហើយ)"""
        chained = chained is True  # ប៊ូតុងផ្ញើ checked=False មក
        error = self.fail if chained else (lambda m: self.set_status(m, True))
        if self.busy or not self._check_ffmpeg():
            return
        if not self.cues:
            return error("សូមជ្រើសរើសឯកសារ .srt")
        try:
            args = self._dub_args(self.cues)
        except backend.BadRequest as e:
            return error(str(e))

        video = self.video_path.text().strip()
        t0 = time.time()
        stages = self.dub_stages(bool(video))
        engine = "Edge" if self.engine() == "edge" else "Gemini"
        if not video:
            if not chained:
                self.begin_stages(f"SRT → សំឡេង ({engine} · {len(args['cues'])} បន្ទាត់)", stages)
            job = srt_dub.start_job(**args)
            self.enter_stage(stages[0])
            self.set_busy(True, "កំពុងចាប់ផ្តើម...")
            self.watch_job(job, lambda s: f"កំពុងបង្កើតសំឡេង {s['done']}/{s['total']} បន្ទាត់...",
                           lambda s: self.set_result(os.path.join(OUTPUT_DIR, s["file"]),
                                                     f"រួចរាល់ {s['total']} បន្ទាត់ ({time.time() - t0:.1f}s) — {s['file']}"),
                           lambda s: stages[0] if s["status"] == "running" else stages[1], "បន្ទាត់")
            return

        try:
            video_dub.probe(video)
        except Exception as e:  # noqa: BLE001
            return error(f"មិនអាចអានវីដេអូ: {e}")
        if not chained:
            self.begin_stages(f"SRT → វីដេអូ ({engine} · {len(args['cues'])} បន្ទាត់ · {os.path.basename(video)})",
                              stages)
        base = backend.safe_name(os.path.splitext(os.path.basename(video))[0]) or "video"

        def done(s):
            folder = os.path.join(OUTPUT_DIR, s["folder"])
            first = os.path.join(OUTPUT_DIR, s["parts"][0]["path"]) if s["parts"] else folder
            self.set_result(first if len(s["parts"]) == 1 else folder,
                            f"រួចរាល់ — {len(s['parts'])} ផ្នែក ({time.time() - t0:.1f}s) — outputs/{s['folder']}")

        self._start_video_dub(args, video, f"{base}_dub_{time.strftime('%Y%m%d_%H%M%S')}", stages, done)

    # ---- សំឡេង → SRT ----
    def run_stt(self):
        if self.busy or not self._check_ffmpeg():
            return
        src = self.stt_path.text().strip()
        if not os.path.isfile(src):
            return self.set_status("សូមជ្រើសរើសឯកសារសំឡេង ឬវីដេអូ", True)
        key = self.gemini_key()
        if not key:
            return self.set_status("សូមបញ្ចូល Gemini API Key (ខាងស្តាំ)", True)
        target = self.stt_target.currentData()
        is_video = os.path.splitext(src)[1].lower() in VIDEO_EXT
        t0 = time.time()
        s_prep = "រៀបចំឯកសារ"
        s_gemini = "Gemini ស្តាប់ & បកប្រែ" if target else "Gemini ស្តាប់"
        dub = self.stt_dub.isChecked()
        with_video = is_video and self.stt_video.isChecked()
        self.begin_stages(f"សំឡេង → SRT ({os.path.basename(src)})",
                          [s_prep, s_gemini, "រក្សាទុក SRT"] + (self.dub_stages(with_video) if dub else []))
        self.enter_stage(s_prep)

        def copy_source():
            # transcribe លុបឯកសារ input ពេលចប់ → ផ្តល់ច្បាប់ចម្លង មិនមែនឯកសារដើមទេ
            fd, tmp = tempfile.mkstemp(suffix=os.path.splitext(src)[1] or ".bin")
            os.close(fd)
            shutil.copyfile(src, tmp)
            return tmp

        def start(tmp):
            base = backend.safe_name(os.path.splitext(os.path.basename(src))[0]) or "audio"
            job = transcribe.start_job(tmp, key, self.stt_model.currentText(), self.stt_gender.isChecked(),
                                       target, OUTPUT_DIR, base, backend.gemini_post)
            self.set_busy(True)
            action = "ស្តាប់ និងបកប្រែ" if target else "ស្តាប់"
            self.watch_job(job, lambda s: f"Gemini កំពុង{action} {s['done']}/{s['total']} ផ្នែក (10 នាទី/ផ្នែក)...",
                           done, lambda s: s_gemini, "ផ្នែក")

        def done(s):
            self.enter_stage("រក្សាទុក SRT")
            srt_file = os.path.join(OUTPUT_DIR, s["file"])
            self.srt_path.setText(srt_file)
            self.set_cues(s["cues"])
            self.video_path.setText(src if with_video else "")
            self._log(f"   SRT: {s['file']} ({len(s['cues'])} បន្ទាត់)")
            self.set_result(srt_file, f"បាន SRT {len(s['cues'])} បន្ទាត់ ({time.time() - t0:.1f}s) — {s['file']}",
                            finish=not dub)
            self.tabs.setCurrentIndex(1)
            if dub:
                QTimer.singleShot(300, lambda: self.run_srt(chained=True))

        self.run_sync(copy_source, "កំពុងរៀបចំឯកសារ...", start)

    # ---- Merge / Mute ----
    def run_merge(self, paths=None):
        if self.busy or not self._check_ffmpeg():
            return
        paths = paths or self.merge_list.paths()
        if len(paths) < 2:
            return self.set_status("សូមជ្រើសរើសវីដេអូយ៉ាងតិច 2", True)
        t0 = time.time()
        self.begin_stages(f"Merge {len(paths)} វីដេអូ", ["វិភាគវីដេអូ", "Merge"])
        self.enter_stage("វិភាគវីដេអូ")
        job = video_merge.start_job(paths, OUTPUT_DIR, backend._merge_base_name(paths[0]),
                                    self.merge_reencode.isChecked(), [])

        def done(s):
            mode = "ចម្លងផ្ទាល់" if s["mode"] == "copy" else "encode ឡើងវិញ"
            self.set_result(os.path.join(OUTPUT_DIR, s["file"]),
                            f"រួចរាល់ — {s['parts']} ផ្នែក → {fmt_dur(s['duration'])} · {fmt_size(s['size'])} · "
                            f"{mode} ({time.time() - t0:.1f}s)")
        self.set_busy(True, "កំពុង Merge...")
        mode_km = {"copy": "ចម្លងផ្ទាល់", "reencode": "encode ឡើងវិញ"}
        self.watch_job(job, lambda s: f"កំពុង Merge ({mode_km.get(s.get('mode'), '...')}) {s['done']}%...", done,
                       lambda s: "Merge" if s.get("mode") else "វិភាគវីដេអូ")

    def run_mute(self, paths=None, audio=None):
        """លុបសំឡេងដើម — បើមាន audio វីដេអូនឹងឮតែសំឡេងនោះប៉ុណ្ណោះ"""
        if self.busy or not self._check_ffmpeg():
            return
        if not paths:
            return self.set_status("សូមជ្រើសរើសវីដេអូ", True)
        bases = [backend.safe_name(os.path.splitext(os.path.basename(p))[0]) or "video" for p in paths]
        t0 = time.time()
        stage = "Mute សំឡេងដើម + ដាក់សំឡេងថ្មី" if audio else "Mute វីដេអូ"
        self.begin_stages(f"{stage} ({len(paths)} វីដេអូ)", [stage])
        self.enter_stage(stage)
        job = video_merge.start_mute_job(paths, bases, OUTPUT_DIR, audio=audio)

        def done(s):
            what = "វីដេអូឮតែសំឡេងថ្មី" if audio else "វីដេអូគ្មានសំឡេង"
            self.set_result(os.path.join(OUTPUT_DIR, s["files"][0]["name"]),
                            f"រួចរាល់ — {len(s['files'])} {what} ({time.time() - t0:.1f}s)")
        self.set_busy(True, "កំពុង Mute...")
        self.watch_job(job, lambda s: f"កំពុង Mute {s['done']}%...", done)

    # ================= files tab =================
    def refresh_files(self):
        tree = self.files_tree
        tree.clear()
        entries = []
        kinds = {".mp4": "វីដេអូ", ".mkv": "វីដេអូ", ".mov": "វីដេអូ", ".mp3": "សំឡេង", ".wav": "សំឡេង", ".srt": "SRT"}
        for e in os.scandir(OUTPUT_DIR):
            if e.name.startswith("."):
                continue
            if e.is_dir():
                files = [f for f in os.scandir(e.path) if f.is_file() and f.name.lower().endswith(".mp4")]
                if files:
                    entries.append((max(f.stat().st_mtime for f in files), e, files))
            elif os.path.splitext(e.name)[1].lower() in kinds:
                entries.append((e.stat().st_mtime, e, None))
        for mtime, e, files in sorted(entries, key=lambda x: x[0], reverse=True):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
            if files is not None:
                total = sum(f.stat().st_size for f in files)
                parent = QTreeWidgetItem([e.name, f"ថត · {len(files)} វីដេអូ", fmt_size(total), when])
                parent.setData(0, Qt.UserRole, e.path)
                parent.setToolTip(0, e.name)
                for f in sorted(files, key=lambda f: natural_key(f.name)):
                    child = QTreeWidgetItem([f.name, "វីដេអូ", fmt_size(f.stat().st_size), ""])
                    child.setData(0, Qt.UserRole, f.path)
                    parent.addChild(child)
                tree.addTopLevelItem(parent)
            else:
                item = QTreeWidgetItem([e.name, kinds[os.path.splitext(e.name)[1].lower()],
                                        fmt_size(e.stat().st_size), when])
                item.setData(0, Qt.UserRole, e.path)
                item.setToolTip(0, e.name)
                tree.addTopLevelItem(item)

    def _selected_paths(self):
        return [i.data(0, Qt.UserRole) for i in self.files_tree.selectedItems()]

    def _selected_videos(self):
        """វីដេអូដែលបានជ្រើស — ជ្រើសថត = វីដេអូទាំងអស់ក្នុងថត"""
        out = []
        for p in self._selected_paths():
            if os.path.isdir(p):
                out += sorted((os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(".mp4")),
                              key=lambda x: natural_key(os.path.basename(x)))
            elif os.path.splitext(p)[1].lower() in VIDEO_EXT:
                out.append(p)
        return out

    def _files_open(self):
        for p in self._selected_paths()[:5]:
            open_path(p)

    def _files_reveal(self):
        paths = self._selected_paths()
        reveal(paths[0] if paths else OUTPUT_DIR)

    def _files_merge(self):
        videos = self._selected_videos()
        if len(videos) < 2:
            return self.set_status("ជ្រើសថតដែលមានផ្នែកច្រើន ឬជ្រើសវីដេអូយ៉ាងតិច 2 (Ctrl+ចុច)", True)
        self.run_merge(videos)

    def _files_mute(self):
        videos = self._selected_videos()
        if not videos:
            return self.set_status("សូមជ្រើសវីដេអូ", True)
        self.run_mute(videos)

    def _files_use_srt(self):
        srts = [p for p in self._selected_paths() if p.lower().endswith(".srt")]
        if not srts:
            return self.set_status("សូមជ្រើសឯកសារ .srt", True)
        self.srt_path.setText(srts[0])
        self.load_srt_file(srts[0])
        self.tabs.setCurrentIndex(1)

    # ================= settings =================
    _CHECKS = ["auto_gender", "strip_parens", "fit", "orig_mute", "stt_gender", "stt_dub", "stt_video",
               "batch_keep_merged"]
    _SPINS = ["max_speed", "workers", "orig_volume", "part_minutes"]
    # key ថ្មី → លំនាំដើមថ្មី (Mute សំឡេងដើម = បើក) មិនត្រូវជាន់ដោយតម្លៃចាស់ដែលបានរក្សាទុក
    _KEYS = {"orig_mute": "orig_mute_v2"}

    def _load_settings(self):
        s = self.settings
        self.api_key.setText(s.value("api_key", ""))
        (self.rb_gemini if s.value("engine") == "gemini" else self.rb_edge).setChecked(True)
        for name, slider in (("rate", self.rate), ("pitch", self.pitch), ("volume", self.volume)):
            slider[1].setValue(int(s.value(name, 0)))
        self.gemini_model.setCurrentText(s.value("gemini_model", backend.GEMINI_MODELS[0]))
        self._select_data(self.gemini_voice, s.value("gemini_voice", "Kore"))
        self.gemini_style.setText(s.value("gemini_style", ""))
        self.stt_model.setCurrentText(s.value("stt_model", transcribe.MODELS[0]))
        i = self.stt_target.findData(s.value("stt_target", "km"))
        self.stt_target.setCurrentIndex(max(i, 0))
        self.audio_format.setCurrentText(s.value("audio_format", "mp3"))
        for name in self._CHECKS:
            key = self._KEYS.get(name, name)
            if s.contains(key):
                getattr(self, name).setChecked(s.value(key) in (True, "true", "1"))
        for name in self._SPINS:
            if s.contains(name):
                widget = getattr(self, name)
                widget.setValue(type(widget.value())(float(s.value(name))))
        geom = s.value("geometry")
        if geom is not None:
            self.restoreGeometry(geom)

    def _save_settings(self):
        s = self.settings
        s.setValue("api_key", self.api_key.text().strip())
        s.setValue("engine", self.engine())
        for name, slider in (("rate", self.rate), ("pitch", self.pitch), ("volume", self.volume)):
            s.setValue(name, slider[1].value())
        if self.edge_lang.currentText():
            s.setValue("edge_lang", self.edge_lang.currentText())
        if self.edge_voice.currentData():
            s.setValue("edge_voice", self.edge_voice.currentData())
        s.setValue("gemini_model", self.gemini_model.currentText())
        s.setValue("gemini_voice", self.gemini_voice.currentData())
        s.setValue("gemini_style", self.gemini_style.text())
        for gender, combo in (("female", self.voice_female), ("male", self.voice_male)):
            if combo.currentData():
                s.setValue(f"voice_{gender}_{self.engine()}", combo.currentData())
        s.setValue("stt_model", self.stt_model.currentText())
        s.setValue("stt_target", self.stt_target.currentData())
        s.setValue("audio_format", self.audio_format.currentText())
        for name in self._CHECKS:
            s.setValue(self._KEYS.get(name, name), getattr(self, name).isChecked())
        for name in self._SPINS:
            s.setValue(name, getattr(self, name).value())
        s.setValue("geometry", self.saveGeometry())

    # ================= Update =================
    def _build_menu(self):
        menu = self.menuBar().addMenu("ជំនួយ")
        menu.addAction("🔄 ពិនិត្យ Update...", lambda: self.check_update(silent=False))
        menu.addAction("អំពីកម្មវិធី", self._about)

    def _about(self):
        cfg = updater.load_config()
        QMessageBox.information(self, "អំពីកម្មវិធី",
                                f"Khmer TTS Studio  v{updater.local_version()}\n\n"
                                f"Update ពី: {('github.com/' + cfg['repo']) if cfg else 'មិនទាន់កំណត់'}")

    def check_update(self, silent=True):
        """silent=True — ពេលបើកកម្មវិធី: បង្ហាញតែពេលមាន Update ថ្មីប៉ុណ្ណោះ"""
        if not updater.load_config():
            if not silent:
                QMessageBox.information(self, "Update", "កម្មវិធីនេះមិនទាន់ភ្ជាប់ទៅ GitHub ទេ។\n"
                                                        "(ម្ចាស់កម្មវិធីត្រូវដំណើរការ publish_update.bat ជាមុន)")
            return
        worker = Worker(updater.check)
        worker.done.connect(lambda info: self._update_found(info, silent))
        worker.failed.connect(lambda e: None if silent else QMessageBox.warning(self, "Update", e))
        self._start_worker(worker)
        if not silent:
            self.set_status("កំពុងពិនិត្យ Update...")

    def _update_found(self, info, silent):
        if not info["available"]:
            if not silent:
                self.set_status(f"កម្មវិធីនេះជាកំណែចុងក្រោយហើយ (v{info['current']})")
                QMessageBox.information(self, "Update", f"អ្នកកំពុងប្រើកំណែចុងក្រោយហើយ (v{info['current']})។")
            return
        if self.busy:
            self.set_status(f"🔔 មាន Update v{info['latest']} — នឹងសួរម្តងទៀតពេលការងារចប់ (ជំនួយ → ពិនិត្យ Update)")
            return
        notes = info["notes"] or "—"
        answer = QMessageBox.question(
            self, "មាន Update ថ្មី",
            f"កំណែថ្មី v{info['latest']} (អ្នកកំពុងប្រើ v{info['current']})\n\n"
            f"អ្វីដែលថ្មី:\n{notes}\n\n"
            f"ឯកសារដែលត្រូវទាញយក: {len(info['changed'])}\n\nUpdate ឥឡូវនេះ?")
        if answer == QMessageBox.Yes:
            self._install_update(info)

    def _install_update(self, info):
        self.begin_stages(f"Update v{info['current']} → v{info['latest']}", ["ទាញយក", "ដំឡើង"])
        self.enter_stage("ទាញយក")

        def work():
            count = updater.download_and_apply(info)
            if "requirements.txt" in info["changed"]:  # library ថ្មី
                subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                                os.path.join(updater.APP_DIR, "requirements.txt")],
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return count

        def done(count):
            self.enter_stage("ដំឡើង")
            self.set_result(None, f"Update រួចរាល់ — v{info['latest']} ({count} ឯកសារ)")
            if QMessageBox.question(self, "Update រួចរាល់",
                                    f"បាន Update ទៅ v{info['latest']} ហើយ។\n"
                                    "បើកកម្មវិធីឡើងវិញឥឡូវនេះ?") == QMessageBox.Yes:
                self._restart()
        self.run_sync(work, "កំពុងទាញយក Update...", done)

    def _restart(self):
        self._save_settings()
        self.busy = False
        subprocess.Popen([sys.executable, os.path.abspath(__file__)], cwd=updater.APP_DIR,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        QApplication.quit()

    def closeEvent(self, event):
        if self.busy and QMessageBox.question(
                self, "កំពុងដំណើរការ", "ការងារកំពុងដំណើរការ។ បិទកម្មវិធី ហើយបោះបង់ការងារនេះ?") != QMessageBox.Yes:
            event.ignore()
            return
        self._save_settings()
        event.accept()


def _excepthook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    with open(os.path.join(backend.BASE_DIR, "gui_error.log"), "a", encoding="utf-8") as f:
        f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n")
    QMessageBox.critical(None, "កំហុស", f"{exc}\n\n(លម្អិតក្នុង gui_error.log)")


def setup_font(qt_app):
    # Kantumruy Pro — ផ្ទុកពីថត fonts/ ដើម្បីឱ្យដំណើរការសូម្បីតែលើកុំព្យូទ័រដែលមិនបានដំឡើង font នេះ
    fonts_dir = os.path.join(backend.BASE_DIR, "fonts")
    if os.path.isdir(fonts_dir):
        for f in os.listdir(fonts_dir):
            if f.lower().endswith((".ttf", ".otf")):
                QFontDatabase.addApplicationFont(os.path.join(fonts_dir, f))
    # Kantumruy Pro គ្មាន emoji និងសញ្ញាខ្លះ (→ ▶ 🔇) → កំណត់ font បម្រុងឱ្យច្បាស់
    font = QFont()
    font.setFamilies(["Kantumruy Pro", "Khmer UI", "Segoe UI", "Segoe UI Symbol", "Segoe UI Emoji"])
    font.setPointSize(10)
    qt_app.setFont(font)
    return font


def main():
    updater.apply_pending()  # ឯកសារ Update ដែលមិនទាន់បានដាក់ពីលើកមុន
    sys.excepthook = _excepthook
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    qt_app = QApplication(sys.argv)
    qt_app.setStyle("Fusion")
    setup_font(qt_app)
    window = MainWindow()
    window.show()
    sys.exit(qt_app.exec_())


if __name__ == "__main__":
    main()
