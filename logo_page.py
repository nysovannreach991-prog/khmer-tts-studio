"""🏷 ទំព័រ Logo & Ads — ដាក់ Logo / Lower third / Watermark / Ads លើវីដេអូដែល Dub រួច (ឬវីដេអូណាក៏បាន)
ឆ្វេង = បញ្ជីវីដេអូ + ជម្រើស + ចាប់ផ្តើម · ស្តាំ = Preview · ⚙ ការកំណត់ Logo & Ads = ផ្ទាំងរំកិលពីស្តាំ
(រក្សាសំឡេងដែលមាន មិនបកប្រែ — video_dub.start_brand_job)"""
import os
import time

from PyQt5.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

import app as backend
import srt_dub
import theme
import video_dub
from simple_ui import SPIN, SmoothBar, StoryCard, _fade_in, fmt_dur

VIDEO_EXT = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".ts")
KINDS = [("lg", "🏷 Logo"), ("lt", "📺 Lower third"), ("wm", "💧 Watermark"), ("ad", "📢 Ads")]


class VideoCard(StoryCard):
    """ជួរវីដេអូមួយ (រចនាដូចកាតរឿង) — ចុច = មើល Preview លើវីដេអូនេះ"""
    clicked = pyqtSignal(object)

    def __init__(self, path):
        super().__init__({"folder": path, "videos": [path], "srts": []})
        self.path = path
        self.setCursor(Qt.PointingHandCursor)
        self.st = {"status": "waiting", "stage": None, "pct": 0, "msg": "", "t0": None, "t1": None, "notes": [],
                   "out": None}

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self)

    def set_selected(self, on):
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)


class LogoPage(QWidget):
    SIDE_W = 520

    def __init__(self, host):
        """host = MainWindow: overlays(), ads_spec(), confirm_overlays(), video_path, _ov_timer,
        ov_settings_panel, ov_preview_panel, busy/set_busy, _check_ffmpeg, _go_page, simple_page"""
        super().__init__()
        self.host = host
        self.cards, self.queue, self.cur = [], [], None
        self.ready = set()  # Logo / Ads ដែលបានកំណត់រួច
        self.active, self.stop_req = False, False
        self._tick = 0
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(10)

        # ---- របារខាងលើ ----
        bar = QFrame()
        bar.setObjectName("simpleHero")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 10, 16, 10)
        bl.setSpacing(10)
        back = self._button("‹‹  ត្រឡប់", lambda: host._go_page(host.simple_page))
        title = QLabel("🏷 LOGO & ADS")
        title.setStyleSheet("font-size: 15pt; font-weight: bold;")
        note = QLabel("ដាក់ Logo · Lower third · Watermark · Ads លើវីដេអូដែល Dub រួច")
        note.setProperty("role", "muted")
        bl.addWidget(back)
        bl.addWidget(title)
        bl.addWidget(note, 1)
        bl.addWidget(self._button("🎬 យកពីទំព័រ Dub", self.import_dubbed,
                                  "យកវីដេអូដែលទើប Dub រួចនៅទំព័រមុខ មកដាក់ Logo"))
        bl.addWidget(self._button("➕ វីដេអូ", self._pick, "ជ្រើសវីដេអូផ្សេងៗ (ឬអូសទម្លាក់លើទំព័រនេះ)"))
        self.btn_settings = self._button("⚙ ការកំណត់ Logo && Ads", None,
                                         "កំណត់ Logo, Lower third, Watermark និង Ads")
        self.btn_settings.setCheckable(True)
        self.btn_settings.toggled.connect(self._toggle_settings)
        bl.addWidget(self.btn_settings)
        outer.addWidget(bar)

        # ---- ខ្លួន: ឆ្វេង = វីដេអូ + ចាប់ផ្តើម · ស្តាំ = Preview ----
        body = QHBoxLayout()
        body.setSpacing(12)
        left = QFrame()
        left.setObjectName("simpleSide")
        left.setFixedWidth(430)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(14, 12, 14, 14)
        ll.setSpacing(8)
        head = QHBoxLayout()
        self.count = QLabel("មិនទាន់មានវីដេអូ")
        self.count.setObjectName("sideHead")
        head.addWidget(self.count, 1)
        self.btn_clear = self._button("សម្អាត", self.clear)
        head.addWidget(self.btn_clear)
        ll.addLayout(head)
        self.list_box = QWidget()
        self.list_lay = QVBoxLayout(self.list_box)
        self.list_lay.setContentsMargins(0, 0, 4, 0)
        self.list_lay.setSpacing(8)
        self.empty = QLabel("🎬 ចុច \"យកពីទំព័រ Dub\"\nឬ ➕ វីដេអូ · អូសទម្លាក់វីដេអូនៅទីនេះ")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setProperty("role", "muted")
        self.list_lay.addWidget(self.empty)
        self.list_lay.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.list_box)
        ll.addWidget(scroll, 1)

        use = QLabel("ដាក់:")
        use.setObjectName("sideLabel")
        ll.addWidget(use)
        grid = QHBoxLayout()
        self.use = {}
        for kind, label in KINDS:
            cb = QCheckBox(label)
            cb.toggled.connect(lambda _: self._sync())
            self.use[kind] = cb
            grid.addWidget(cb)
        grid.addStretch()
        ll.addLayout(grid)
        part_row = QHBoxLayout()
        lab = QLabel("✂ កាត់ជាផ្នែក")
        lab.setObjectName("sideLabel")
        self.part = QSpinBox()
        self.part.setRange(0, 120)
        self.part.setSuffix(" នាទី")
        self.part.setSpecialValueText("មិនកាត់")
        self.part.valueChanged.connect(lambda _: self._sync())
        part_row.addWidget(lab)
        part_row.addWidget(self.part, 1)
        ll.addLayout(part_row)

        self.headline = QLabel("")
        self.headline.setObjectName("simpleHeadline")
        self.headline.setWordWrap(True)
        self.bar = SmoothBar(14)
        self.bar.setObjectName("bigBar")
        ll.addWidget(self.headline)
        ll.addWidget(self.bar)
        go_row = QHBoxLayout()
        self.btn_open = self._button("📂 Output", lambda: os.startfile(self.host_out()) if os.path.isdir(
            self.host_out()) else None)
        self.btn_stop = self._button("■ បញ្ឈប់", self._request_stop)
        self.btn_stop.hide()
        self.btn_go = QPushButton("▶  ដាក់ Logo")
        self.btn_go.setObjectName("bigGo")
        self.btn_go.setCursor(Qt.PointingHandCursor)
        self.btn_go.setMinimumHeight(50)
        self.btn_go.clicked.connect(self.start)
        go_row.addWidget(self.btn_open)
        go_row.addWidget(self.btn_stop)
        go_row.addWidget(self.btn_go, 1)
        ll.addLayout(go_row)
        body.addWidget(left)

        preview = QFrame()
        preview.setObjectName("simpleSide")
        pl = QVBoxLayout(preview)
        pl.setContentsMargins(14, 12, 14, 14)
        pl.addWidget(host.ov_preview_panel)  # ផ្ទាំង Preview ដើម (មើលជាមុន + ▶ Preview វីដេអូ)
        body.addWidget(preview, 1)
        outer.addLayout(body, 1)

        # ---- ⚙ ការកំណត់ Logo & Ads: ផ្ទាំងអណ្តែតរំកិលពីស្តាំ ----
        self.side = side = QFrame(self)
        side.setObjectName("simpleSide")
        side.setFixedWidth(self.SIDE_W)
        side.hide()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 12, 10, 12)
        sh = QHBoxLayout()
        st = QLabel("⚙ ការកំណត់ Logo & Ads")
        st.setObjectName("sideHead")
        sh.addWidget(st, 1)
        close = QPushButton("✕")
        close.setObjectName("cardX")
        close.setFixedSize(28, 28)
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(lambda: self.btn_settings.setChecked(False))
        sh.addWidget(close)
        sl.addLayout(sh)
        sl.addWidget(host.ov_settings_panel, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.restyle()
        self._sync()

    def _button(self, text, fn, tip=""):
        b = QPushButton(text)
        b.setObjectName("ghost")
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        if fn:
            b.clicked.connect(fn)
        return b

    @staticmethod
    def host_out():
        return backend.OUTPUT_DIR  # set_output_dir ប្តូរទាំង gui និង backend

    # ---------- ផ្ទាំងការកំណត់ ----------
    def _toggle_settings(self, on):
        y, h = 6, self.height() - 12
        self.side.setFixedHeight(h)
        shown, hidden = QPoint(self.width() - self.SIDE_W - 6, y), QPoint(self.width() + 4, y)
        if on:
            self.side.move(hidden)
            self.side.show()
            self.side.raise_()
        else:
            self._sync()  # Logo / Ads ទើបកំណត់ → ធីកបាន
        anim = QPropertyAnimation(self.side, b"pos", self)
        anim.setDuration(280)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(self.side.pos())
        anim.setEndValue(shown if on else hidden)
        if not on:
            anim.finished.connect(self.side.hide)
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.side.isVisible():
            self.side.setFixedHeight(self.height() - 12)
            self.side.move(self.width() - self.SIDE_W - 6, 6)

    def showEvent(self, e):
        super().showEvent(e)
        self._sync()
        if not self.cards:
            self.import_dubbed(quiet=True)

    def paintEvent(self, e):
        from PyQt5.QtGui import QPainter
        import vector_fx
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        vector_fx.paint_backdrop(p, self.rect(), seed=11)
        super().paintEvent(e)

    # ---------- វីដេអូ ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and not self.active:
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def _pick(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "ជ្រើសវីដេអូដើម្បីដាក់ Logo", self.host_out(),
            "វីដេអូ (*.mp4 *.mkv *.mov *.avi *.webm *.flv *.m4v *.ts);;ឯកសារទាំងអស់ (*)")
        if files:
            self.add_paths(files)

    def import_dubbed(self, quiet=False):
        """វីដេអូដែល Dub រួចនៅទំព័រមុខ (Folder លទ្ធផលនៃរឿងនីមួយៗ)"""
        pipe = self.host.simple_page.pipeline
        folders = [st["out"] for st in (pipe.state if pipe else []) if st.get("out") and os.path.isdir(st["out"])]
        files = []
        for folder in folders:
            files += sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(VIDEO_EXT))
        if files:
            self.add_paths(files)
        elif not quiet:
            self.headline.setText("⚠ មិនទាន់មានវីដេអូដែល Dub រួចនៅទំព័រមុខ — ចុច ➕ វីដេអូ")

    def add_paths(self, paths):
        if self.active:
            return
        files = []
        for p in paths:
            if os.path.isdir(p):
                files += sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(VIDEO_EXT))
            elif p.lower().endswith(VIDEO_EXT) and os.path.isfile(p):
                files.append(p)
        known = {c.path for c in self.cards}
        for path in dict.fromkeys(os.path.normpath(f) for f in files):
            if path in known:
                continue
            card = VideoCard(path)
            card.remove.connect(self._remove)
            card.clicked.connect(self._select)
            self.list_lay.insertWidget(self.list_lay.count() - 1, card)
            _fade_in(card)
            self.cards.append(card)
        if self.cards and not any(c.property("selected") for c in self.cards):
            self._select(self.cards[0])
        self._sync()

    def _select(self, card):
        for c in self.cards:
            c.set_selected(c is card)
        self.host.video_path.setText(card.path)  # Preview លើវីដេអូនេះ
        self.host._ov_timer.start(50)

    def _remove(self, card):
        if self.active:
            return
        self.cards.remove(card)
        card.deleteLater()
        if card.property("selected") and self.cards:
            self._select(self.cards[0])
        self._sync()

    def clear(self):
        if self.active:
            return
        for c in self.cards:
            c.deleteLater()
        self.cards = []
        self.bar.set_pct(0)
        self._sync()

    # ---------- ដំណើរការ ----------
    def _selection(self):
        overlays = [o for o in self.host.overlays() if self.use[o["kind"]].isChecked()]
        ads = self.host.ads_spec() if self.use["ad"].isChecked() else None
        return overlays, ads

    def _sync(self):
        """ធីកបានតែអ្វីដែលបានកំណត់រួច · ប៊ូតុង/អក្សរតាមស្ថានភាព"""
        ready = {o["kind"] for o in self.host.overlays()} | ({"ad"} if self.host.ads_spec() else set())
        for kind, cb in self.use.items():
            if (kind in ready) != (kind in self.ready):  # ទើបកំណត់ → ធីក · មិនទាន់កំណត់ → មិនធីក
                cb.blockSignals(True)
                cb.setChecked(kind in ready)
                cb.blockSignals(False)
            cb.setEnabled(kind in ready and not self.active)
            cb.setToolTip("" if kind in ready else "មិនទាន់កំណត់ — ចុច ⚙ ការកំណត់ Logo & Ads")
        self.ready = ready
        n = len(self.cards)
        self.empty.setVisible(n == 0)
        self.count.setText(f"🎬 {n} វីដេអូ" if n else "មិនទាន់មានវីដេអូ")
        self.btn_clear.setEnabled(n > 0 and not self.active)
        self.btn_go.setEnabled(n > 0 and not self.active)
        if self.active:
            return
        overlays, ads = self._selection()
        names = [label for kind, label in KINDS if kind in ready and self.use[kind].isChecked()]
        if not ready:
            self.headline.setText("⚠ មិនទាន់កំណត់ Logo / Ads — ចុច ⚙ ការកំណត់ Logo & Ads")
        elif not names:
            self.headline.setText("⚠ សូមធីក Logo / Ads ដែលចង់ដាក់")
        else:
            self.headline.setText("នឹងដាក់: " + " · ".join(names) + (f" · ✂ {self.part.value()} នាទី"
                                                                      if self.part.value() else ""))

    def start(self):
        host = self.host
        if self.active or host.busy or not self.cards or not host._check_ffmpeg():
            return
        overlays, ads = self._selection()
        if not (overlays or ads):
            self._sync()
            return self.btn_settings.setChecked(True)
        todo = [c for c in self.cards if c.st["status"] != "done"] or self.cards
        if not host.confirm_overlays([(os.path.basename(c.path), c.path) for c in todo], overlays, ads,
                                     use_ads=bool(ads)):
            return
        for c in todo:
            c.st.update(status="waiting", stage=None, pct=0, msg="", t0=None, t1=None, out=None)
            c.update_from(c.st, 0)
        self.queue, self.total, self.done_n = list(todo), len(todo), 0
        self.specs = (overlays, ads, self.part.value() * 60)
        self.active, self.stop_req, self.t0 = True, False, time.time()
        host.set_busy(True)
        self.btn_go.setText("⏳  កំពុងដាក់...")
        self.btn_stop.show()
        self.btn_stop.setEnabled(True)
        self._sync()
        self._next()

    def _next(self):
        if self.stop_req or not self.queue:
            return self._finish()
        card = self.cur = self.queue.pop(0)
        overlays, ads, part_sec = self.specs
        name = os.path.splitext(os.path.basename(card.path))[0]
        base = f"{backend.safe_name(name) or 'video'}_logo_{time.strftime('%Y%m%d_%H%M%S')}"
        self.job = video_dub.start_brand_job(card.path, self.host_out(), base, overlays, ads, part_sec)
        card.st.update(status="running", stage="video", pct=0, msg="កំពុងរកចន្លោះស្ងាត់...", t0=time.time(),
                       since=time.time())
        self.timer.start(300)

    def _poll(self):
        self._tick += 1
        card, s = self.cur, srt_dub.jobs.get(self.job)
        if not card or s is None:
            return
        st = card.st
        if s["status"] == "error":
            st.update(status="failed", msg=f"✕ {s['error'][-120:]}", t1=time.time())
        elif s["status"] == "done":
            out = os.path.join(self.host_out(), s["folder"])
            extra = f" · Ads {s['ads_inserted']}" if s.get("ads_inserted") else ""
            st.update(status="done", pct=100, msg=f"រួចរាល់ {len(s['parts'])} ផ្នែក{extra}", out=out, t1=time.time())
            self.done_n += 1
        else:
            pct = int(s.get("stage_pct", 0)) if s["status"] == "video" else 0
            msg = {"analyzing": "កំពុងរកចន្លោះស្ងាត់...", "trimming": "កំពុងកាត់..."}.get(
                s["status"], f"កំពុងដាក់ Logo {pct}%")
            if (pct, msg) != (st["pct"], st["msg"]):
                st["since"] = time.time()
            st.update(pct=pct, msg=msg)
        card.update_from(st, self._tick)
        finished = self.done_n + sum(c.st["status"] == "failed" for c in self.cards)
        overall = (finished - (0 if st["status"] == "running" else 1) + st["pct"] / 100) / max(self.total, 1)
        self.bar.set_pct(100 * max(0.0, min(overall, 1.0)))
        if st["status"] in ("done", "failed"):
            self.timer.stop()
            QTimer.singleShot(200, self._next)
            return
        self.headline.setText(f"{SPIN[self._tick % 4]}  {self.total - len(self.queue)}/{self.total} — "
                              f"{os.path.basename(card.path)} · ⏱ {fmt_dur(time.time() - self.t0)}")

    def _request_stop(self):
        self.stop_req = True
        self.btn_stop.setEnabled(False)
        self.headline.setText("■ នឹងបញ្ឈប់បន្ទាប់ពីវីដេអូនេះចប់")

    def _finish(self):
        self.timer.stop()
        self.active, self.cur = False, None
        self.host.set_busy(False)
        self.btn_go.setText("▶  ដាក់ Logo")
        self.btn_stop.hide()
        left = len(self.queue)
        self._sync()
        self.bar.set_pct(100 if not left else self.bar.value() / 10)
        self.headline.setText(f"🎉 រួចរាល់ ✓ {self.done_n}/{self.total}" + (f" · នៅសល់ {left}" if left else "") +
                              f"  ⏱ {fmt_dur(time.time() - self.t0)} → ថត Output")
        try:
            self.host.refresh_files()
        except Exception:  # noqa: BLE001 — ផ្ទាំងឯកសារចាស់ (មិនបង្ហាញ)
            pass

    def restyle(self):
        """ប្រើរចនាប័ទ្មកាតដូចទំព័រ Dub + កាតដែលកំពុង Preview មានស៊ុមទឹកក្រូច"""
        for c in self.cards:
            c.sync_skin()
        self.setStyleSheet(self.host.simple_page.styleSheet() + f"""
QFrame#storyCard[selected="true"] {{ border: 2px solid {theme.C['accent']}; }}
""")
