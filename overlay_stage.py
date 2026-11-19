"""ផ្ទាំង Preview អន្តរកម្មសម្រាប់ Logo & Ads — លេងវីដេអូ + Logo / Logo 2 / Lower third ដែលអូស និងប្តូរទំហំបានផ្ទាល់លើរូប។

* ផ្ទៃខាងក្រោយ = វីដេអូដែលជ្រើស (▶ លេង / ផ្អាក / រំកិល) ឬផ្ទៃពណ៌តាម Aspect ratio
* ចុចលើ Logo / Lower third = ជ្រើស (selected signal → បើកការកំណត់របស់វា) · អូស = ផ្លាស់ទី (សារ៉េ X / Y)
  · ជ្រុងស្តាំក្រោម = ប្តូរទំហំ — តម្លៃសរសេរត្រឡប់ទៅ spin box របស់ host.ov[prefix] ដូច្នេះលទ្ធផល Export ដូចដែលឃើញ
* រូបរបស់ Logo នីមួយៗ = ស៊ុមមួយពី ffmpeg (លុបពណ៌បៃតងរួច) — វីដេអូ Logo មិនលេងចលនាក្នុងផ្ទាំងនេះទេ
"""
import hashlib
import os
import subprocess
import tempfile
import threading

from PyQt5.QtCore import QPointF, QRectF, QSizeF, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen, QPixmap
from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
from PyQt5.QtMultimediaWidgets import QGraphicsVideoItem
from PyQt5.QtWidgets import (
    QComboBox, QFrame, QGraphicsItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)

import overlay

LABELS = {"lg": "Logo 1", "lg2": "Logo 2", "lt": "Lower third"}
ACCENT = "#F5C451"
STILL_W = 720  # ទទឹងរូបរបស់ Logo ក្នុង Preview (px)


def _fmt(ms):
    s = int(ms / 1000)
    return f"{s // 60}:{s % 60:02d}"


def still_path(path, key, strength):
    """រូប PNG ថ្លារបស់ Logo / Lower third (លុបពណ៌រួច) — cache តាមឯកសារ + ការកំណត់លុបពណ៌"""
    info = overlay.media_info(path)
    mode = overlay._key_mode({"key": key}, info)
    tag = hashlib.md5(f"{path}|{os.path.getmtime(path)}|{mode}|{strength:.3f}".encode("utf-8")).hexdigest()[:16]
    out = os.path.join(tempfile.gettempdir(), f"dubber_stage_{tag}.png")
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        return out
    chain = []
    if mode in overlay.KEY_COLOR:
        sim = min(max(float(strength), 0.01), 0.6)
        chain += [f"chromakey={overlay.KEY_COLOR[mode]}:{sim:.3f}:0.08", f"despill=type={mode}"]
    chain += ["format=rgba", f"scale={STILL_W}:-1"]
    seek = [] if info["image"] else ["-ss", f"{min(info['duration'] * 0.6, max(info['duration'] - 0.1, 0)):.3f}"]
    dec = ["-c:v", info["decoder"]] if info["decoder"] else []
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *seek, *dec, "-i", path, "-vf", ",".join(chain),
                           "-frames:v", "1", "-update", "1", out], capture_output=True, creationflags=overlay._NO_WINDOW,
                          timeout=120)
    if proc.returncode != 0 or not os.path.isfile(out):
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-200:])
    return out


class OvItem(QGraphicsItem):
    """Logo / Lower third មួយលើឆាក — អូសបាន · ជ្រុងស្តាំក្រោមប្តូរទំហំ"""

    def __init__(self, stage, prefix):
        super().__init__()
        self.stage, self.prefix = stage, prefix
        self.pix, self.w, self.h = QPixmap(), 100.0, 50.0
        self._mode, self._grab = None, QPointF()
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(10)

    def handle(self):
        s = 16 / max(self.stage.view_scale(), 1e-6)
        return QRectF(self.w - s, self.h - s, s, s)

    def boundingRect(self):
        s = 20 / max(self.stage.view_scale(), 1e-6)
        return QRectF(-s, -s * 2, self.w + 2 * s, self.h + 3 * s)

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if not self.pix.isNull():
            p.setOpacity(self.stage.value(self.prefix, "opacity", 100) / 100)
            p.drawPixmap(QRectF(0, 0, self.w, self.h), self.pix, QRectF(self.pix.rect()))
            p.setOpacity(1)
        k = 1 / max(self.stage.view_scale(), 1e-6)
        if self.isSelected() or self.pix.isNull():
            pen = QPen(QColor(ACCENT), 2 * k, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(0, 0, self.w, self.h))
        if self.isSelected():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT))
            p.drawRect(self.handle())
            f = p.font()
            f.setPixelSize(max(int(12 * k), 1))
            f.setBold(True)
            p.setFont(f)
            tag = QRectF(0, -18 * k, 110 * k, 16 * k)
            p.drawRect(tag)
            p.setPen(QColor("#0B0D1A"))
            p.drawText(tag, Qt.AlignCenter, LABELS.get(self.prefix, self.prefix))

    def hoverMoveEvent(self, e):
        self.setCursor(Qt.SizeFDiagCursor if self.isSelected() and self.handle().contains(e.pos()) else Qt.SizeAllCursor)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return e.ignore()
        self.scene().clearSelection()
        self.setSelected(True)
        self._mode = "size" if self.handle().contains(e.pos()) else "move"
        self._grab = e.pos()
        self.stage.selected.emit(self.prefix)
        e.accept()

    def mouseMoveEvent(self, e):
        if self._mode == "move":
            self.setPos(self.pos() + e.pos() - self._grab)
        elif self._mode == "size":
            W = self.stage.W
            w = min(max(e.pos().x(), W * 0.02), W * 4)
            self.prepareGeometryChange()
            self.h, self.w = w * self.h / max(self.w, 1e-6), w
        if self._mode:
            self.stage.item_changed(self)
            self.update()

    def mouseReleaseEvent(self, e):
        self._mode = None
        self.stage.item_changed(self, final=True)


class StageView(QGraphicsView):
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)


class OverlayStage(QWidget):
    selected = pyqtSignal(str)   # prefix ដែលទើបចុច ("lg" / "lg2" / "lt")
    more = pyqtSignal(str)       # ⚙ បើកការកំណត់ពេញរបស់ prefix
    stills = pyqtSignal(object)  # (prefix, token, path ឬ None) ពី thread

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.W, self.H = 1280.0, 720.0
        self.video, self._busy, self._tokens, self.items = "", False, {}, {}
        self.scene = QGraphicsScene(self)
        self.view = StageView(self.scene)
        self.view.setObjectName("stageView")
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setBackgroundBrush(QColor("#05060D"))
        self.view.setMinimumSize(280, 158)
        self.back = self.scene.addRect(QRectF(0, 0, self.W, self.H), QPen(Qt.NoPen))
        self.back.setZValue(0)
        self.vitem = QGraphicsVideoItem()
        self.vitem.setZValue(1)
        self.vitem.setAspectRatioMode(Qt.KeepAspectRatio)
        self.scene.addItem(self.vitem)
        self.player = QMediaPlayer(self, QMediaPlayer.VideoSurface)
        self.player.setVideoOutput(self.vitem)
        self.player.setNotifyInterval(100)
        self.player.setVolume(60)
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(lambda d: self.seek.setRange(0, int(d)))
        self.player.stateChanged.connect(lambda st: self.btn_play.setText("⏸" if st == QMediaPlayer.PlayingState else "▶"))
        self.player.mediaStatusChanged.connect(self._on_media)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedWidth(46)
        self.btn_play.setCursor(Qt.PointingHandCursor)
        self.btn_play.setToolTip("លេង / ផ្អាកវីដេអូក្នុង Preview")
        self.btn_play.clicked.connect(self.toggle_play)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.lbl_time = QLabel("0:00 / 0:00")
        self.lbl_time.setProperty("role", "muted")
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(60)
        self.vol.setFixedWidth(80)
        self.vol.setToolTip("កម្រិតសំឡេង")
        self.vol.valueChanged.connect(self.player.setVolume)
        for wdg, stretch in ((self.btn_play, 0), (self.seek, 1), (self.lbl_time, 0), (QLabel("🔊"), 0), (self.vol, 0)):
            row.addWidget(wdg, stretch)
        lay.addLayout(row)
        # ---- ផ្ទាំងកំណត់តូចរបស់អ្វីដែលជ្រើស (ស្លាយ ↔ spin box ក្នុង ⚙ ការកំណត់)
        self.cur = ""
        self.insp = QFrame()
        self.insp.setObjectName("stageInspector")
        il = QHBoxLayout(self.insp)
        il.setContentsMargins(10, 6, 10, 6)
        il.setSpacing(8)
        self.insp_name = QLabel("")
        self.insp_name.setStyleSheet(f"font-weight:bold; color:{ACCENT}")
        il.addWidget(self.insp_name)
        self.insp_key = QComboBox()
        self.insp_key.setToolTip("លុបពណ៌ផ្ទៃខាងក្រោយ (Greenscreen)")
        self.insp_key.currentIndexChanged.connect(self._insp_key_changed)
        il.addWidget(self.insp_key)
        self.sl = {}
        for key, cap in (("strength", "Key"), ("opacity", "Opacity"), ("size", "Size")):
            lab = QLabel(cap)
            lab.setProperty("role", "muted")
            s = QSlider(Qt.Horizontal)
            s.setMinimumWidth(70)
            val = QLabel("")
            val.setMinimumWidth(44)
            s.valueChanged.connect(lambda v, k=key: self._insp_slider(k, v))
            self.sl[key] = (s, val)
            il.addWidget(lab)
            il.addWidget(s, 1)
            il.addWidget(val)
        btn = QPushButton("⚙")
        btn.setFixedWidth(40)
        btn.setToolTip("ការកំណត់ពេញ (ទីតាំង · គម្លាត · ពេលលេច ...)")
        btn.clicked.connect(lambda: self.more.emit(self.cur) if self.cur else None)
        il.addWidget(btn)
        self.insp.hide()
        lay.addWidget(self.insp)
        self.selected.connect(self._show_insp)
        self.hint = QLabel("ចុចលើ Logo / Lower third ដើម្បីជ្រើស · អូស = ផ្លាស់ទី · ជ្រុងស្តាំក្រោម = ប្តូរទំហំ")
        self.hint.setProperty("role", "muted")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        self.stills.connect(self._still_ready)
        self._layout_scene()

    # ---------- ឧបករណ៍ ----------
    def view_scale(self):
        return self.view.transform().m11()

    def value(self, prefix, key, default=0):
        try:
            return self.host.ov[prefix][key].value()
        except (KeyError, AttributeError):
            return default

    def _spec(self, prefix):
        c = self.host.ov[prefix]
        return {"pos": c["pos"].currentData() or "tr", "size": c["size"].value(), "margin": c["margin"].value(),
                "x": c["x"].value(), "y": c["y"].value()}

    def _base(self, spec, w, h):
        """ទីតាំងមុនសារ៉េ X / Y (ដូច overlay._xy)"""
        m = round(self.W * spec["margin"] / 100)
        pos = spec["pos"]
        return ({"l": m, "c": (self.W - w) / 2, "r": self.W - w - m}[pos[1]], {"t": m, "b": self.H - h - m}[pos[0]])

    def _layout_scene(self):
        self.scene.setSceneRect(0, 0, self.W, self.H)
        self.back.setRect(QRectF(0, 0, self.W, self.H))
        g = QLinearGradient(0, 0, self.W, self.H)
        g.setColorAt(0, QColor("#2a1f4d"))
        g.setColorAt(1, QColor("#5b2a55"))
        self.back.setBrush(QBrush(g))
        self.vitem.setSize(QSizeF(self.W, self.H))
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    # ---------- វីដេអូ ----------
    def set_source(self, video, canvas):
        """video = ឯកសារ (លេងបាន) · canvas = (w, h) ផ្ទៃពណ៌ (គ្មានវីដេអូ)"""
        video = video or ""
        size = None
        if video and os.path.isfile(video):
            try:
                info = overlay.media_info(video)
                size = (float(info["width"] or 1280), float(info["height"] or 720))
            except Exception:  # noqa: BLE001
                video = ""
        if not size:
            size = tuple(float(v) for v in (canvas or overlay.ASPECTS["16:9"]))
            video = ""
        changed = video != self.video
        if (self.W, self.H) != size:
            self.W, self.H = size
            self._layout_scene()
        if changed:
            self.video = video
            self.player.stop()
            self.vitem.setVisible(bool(video))
            self.player.setMedia(QMediaContent(QUrl.fromLocalFile(video)) if video else QMediaContent())
            for wdg in (self.btn_play, self.seek):
                wdg.setEnabled(bool(video))
            if video:
                self.player.pause()  # បង្ហាញស៊ុមដំបូង

    def _on_media(self, status):
        if status == QMediaPlayer.LoadedMedia and self.player.position() == 0 and self.player.duration() > 4000:
            self.player.setPosition(min(int(self.player.duration() * 0.2), 60000))  # កុំបង្ហាញស៊ុមខ្មៅដើមរឿង
        elif status == QMediaPlayer.EndOfMedia:
            self.player.setPosition(0)
            self.player.pause()

    def _on_pos(self, ms):
        if not self.seek.isSliderDown():
            self.seek.setValue(int(ms))
        self.lbl_time.setText(f"{_fmt(ms)} / {_fmt(self.player.duration())}")

    def toggle_play(self):
        if not self.video:
            return
        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def pause(self):
        if self.player.state() == QMediaPlayer.PlayingState:
            self.player.pause()

    def hideEvent(self, e):
        self.pause()
        super().hideEvent(e)

    def shutdown(self):
        self.player.stop()
        self.player.setMedia(QMediaContent())

    # ---------- Logo / Lower third ----------
    def sync(self):
        """អានការកំណត់ពី host.ov → បង្កើត / ដាក់ទីតាំង / លុប item (ហៅពេលការកំណត់ប្តូរ)"""
        if self._busy:
            return
        for prefix, c in self.host.ov.items():
            path = c["path"].text().strip()
            if not c["on"].isChecked() or not os.path.isfile(path):
                item = self.items.pop(prefix, None)
                if item:
                    self.scene.removeItem(item)
                continue
            item = self.items.get(prefix)
            if item is None:
                item = self.items[prefix] = OvItem(self, prefix)
                self.scene.addItem(item)
            token = (path, c["key"].currentData(), c["strength"].value())
            if self._tokens.get(prefix) != token:
                self._tokens[prefix] = token
                self._load_still(prefix, token)
            self._place(item)
        if self.cur and self.cur not in self.items:
            self.cur = ""
            self.insp.hide()
        self._insp_refresh()

    def _load_still(self, prefix, token):
        def run():
            try:
                out = still_path(token[0], token[1], token[2] / 100)
            except Exception:  # noqa: BLE001 — គ្មានរូប → នៅតែអូសប្រអប់បាន
                out = None
            self.stills.emit((prefix, token, out))
        threading.Thread(target=run, daemon=True).start()

    def _still_ready(self, res):
        prefix, token, path = res
        item = self.items.get(prefix)
        if item is None or self._tokens.get(prefix) != token:
            return
        item.pix = QPixmap(path) if path else QPixmap()
        self._place(item)

    def _place(self, item):
        if item._mode:  # កំពុងអូស — កុំរំខាន
            return
        spec = self._spec(item.prefix)
        ratio = item.pix.height() / item.pix.width() if not item.pix.isNull() and item.pix.width() else 0.4
        item.prepareGeometryChange()
        item.w = max(8.0, self.W * spec["size"] / 100)
        item.h = item.w * ratio
        bx, by = self._base(spec, item.w, item.h)
        item.setPos(bx + self.W * spec["x"] / 100, by + self.H * spec["y"] / 100)
        item.update()

    def item_changed(self, item, final=False):
        """អូស / ប្តូរទំហំលើឆាក → សរសេរទៅ spin box (ទំហំ · សារ៉េ X / Y)"""
        c = self.host.ov[item.prefix]
        spec = self._spec(item.prefix)
        size = min(max(item.w / self.W * 100, c["size"].minimum()), c["size"].maximum())
        spec["size"] = round(size)
        w = self.W * spec["size"] / 100
        h = w * item.h / max(item.w, 1e-6)
        bx, by = self._base(spec, w, h)
        ox = min(max((item.pos().x() - bx) / self.W * 100, -100), 100)
        oy = min(max((item.pos().y() - by) / self.H * 100, -100), 100)
        self._busy = True
        try:
            c["size"].setValue(spec["size"])
            c["x"].setValue(round(ox, 1))
            c["y"].setValue(round(oy, 1))
        finally:
            self._busy = False
        self._insp_refresh()
        if final:
            self._place(item)  # ខ្ទាស់ទៅតម្លៃដែលបានបង្គត់ → ឃើញដូច Export

    # ---------- ផ្ទាំងកំណត់តូច ----------
    def _show_insp(self, prefix):
        c = self.host.ov.get(prefix)
        if not c:
            return self.insp.hide()
        self.cur = ""
        self.insp_name.setText(LABELS.get(prefix, prefix))
        self.insp_key.clear()
        for i in range(c["key"].count()):
            self.insp_key.addItem(c["key"].itemText(i), c["key"].itemData(i))
        self.cur = prefix
        self._insp_refresh()
        self.insp.show()

    def _insp_refresh(self):
        c = self.host.ov.get(self.cur)
        if not c:
            return
        self._busy_insp = True
        try:
            self.insp_key.setCurrentIndex(c["key"].currentIndex())
            for key, (s, val) in self.sl.items():
                hi = 100 if key == "size" else c[key].maximum()  # ស្លាយទំហំ: ដល់ 100% (spin box អនុញ្ញាតលើសនេះ)
                s.setRange(c[key].minimum(), max(hi, int(c[key].value())))
                s.setValue(int(c[key].value()))
                val.setText(f"{int(c[key].value())}%")
        finally:
            self._busy_insp = False

    def _insp_slider(self, key, v):
        if self.cur and not getattr(self, "_busy_insp", False):
            self.host.ov[self.cur][key].setValue(v)
            self.sl[key][1].setText(f"{v}%")

    def _insp_key_changed(self, i):
        if self.cur and i >= 0 and not getattr(self, "_busy_insp", False):
            self.host.ov[self.cur]["key"].setCurrentIndex(i)

    def select(self, prefix):
        self.scene.clearSelection()
        item = self.items.get(prefix)
        if item:
            item.setSelected(True)
