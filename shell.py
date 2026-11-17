"""🧭 Studio shell — របារចូលមុខងារខាងឆ្វេង (Sidebar) ចែកជាក្រុម (បត់/លាបាន) ឱ្យចូលគ្រប់មុខងារក្នុងមួយចុច។

MainWindow ភ្ជាប់ signal `selected(key)` → បើកទំព័រ · `set_current(key)` ឱ្យស្តាំ/ឆ្វេងស៉ីគ្នាពេលទំព័រប្តូរដោយវិធីផ្សេង។
ក្រុមដែលបត់ រក្សាទុកក្នុង QSettings (nav_collapsed) · ប្លង់ចាស់ (Classic) = លាក់ Sidebar ទាំងមូល (ម៉ឺនុយ 🧭 ប្លង់)។
"""
from PyQt5.QtCore import QSettings, Qt, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import QFrame, QLabel, QPushButton, QScrollArea, QShortcut, QSizePolicy, QVBoxLayout, QWidget

import theme

# (key, icon+ឈ្មោះ, ពន្យល់) តាមក្រុម — key ត្រូវនឹង MainWindow._nav_go
GROUPS = [
    ("prod", "⚡ ផលិតកម្ម", [
        ("auto", "✨ Auto Dub", "រឿងចិន → វីដេអូខ្មែរ ក្នុងមួយចុច (ស្តាប់ · បកប្រែ · សំឡេង · កាត់)"),
        ("logo", "🏷 Logo & Ads", "ដាក់ Logo / Lower third / Ads លើវីដេអូដែលមានស្រាប់"),
        ("batch", "🗂 Auto Batch", "ផ្ទាំង Auto លម្អិត (ចាស់) — ដំណើរការរឿងច្រើន ជាមួយជម្រើសគ្រប់យ៉ាង"),
    ]),
    ("audio", "🎙 សំឡេង", [
        ("text", "✍️ អត្ថបទ → សំឡេង", "វាយអក្សរ → សំឡេងខ្មែរ (Edge TTS / Gemini TTS)"),
        ("stt", "🎧 សំឡេង → SRT", "ស្តាប់ឯកសារសំឡេង/វីដេអូ → អត្ថបទ SRT + បកប្រែ"),
    ]),
    ("video", "🎬 វីដេអូ", [
        ("srt", "🎬 SRT → វីដេអូ", "បញ្ចូលសំឡេងខ្មែរតាម SRT ទៅក្នុងវីដេអូ"),
        ("vsrt", "📝 វីដេអូ → SRT", "ដកអត្ថបទពីវីដេអូ (Whisper / SenseVoice / Gemini)"),
        ("merge", "🧩 Merge", "ភ្ជាប់វីដេអូច្រើនជាមួយគ្នា (ចម្លងផ្ទាល់ ឬ encode)"),
        ("brand", "🎨 Logo ប៉ុណ្ណោះ", "ដាក់ Logo លើវីដេអូដោយមិនបកប្រែ"),
    ]),
    ("system", "📁 ឯកសារ & ប្រព័ន្ធ", [
        ("files", "📁 លទ្ធផល", "ឯកសារដែលបានបង្កើត · បើក / លុប"),
        ("output", "📂 បើកថត Output", "បើកថតលទ្ធផលក្នុង Explorer"),
        ("settings", "⚙️ ការកំណត់", "Key · Server · សំឡេង · Google Drive · ថត Output"),
        ("router", "🔀 Gemini Router", "Dashboard: Live Traffic · គណនី/Profile · Request Log · បើក/បិទ Direct"),
        ("check", "🩺 ពិនិត្យថាប្រើបាន", "សាកអ៊ីនធឺណិត · ffmpeg · GPU · key · Whisper"),
    ]),
]
LABELS = {k: label for _, _, items in GROUPS for k, label, _ in items}
ACTIONS = ("output", "check", "settings", "router")  # សកម្មភាព (មិនមែនទំព័រ) → មិន checked


class SideNav(QFrame):
    selected = pyqtSignal(str)
    W = 222

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sideNav")
        self.setFixedWidth(self.W)
        self.buttons, self.groups = {}, {}  # key → ប៊ូតុង · gid → (ប៊ូតុងក្បាល, [ប៊ូតុងធាតុ])
        self.settings = QSettings("KhmerTTS", "Studio")
        collapsed = set(str(self.settings.value("nav_collapsed", "") or "").split(","))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("navScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(8, 10, 8, 8)
        lay.setSpacing(1)
        n = 0
        for gid, group, items in GROUPS:
            head = QPushButton(group)
            head.setObjectName("navGroup")
            head.setCursor(Qt.PointingHandCursor)
            head.setToolTip("ចុច → បត់ / លាក្រុមនេះ")
            if n:
                lay.addSpacing(6)
            lay.addWidget(head)
            btns = []
            for key, label, tip in items:
                n += 1
                b = QPushButton(label.replace("&", "&&"))  # & តែមួយ = mnemonic (អក្សរបាត់)
                b.setObjectName("navItem")
                b.setCheckable(key not in ACTIONS)
                b.setCursor(Qt.PointingHandCursor)
                b.setToolTip(f"{tip}" + (f"\nCtrl+{n}" if n <= 9 else ""))
                b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                b.clicked.connect(lambda _=False, k=key: self._pick(k))
                lay.addWidget(b)
                self.buttons[key] = b
                btns.append(b)
                if n <= 9 and parent is not None:
                    QShortcut(QKeySequence(f"Ctrl+{n}"), parent, activated=lambda k=key: self._pick(k))
            self.groups[gid] = (head, btns)
            head.clicked.connect(lambda _=False, g=gid: self.toggle_group(g))
            self._apply_group(gid, gid in collapsed)
        lay.addStretch()
        self.hint = QLabel("Ctrl+1…9 ប្តូរទំព័រ · ចុចក្បាលក្រុម = បត់")
        self.hint.setObjectName("navHint")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

    # ---------- ក្រុម បត់ / លា ----------
    def _apply_group(self, gid, collapsed):
        head, btns = self.groups[gid]
        title = next(g for i, g, _ in GROUPS if i == gid)
        head.setText(("▸ " if collapsed else "▾ ") + title)
        head.setProperty("collapsed", collapsed)
        for b in btns:
            b.setVisible(not collapsed)

    def toggle_group(self, gid):
        head, _ = self.groups[gid]
        now = not bool(head.property("collapsed"))
        self._apply_group(gid, now)
        collapsed = [g for g, (h, _) in self.groups.items() if h.property("collapsed")]
        self.settings.setValue("nav_collapsed", ",".join(collapsed))
        head.style().unpolish(head)
        head.style().polish(head)

    def _pick(self, key):
        if self.buttons[key].isCheckable():
            self.set_current(key)
        self.selected.emit(key)

    def set_current(self, key):
        """បង្ហាញទំព័របច្ចុប្បន្ន (មិន emit) · ក្រុមដែលមានទំព័រនេះ បើបត់ → លាឱ្យឃើញ"""
        for k, b in self.buttons.items():
            if b.isCheckable():
                b.setChecked(k == key)
        for gid, _, items in GROUPS:
            if any(k == key for k, _, _ in items) and self.groups[gid][0].property("collapsed"):
                self.toggle_group(gid)

    def restyle(self):
        c = theme.C
        self.setStyleSheet(theme.hackify(f"""
QFrame#sideNav {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 18px; }}
QScrollArea#navScroll, QScrollArea#navScroll > QWidget > QWidget {{ background: transparent; }}
QPushButton#navGroup {{ background: transparent; border: none; color: {c['faint']}; font-size: 8pt; font-weight: bold;
    padding: 3px 6px; text-align: left; letter-spacing: 1px; }}
QPushButton#navGroup:hover {{ color: {c['accent']}; background: transparent; border: none; }}
QPushButton#navGroup[collapsed="true"] {{ color: {c['muted']}; }}
QLabel#navHint {{ color: {c['faint']}; font-size: 8pt; padding: 4px; }}
QPushButton#navItem {{ background: transparent; border: none; border-radius: 8px; text-align: left;
    padding: 5px 10px; color: {c['text']}; font-weight: bold; }}
QPushButton#navItem:hover {{ background: {c['hover']}; border: none; }}
QPushButton#navItem:pressed {{ background: {c['border']}; padding-top: 5px; }}
QPushButton#navItem:checked {{ background: {c['lcd']}; color: {c['accent']}; border: none;
    border-left: 3px solid {c['accent']}; padding-left: 7px; }}
"""))
