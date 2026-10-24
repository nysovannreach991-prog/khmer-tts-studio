"""📂 Explorer ខាងឆ្វេង — រកមើល Folder / វីដេអូ ក្នុងកម្មវិធីផ្ទាល់ (មិនបាច់បើកប្រអប់ "ជ្រើស...")។
ចុចពីរដង ឬ ➕ = ដាក់ចូលផ្ទាំងដែលកំពុងបើក · អូសទម្លាក់ចូលផ្ទាំងក៏បាន"""
import ctypes
import hashlib
import os
import re
import subprocess
import tempfile
import threading

from PyQt5.QtCore import QDir, QSortFilterProxyModel, Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QFileSystemModel, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QTreeView, QVBoxLayout, QWidget,
)

VIDEO = [".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".ts"]
AUDIO = [".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"]
IMAGE = [".png", ".jpg", ".jpeg", ".webp", ".bmp"]
KINDS = [("media", "🎬 វីដេអូ + SRT", VIDEO + [".srt"]), ("video", "🎬 វីដេអូ", VIDEO), ("srt", "📄 SRT", [".srt"]),
         ("image", "🖼 រូបភាព", IMAGE), ("audio", "🔊 សំឡេង", AUDIO),
         ("all", "ឯកសារទាំងអស់", VIDEO + AUDIO + IMAGE + [".srt", ".txt"])]
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
THUMB_DIR = os.path.join(tempfile.gettempdir(), "aiteam1_thumbs")


def _known_folder(name):
    """Desktop / Videos ... (OneDrive ក៏ស្គាល់)"""
    home = os.path.expanduser("~")
    for base in (os.path.join(home, "OneDrive"), home):
        path = os.path.join(base, name)
        if os.path.isdir(path):
            return path
    return None


def places():
    """[(ឈ្មោះ, path)] — កន្លែងញឹកញាប់ + ដ្រាយ"""
    out = [(f"{icon} {label}", p) for icon, label, name in
           [("🖥", "Desktop", "Desktop"), ("🎞", "Videos", "Videos"), ("⬇", "Downloads", "Downloads"),
            ("📄", "Documents", "Documents")] if (p := _known_folder(name))]
    for drive in QDir.drives():
        root = os.path.normpath(drive.absoluteFilePath())
        label = ""
        try:  # ឈ្មោះដ្រាយ (ឧ. "Data")
            buf = ctypes.create_unicode_buffer(261)
            if ctypes.windll.kernel32.GetVolumeInformationW(root, buf, 261, None, None, None, None, 0):
                label = buf.value
        except Exception:  # noqa: BLE001
            pass
        out.append((f"💽 {label + ' ' if label else ''}({root.rstrip(os.sep)})", root))
    return out


class _Filter(QSortFilterProxyModel):
    """Folder មុន ឯកសារក្រោយ · តម្រៀបលេខតាមធម្មជាតិ (EP2 មុន EP10)"""

    def lessThan(self, a, b):
        m = self.sourceModel()
        da, db = m.isDir(a), m.isDir(b)
        if da != db:
            return da if self.sortOrder() == Qt.AscendingOrder else db
        if a.column() == 0:
            key = lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]  # noqa: E731
            return key(m.fileName(a)) < key(m.fileName(b))
        return super().lessThan(a, b)


class ExplorerPanel(QWidget):
    use = pyqtSignal(list)          # ដាក់ចូលផ្ទាំងដែលកំពុងបើក
    folder_changed = pyqtSignal(str)
    _thumb_ready = pyqtSignal(int, str, str)

    def __init__(self, start_dir=""):
        super().__init__()
        self.setMinimumWidth(210)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        title = QLabel("📂 Explorer")
        title.setStyleSheet("font-weight:bold")
        top = QHBoxLayout()
        top.addWidget(title)
        top.addStretch()
        self.btn_up = QPushButton("⬆")
        self.btn_up.setToolTip("ទៅ Folder មេ")
        self.btn_up.clicked.connect(self.go_up)
        browse = QPushButton("…")
        browse.setToolTip("ជ្រើស Folder ផ្សេង")
        browse.clicked.connect(self._browse)
        for b in (self.btn_up, browse):
            b.setFixedWidth(36)
            b.setStyleSheet("padding:0")
            top.addWidget(b)
        lay.addLayout(top)

        self.place = QComboBox()
        self.place.setToolTip("កន្លែងញឹកញាប់ និងដ្រាយ")
        self.place.setPlaceholderText("📍 ទៅកន្លែង... (Desktop, Videos, ដ្រាយ)")
        self.place.activated.connect(lambda i: self.go(self.place.itemData(i)))
        lay.addWidget(self.place)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("ផ្លូវ Folder — វាយ ហើយចុច Enter")
        self.path_edit.returnPressed.connect(lambda: self.go(self.path_edit.text().strip().strip('"')))
        lay.addWidget(self.path_edit)

        row = QHBoxLayout()
        self.kind = QComboBox()
        for key, label, _ in KINDS:
            self.kind.addItem(label, key)
        self.kind.currentIndexChanged.connect(lambda _: self._apply_filter())
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 ស្វែងរក")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self._apply_filter())
        row.addWidget(self.kind)
        row.addWidget(self.search, 1)
        lay.addLayout(row)

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot | QDir.Drives)
        self.model.setNameFilterDisables(False)  # លាក់ឯកសារដែលមិនត្រូវប្រភេទ (មិនមែនគ្រាន់តែប្រផេះ)
        self.model.setRootPath("")
        self.proxy = _Filter(self)
        self.proxy.setSourceModel(self.model)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragOnly)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        self.tree.setColumnHidden(2, True)  # ប្រភេទ
        self.tree.setColumnHidden(3, True)  # កាលបរិច្ឆេទ
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, self.tree.header().Stretch)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.doubleClicked.connect(self._double_clicked)
        self.tree.selectionModel().selectionChanged.connect(lambda *_: self._selection_changed())
        lay.addWidget(self.tree, 1)

        self.thumb = QLabel()
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setFixedHeight(120)
        self.thumb.hide()
        lay.addWidget(self.thumb)
        self.info = QLabel("ចុចពីរដង ឬអូសទម្លាក់ ដើម្បីដាក់ចូល · Ctrl/Shift = ជ្រើសច្រើន")
        self.info.setProperty("role", "muted")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.btn_use = QPushButton("➕ ដាក់ចូល")
        self.btn_use.setObjectName("primary")
        self.btn_use.setCursor(Qt.PointingHandCursor)
        self.btn_use.clicked.connect(lambda: self.use.emit(self.selected()) if self.selected() else None)
        lay.addWidget(self.btn_use)

        self._gen = 0
        self._thumb_ready.connect(self._show_thumb)
        self._apply_filter()
        self.refresh_places()
        self.go(start_dir if start_dir and os.path.isdir(start_dir) else (_known_folder("Videos") or
                                                                         _known_folder("Desktop") or "C:\\"))

    # ---------- navigation ----------
    def refresh_places(self, extra=()):
        current = self.place.currentData()
        self.place.blockSignals(True)
        self.place.clear()
        self.place.addItem("", None)  # ជួរទី 0 = Folder បច្ចុប្បន្ន (ពេលវាមិនមែនជាកន្លែងក្នុងបញ្ជី)
        for label, path in [*extra, *places()]:
            self.place.addItem(label, path)
        self.place.blockSignals(False)
        self._sync_place(current)

    def _sync_place(self, folder):
        i = self.place.findData(folder, flags=Qt.MatchExactly) if folder else -1
        if i <= 0:
            self.place.setItemText(0, f"📁 {os.path.basename(folder) or folder}" if folder else "📍 ទៅកន្លែង...")
            self.place.setItemData(0, folder)
            i = 0
        self.place.setCurrentIndex(i)

    def current_dir(self):
        return self._dir

    def go(self, folder):
        if not folder or not os.path.isdir(folder):
            return
        self._dir = os.path.normpath(folder)
        self.model.setRootPath(self._dir)
        self.tree.setRootIndex(self.proxy.mapFromSource(self.model.index(self._dir)))
        self.path_edit.setText(self._dir)
        self.btn_up.setEnabled(os.path.dirname(self._dir) != self._dir)
        self._sync_place(self._dir)
        self.folder_changed.emit(self._dir)

    def go_up(self):
        parent = os.path.dirname(self._dir)
        if parent and parent != self._dir:
            child = self._dir
            self.go(parent)
            index = self.proxy.mapFromSource(self.model.index(child))
            self.tree.setCurrentIndex(index)
            self.tree.scrollTo(index)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "ជ្រើស Folder", self._dir)
        if folder:
            self.go(folder)

    def _apply_filter(self):
        exts = next(e for k, _, e in KINDS if k == self.kind.currentData())
        text = self.search.text().strip().replace("*", "").replace("?", "")
        self.model.setNameFilters([f"*{text}*{e}" for e in exts])

    # ---------- selection ----------
    def _path(self, proxy_index):
        return os.path.normpath(self.model.filePath(self.proxy.mapToSource(proxy_index)))

    def selected(self):
        rows = self.tree.selectionModel().selectedRows(0)
        return [self._path(i) for i in sorted(rows, key=lambda i: (i.parent().row(), i.row()))]

    def _double_clicked(self, index):
        path = self._path(index)
        if os.path.isdir(path):
            self.go(path)
        else:
            self.use.emit([path])

    def _menu(self, pos):
        paths = self.selected()
        if not paths:
            return
        menu = QMenu(self)
        menu.addAction(self.btn_use.text(), lambda: self.use.emit(paths))
        if len(paths) == 1:
            p = paths[0]
            if os.path.isdir(p):
                menu.addAction("📂 បើក Folder នេះ", lambda: self.go(p))
            else:
                menu.addAction("▶ បើកមើល", lambda: os.startfile(p))
            menu.addAction("🗂 បង្ហាញក្នុង Windows Explorer",
                           lambda: subprocess.Popen(["explorer", "/select,", p]))
        menu.exec_(self.tree.viewport().mapToGlobal(pos))

    def _selection_changed(self):
        paths = self.selected()
        self._gen += 1
        if len(paths) != 1 or os.path.isdir(paths[0]):
            self.thumb.hide()
            if len(paths) > 1:
                self.info.setText(f"បានជ្រើស {len(paths)} · ចុច {self.btn_use.text()}")
            else:
                self.info.setText("ចុចពីរដង ឬអូសទម្លាក់ ដើម្បីដាក់ចូល · Ctrl/Shift = ជ្រើសច្រើន")
            return
        path = paths[0]
        ext = os.path.splitext(path)[1].lower()
        size = os.path.getsize(path)
        self.info.setText(f"{os.path.basename(path)} · {size / 1e6:.1f} MB")
        if ext in VIDEO or ext in IMAGE:
            gen = self._gen
            threading.Thread(target=self._make_thumb, args=(gen, path, ext in IMAGE), daemon=True).start()
        else:
            self.thumb.hide()

    def _make_thumb(self, gen, path, is_image):
        """រូបតូច + ព័ត៌មាន (ffmpeg ក្នុង thread — មិនធ្វើឱ្យកម្មវិធីគាំង)"""
        info = ""
        try:
            import overlay
            m = overlay.media_info(path)
            info = f"{m['width']}×{m['height']}"
            if not is_image:
                d = int(m["duration"])
                info += f" · {d // 3600}:{d // 60 % 60:02d}:{d % 60:02d}" if d >= 3600 else f" · {d // 60}:{d % 60:02d}"
        except Exception:  # noqa: BLE001
            pass
        png = path
        if not is_image:
            os.makedirs(THUMB_DIR, exist_ok=True)
            key = hashlib.sha1(f"{path}|{os.path.getmtime(path)}".encode("utf-8")).hexdigest()[:16]
            png = os.path.join(THUMB_DIR, key + ".jpg")
            if not os.path.exists(png):
                try:
                    dur = overlay.media_info(path)["duration"]
                except Exception:  # noqa: BLE001
                    dur = 0
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{min(dur * 0.1, 30):.2f}",
                                "-i", path, "-frames:v", "1", "-vf", "scale=320:-2", png],
                               capture_output=True, creationflags=_NO_WINDOW)
        self._thumb_ready.emit(gen, png if os.path.exists(png) else "", info)

    def _show_thumb(self, gen, png, info):
        if gen != self._gen:
            return  # បានជ្រើសឯកសារផ្សេងរួចហើយ
        if info:
            self.info.setText(f"{self.info.text()} · {info}")
        pix = QPixmap(png) if png else QPixmap()
        if pix.isNull():
            self.thumb.hide()
            return
        self.thumb.setPixmap(pix.scaled(self.thumb.width() or 260, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.thumb.show()

    def set_target(self, text, enabled=True):
        """ឈ្មោះផ្ទាំងដែលនឹងទទួលឯកសារ (បង្ហាញលើប៊ូតុង)"""
        self.btn_use.setText(f"➕ ដាក់ចូល {text}")
        self.btn_use.setEnabled(enabled)

