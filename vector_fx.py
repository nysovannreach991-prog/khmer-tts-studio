"""ចលនា theme "vector" (ម៉ាស៊ីនកុំព្យូទ័របុរាណ) សម្រាប់ Simple mode
BootOverlay — "Linking... N%" (tick 16) → ផ្ទាំងងងឹតមានជ្រុងកាត់ឡើងពីក្រោម បង្ហាញ UI → ភ្លឺឡើង
TraceArrow — ខ្សែសៀគ្វីរវាងជំហាន: សញ្ញាទឹកក្រូចហូរចូលជំហានដែលកំពុងធ្វើ
Receipt — វិក្កយបត្រក្រដាសបោះពុម្ពចេញពីរន្ធ ពេលចប់ការងារ
ProcessBoard — បន្ទះសៀគ្វី: module ៤ (ឆ្វេង) → កុំព្យូទ័រធំកណ្តាល → module វីដេអូ (ស្តាំ)
paint_backdrop — ផ្ទៃខាងក្រោយបន្ទះសៀគ្វីស្រាលៗ"""
import math
import os
import random
import time

from PyQt5.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt5.QtWidgets import QWidget

import theme

TICKS = 16
# គែមខាងលើនៃផ្ទាំងដែលឡើង (x, y) ជាសមាមាត្រ — ចំកណ្តាលទាប ជ្រុងសងខាងខ្ពស់ (ដូចបន្ទះម៉ាស៊ីន)
_PROFILE = [(0.0, 0.0), (0.07, 0.10), (0.15, 0.10), (0.21, 0.17), (0.66, 0.17), (0.74, 0.08),
            (0.80, 0.08), (1.0, -0.06)]


def _ease(x):
    return QEasingCurve(QEasingCurve.InOutCubic).valueForProgress(max(0.0, min(1.0, x)))


def draw_ticks(p, rect, frac, on, off):
    """របារ tick (ដូច "Linking...") — frac 0..1"""
    p.setPen(QPen(QColor(theme.C["border_hi"]), 1))
    p.setBrush(QColor("#ececeb"))
    p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
    pad = rect.height() * 0.8
    step = (rect.width() - 2 * pad) / (TICKS - 1)
    lit = round(frac * TICKS)
    for i in range(TICKS):
        x = rect.left() + pad + i * step
        p.setPen(QPen(QColor(on if i < lit else off), 2, cap=Qt.FlatCap))
        p.drawLine(QPointF(x, rect.top() + rect.height() * 0.3), QPointF(x, rect.bottom() - rect.height() * 0.3))


class BootOverlay(QWidget):
    DURATION = 3000

    def __init__(self, page):
        super().__init__(page)
        self.snapshot = page.grab()
        self.setGeometry(page.rect())
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.t = 0
        self.anim = QVariantAnimation(self)
        self.anim.setStartValue(0)
        self.anim.setEndValue(self.DURATION)
        self.anim.setDuration(self.DURATION)
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self.deleteLater)
        self.show()
        self.raise_()
        self.anim.start()

    def _step(self, v):
        self.t = int(v)
        self.update()

    def paintEvent(self, _):
        c, t = theme.C, self.t
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # ---- អេក្រង់ boot ----
        p.fillRect(self.rect(), QColor(c["bg"]))
        frac = min(1.0, t / 1500)
        cx, cy = w / 2, h * 0.36
        logo = QPolygonF([QPointF(cx, cy - 34), QPointF(cx + 8, cy - 25), QPointF(cx, cy - 16), QPointF(cx - 8, cy - 25)])
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["lcd"]))
        p.drawPolygon(logo)
        draw_ticks(p, QRectF(cx - 120, cy - 6, 240, 22), frac, c["lcd"], "#c9cbcd")
        p.setPen(QColor(c["text"]))
        f = theme.mono(QFont(self.font()))
        f.setPointSizeF(8.5)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, cy + 22, w, 18), Qt.AlignHCenter, "Linking...")
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(c["muted"]))
        p.drawText(QRectF(0, cy + 38, w, 18), Qt.AlignHCenter, f"{int(frac * 100)}%")
        # ---- ផ្ទាំងងងឹតឡើងពីក្រោម → UI ពិត ----
        rise = _ease((t - 1700) / 700)
        if t < 1700:
            return
        base = h - rise * h * 1.3
        pts = [QPointF(x * w, base + y * h) for x, y in _PROFILE]
        path = QPainterPath()
        path.addPolygon(QPolygonF(pts + [QPointF(w, h + 2), QPointF(0, h + 2)]))
        path.closeSubpath()
        p.save()
        p.setClipPath(path)
        p.drawPixmap(0, 0, self.snapshot)
        dark = 0.62 * (1 - _ease((t - 2350) / 650))
        p.fillRect(self.rect(), QColor(24, 25, 27, int(255 * dark)))
        p.restore()
        if rise < 1:  # គែមខ្មៅក្រាស់ (ដូចស៊ុមម៉ាស៊ីន) ពេលកំពុងឡើង
            p.setPen(QPen(QColor(32, 33, 35, int(255 * (1 - rise))), 6, join=Qt.MiterJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPolyline(QPolygonF(pts))


class TraceArrow(QWidget):
    """ខ្សែសៀគ្វីតូច: todo = ប្រផេះ · done = ទឹកក្រូចស្ងៀម · flow = សញ្ញាទឹកក្រូចហូរទៅស្តាំ"""

    def __init__(self):
        super().__init__()
        self.setFixedSize(38, 22)
        self.state = "todo"
        self.phase = 0.0

    def set_state(self, state):
        if state != self.state:
            self.state = state
            self.update()

    def advance(self):
        self.phase = (self.phase + 1.6) % 8
        if self.state == "flow":
            self.update()

    def paintEvent(self, _):
        c = theme.C
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        y1, y2 = 8.0, 14.0
        line = QPolygonF([QPointF(0, y1), QPointF(13, y1), QPointF(19, y2), QPointF(38, y2)])
        p.setPen(QPen(QColor(c["border_hi"]), 3, cap=Qt.FlatCap, join=Qt.MiterJoin))
        p.drawPolyline(line)
        if self.state == "todo":
            return
        pen = QPen(QColor(c["accent"]), 3, cap=Qt.FlatCap, join=Qt.MiterJoin)
        pen.setDashPattern([1.2, 1.4])  # គិតជាទទឹងខ្សែ — ចំណុចតូចៗដូចសញ្ញាទិន្នន័យ
        if self.state == "flow":
            pen.setDashOffset(-self.phase)
        else:
            pen.setColor(QColor(c["accent3"]))
        p.setPen(pen)
        p.drawPolyline(line)


class Receipt(QWidget):
    """វិក្កយបត្របោះពុម្ពចេញពីរន្ធ (ចុចដើម្បីបិទ)"""
    W, H = 250, 168

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(self.W + 20, self.H + 16)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("ចុចដើម្បីបិទ")
        self.lines = []
        self.shown = 0.0
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(1400)
        self.anim.valueChanged.connect(self._step)
        self.hide()

    def _step(self, v):
        # ម៉ាស៊ីនបោះពុម្ព: ចេញជាជំហានៗ (មិនរលូន)
        self.shown = round(float(v) * 14) / 14
        self.update()

    def print_out(self, title, lines):
        self.title, self.lines = title, lines
        self.stamp = time.strftime("%d.%m.%Y · %H:%M")
        self.shown = 0.0
        self.show()
        self.raise_()
        self.anim.stop()
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()

    def mouseReleaseEvent(self, _):
        self.hide()

    def paintEvent(self, _):
        c = theme.C
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # រន្ធម៉ាស៊ីន
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["lcd"]))
        p.drawRoundedRect(QRectF(0, 0, self.W + 20, 9), 4, 4)
        p.setBrush(QColor("#111214"))
        p.drawRect(QRectF(8, 3, self.W + 4, 3))
        # ក្រដាស (មើលឃើញតែផ្នែកដែលបានបោះពុម្ពចេញ — អត្ថបទរំកិលចុះពីរន្ធ)
        visible = self.H * self.shown
        if visible <= 0:
            return
        p.setClipRect(QRectF(0, 6, self.width(), visible + 2))
        p.translate(10, 6 + visible - self.H)
        paper = QPainterPath()
        paper.moveTo(0, 0)
        paper.lineTo(self.W, 0)
        teeth = 14
        for i in range(teeth, -1, -1):  # គែមក្រោមជាធ្មេញរណារ
            x = self.W * i / teeth
            paper.lineTo(x, self.H - (0 if i % 2 else 7))
        paper.closeSubpath()
        p.setBrush(QColor(0, 0, 0, 30))
        p.drawPath(paper.translated(2, 3))
        p.setBrush(QColor("#fbfbf8"))
        p.drawPath(paper)
        f = theme.mono(QFont(self.font()))
        f.setPointSizeF(7.5)
        p.setFont(f)
        p.setPen(QColor(c["muted"]))
        p.drawText(QRectF(14, 10, self.W - 28, 14), Qt.AlignLeft, "AI-TEAM/01")
        p.drawText(QRectF(14, 10, self.W - 28, 14), Qt.AlignRight, self.stamp)
        p.setPen(QPen(QColor(c["border"]), 1, Qt.DashLine))
        p.drawLine(QPointF(14, 30), QPointF(self.W - 14, 30))
        serif = QFont(self.font())
        serif.setFamilies(["Georgia"] + serif.families())
        serif.setPointSizeF(17)
        p.setFont(serif)
        p.setPen(QColor(c["text"]))
        p.drawText(QRectF(14, 34, self.W - 28, 40), Qt.AlignLeft | Qt.AlignVCenter, self.title)
        f.setPointSizeF(8.5)
        p.setFont(f)
        for n, line in enumerate(self.lines[:4]):
            p.setPen(QColor(c["accent"] if n == 0 else c["text"]))
            p.drawText(QRectF(14, 80 + n * 18, self.W - 28, 18), Qt.AlignLeft | Qt.AlignVCenter, line)


# ---------------- ផ្ទៃខាងក្រោយ + បន្ទះសៀគ្វី ----------------

def _font(base, families, pt, bold=False):
    f = QFont(base)
    f.setFamilies(families + [x for x in base.families() if x not in families])
    f.setPointSizeF(pt)
    f.setBold(bold)
    return f


def _condensed(base, pt):
    return _font(base, ["Bahnschrift SemiBold SemiConden", "Bahnschrift SemiCondensed", "Bahnschrift"], pt, True)


def paint_backdrop(p, rect, seed=7):
    """ខ្សែសៀគ្វី ចំណុច និងរន្ធខ្យល់ស្រាលៗ — គ្រាន់តែជាផ្ទៃខាងក្រោយ (មិនរំខានអក្សរ)"""
    rnd = random.Random(seed)
    line = QColor(theme.C["border"])
    line.setAlpha(150)
    p.setBrush(Qt.NoBrush)
    x0, y0, w, h = rect.left(), rect.top(), rect.width(), rect.height()
    for _ in range(int(w * h / 26000) + 4):  # ខ្សែមានជ្រុងបត់ 45°
        x, y = x0 + rnd.random() * w, y0 + rnd.random() * h
        a, d, b = rnd.randint(30, 160), rnd.choice((-1, 1)) * rnd.randint(14, 40), rnd.randint(30, 160)
        p.setPen(QPen(line, 2, cap=Qt.FlatCap, join=Qt.MiterJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([QPointF(x, y), QPointF(x + a, y), QPointF(x + a + abs(d), y + d),
                                  QPointF(x + a + abs(d) + b, y + d)]))
        p.setPen(Qt.NoPen)
        p.setBrush(line)
        p.drawEllipse(QPointF(x, y), 3, 3)
    p.setPen(QPen(line, 2, cap=Qt.FlatCap))
    for _ in range(int(w * h / 90000) + 2):  # រន្ធខ្យល់ ||||
        x, y = x0 + rnd.random() * (w - 40), y0 + rnd.random() * (h - 14)
        for i in range(5):
            p.drawLine(QPointF(x + i * 6, y), QPointF(x + i * 6, y + 12))


STEP_INFO = {"merge": ("MERGE", "បញ្ចូល Part"), "asr": ("SPEECH → SRT", "ស្តាប់ជា SRT"),
             "translate": ("TRANSLATE", "បកប្រែខ្មែរ"), "tts": ("VOICE", "បញ្ចូលសំឡេង"),
             "video": ("RENDER", "វីដេអូខ្មែរ")}
ORDER = ["merge", "asr", "translate", "tts", "video"]

BOARD_IMAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images", "vector_board.png")
IMG_W, IMG_H = 1672, 941
# ខ្សែក្នុងរូប: ចាប់ពី pad (module) → ចុងខ្សែនៅកុំព្យូទ័រ · ទិសហូរតាមលំដាប់ចំណុច
IMG_LEFT = [
    [(298, 225), (365, 225), (440, 305), (440, 350), (480, 396), (650, 396)],
    [(298, 370), (390, 370), (445, 428), (650, 428)],
    [(298, 526), (382, 526), (445, 461), (650, 461)],
    [(298, 646), (372, 646), (440, 577), (440, 535), (480, 496), (650, 496)],
]
IMG_RIGHT = [  # កុំព្យូទ័រ → ចេញក្រៅ: ឡើង (RENDER) · ផ្ដេក · ចុះ (ថតរឿង)
    [(1017, 411), (1145, 411), (1172, 382), (1172, 295), (1218, 247)],
    [(1017, 434), (1150, 434), (1172, 458), (1221, 458)],
    [(1017, 457), (1147, 457), (1172, 482), (1172, 572), (1219, 617)],
]
IMG_BAYS = [(36, 157, 286, 297), (36, 310, 286, 450), (36, 464, 286, 603), (36, 615, 286, 698)]
IMG_COMP = (668, 250, 1002, 640)
IMG_KNOBS = [(1155, 122), (1200, 122), (1245, 122)]
IMG_RENDER = (1258, 175, 1608, 303)
IMG_QUEUE = (1262, 345, 1604, 592)
IMG_NAV = (1270, 612, 1596, 694)  # ប៊ូតុងទៅទំព័រ 🏷 Logo & Ads
_board_pix = None


def _board_image():
    global _board_pix
    if _board_pix is None:
        _board_pix = QPixmap(BOARD_IMAGE) if os.path.exists(BOARD_IMAGE) else QPixmap()
    return None if _board_pix.isNull() else _board_pix


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class ProcessBoard(QWidget):
    """បន្ទះសៀគ្វីពេញផ្ទាំង (ដូចវីដេអូ Superlinked):
    module ៤ ខាងឆ្វេង → ផ្លូវខ្សែ (bus) មានកញ្ចប់ទិន្នន័យទឹកក្រូច → កុំព្យូទ័រកណ្តាល (ទម្លាក់ Folder នៅទីនេះ)
    → module RENDER ខាងស្តាំ + ថតរឿង (drawer) · LCD ‹ › មើលរឿងនីមួយៗ + progress · ប៊ូតុង START ក្រោមរន្ធ
    model() → dict ពី SimplePage (រឿង, ស្ថានភាព, អក្សរ) — board គ្រាន់តែគូរ និងបញ្ជូនការចុច"""
    dropped = pyqtSignal(list)
    action = pyqtSignal(str)  # pick · start · stop · settings · parent · clear · open · remove:N
    INPUTS = ORDER[:4]  # module ៤ ខាងឆ្វេង (វីដេអូ = module ចេញខាងស្តាំ)

    def __init__(self, icons, model):
        super().__init__()
        self.icons, self.model = icons, model
        self.setMinimumHeight(420)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.phase = 0
        self.view = None  # រឿងដែល LCD កំពុងបង្ហាញ (None = តាមរឿងដែលកំពុងធ្វើ)
        self.hover, self.drag = None, False
        self.hits = []
        self.slot_rect = QRectF()
        self.timer = QTimer(self)
        self.timer.setInterval(55)
        self.timer.timeout.connect(self._tick)

    def _tick(self):
        self.phase += 1
        self.update()

    def refresh(self):
        m = self.model()
        if m["running"] and self.isVisible():
            if not self.timer.isActive():
                self.timer.start()
        else:
            self.timer.stop()
        self.update()

    # ---------- ការចុច / ទម្លាក់ ----------
    def _hit(self, pos):
        for name, rect in reversed(self.hits):  # ផ្នែកដែលគូរក្រោយ (ខាងលើ) មុន
            if rect.contains(QPointF(pos)):
                return name
        return None

    def mouseMoveEvent(self, e):
        name = self._hit(e.pos())
        if name != self.hover:
            self.hover = name
            self.setCursor(Qt.PointingHandCursor if name else Qt.ArrowCursor)
            self.setToolTip({"settings": "⚙ ការកំណត់", "parent": "➕ Folder មេ (Folder រងទាំងអស់)",
                             "clear": "សម្អាតបញ្ជីរឿង", "pick": "ទម្លាក់ ឬចុចដើម្បីជ្រើស Folder រឿង",
                             "open": "📂 បើកថត Output"}.get(name, ""))
            self.update()

    def leaveEvent(self, _):
        self.hover = None
        self.update()

    def mouseReleaseEvent(self, e):
        name = self._hit(e.pos())
        if e.button() != Qt.LeftButton or not name:
            return
        n = len(self.model()["stories"])
        if name in ("prev", "next") and n:
            cur = self._viewed(self.model())
            self.view = (cur + (1 if name == "next" else -1)) % n
            self.update()
        elif name.startswith("drawer:"):
            self.view = int(name.split(":")[1])
            self.update()
        else:
            self.action.emit(name)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and not self.model()["running"]:
            e.acceptProposedAction()
            self.drag = True
            self.update()

    def dragLeaveEvent(self, _):
        self.drag = False
        self.update()

    def dropEvent(self, e):
        self.drag = False
        self.update()
        self.dropped.emit([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def _viewed(self, m):
        n = len(m["stories"])
        if not n:
            return 0
        if self.view is not None and self.view < n and not m["running"]:
            return self.view
        if m["running"] and self.view is not None and self.view < n:
            return self.view
        live = next((i for i, s in enumerate(m["stories"]) if s["status"] == "running"), None)
        return live if live is not None else 0

    def _stage_state(self, m, key):
        if m["finished"]:
            return "done"
        cur = m["stage"]
        if not m["running"] or cur not in ORDER:
            return "todo"
        i, j = ORDER.index(key), ORDER.index(cur)
        return "done" if i < j else "active" if i == j else "todo"

    # ---------- គូរ ----------
    def _layout_image(self, W, H):
        """ប្រើរូបផ្ទៃខាងក្រោយ (images/vector_board.png): ដាក់ module/កុំព្យូទ័រ/ប៊ូតុង ត្រូវនឹងរូប"""
        sc = min(W / IMG_W, H / IMG_H)
        ox, oy = (W - IMG_W * sc) / 2, (H - IMG_H * sc) / 2

        def pt(x, y):
            return QPointF(ox + x * sc, oy + y * sc)

        def rect(x0, y0, x1, y1):
            return QRectF(pt(x0, y0), pt(x1, y1))

        return {"image": True, "scale": sc, "target": rect(0, 0, IMG_W, IMG_H),
                "mods": [rect(x0 + 6, y0 + 6, x1 - 6, y1 - 6) for x0, y0, x1, y1 in IMG_BAYS],
                "comp": rect(*IMG_COMP), "out": rect(*IMG_RENDER), "queue": rect(*IMG_QUEUE),
                "left": [[pt(*q) for q in w] for w in IMG_LEFT], "right": [[pt(*q) for q in w] for w in IMG_RIGHT],
                "knobs": [pt(*q) for q in IMG_KNOBS], "nav": rect(*IMG_NAV)}

    def _layout(self, W, H):
        """ទីតាំងគ្រប់ផ្នែក (គណនាម្តងក្នុងមួយទំហំ) + ផ្លូវខ្សែជាដើមឈើ"""
        if _board_image():
            return self._layout_image(W, H)
        pad = 18.0
        bay_w = _clamp(W * 0.055, 44, 90)
        mw, gap = _clamp(W * 0.135, 128, 210), 16.0
        mh = _clamp((H - 2 * pad - 3 * gap) / 4 - 14, 70, 150)
        my = (H - 4 * mh - 3 * gap) / 2
        mx = pad + bay_w + 12
        mods = [QRectF(mx, my + i * (mh + gap), mw, mh) for i in range(4)]
        cw, ch = _clamp(W * 0.28, 300, 470), _clamp(H * 0.48, 250, 370)
        comp = QRectF(W * 0.47 - cw / 2, H * 0.46 - ch / 2 - 24, cw, ch)
        rw = _clamp(W * 0.19, 210, 320)
        plate = QRectF(W - pad - rw - 28, max(pad + 6, comp.top() - 40), rw + 28, 0)
        plate.setBottom(H - pad - 18)
        out = QRectF(plate.left() + 14, plate.top() + 16, rw, 78)
        queue = QRectF(out.left(), out.bottom() + 40, rw, plate.bottom() - out.bottom() - 160)
        nav = QRectF(out.left(), queue.bottom() + 18, rw, 76)

        # ---- ដើមឈើខាងឆ្វេង: module ៤ → ចំណុចប្រសព្វ J → កុំព្យូទ័រ ----
        ys = [r.center().y() for r in mods]
        jy = (ys[1] + ys[2]) / 2  # ចំណុចប្រសព្វនៅចន្លោះ module ទី ២ និង ៣
        xv = mods[0].right() + _clamp((comp.left() - mods[0].right()) * 0.3, 26, 70)
        J = QPointF(xv, jy)
        stubs = [[QPointF(r.right(), y), QPointF(xv, y)] for r, y in zip(mods, ys)]
        # ខ្សែបញ្ឈរជាកំណាត់ៗ — ទិសហូរទៅ J · carriers = module ដែលទិន្នន័យឆ្លងកាត់កំណាត់នេះ
        levels = sorted(ys + [jy])
        pieces = []
        for a, b in zip(levels, levels[1:]):
            if b <= jy:
                pieces.append(([QPointF(xv, a), QPointF(xv, b)], [i for i, y in enumerate(ys) if y <= a]))
            else:
                pieces.append(([QPointF(xv, b), QPointF(xv, a)], [i for i, y in enumerate(ys) if y >= b]))
        trunk = [J, QPointF(comp.left() + 2, jy)]
        # ---- ខាងស្តាំ: កុំព្យូទ័រ → J2 → RENDER (ឡើងលើ) + មែកចុះទៅថតរឿង ----
        j2 = QPointF(min(comp.right() + W * 0.05, plate.left() - 60), comp.top() + comp.height() * 0.48)
        out_trunk = [QPointF(comp.right() - 2, j2.y()), j2]
        up = _route45(j2, QPointF(plate.left() + 2, out.center().y()), diag_first=True)
        down_y = min(queue.top() + 60, plate.bottom() - 40)
        down = _route45(j2, QPointF(plate.left() + 2, down_y), diag_first=True)
        return {"pad": pad, "bay_w": bay_w, "mods": mods, "comp": comp, "plate": plate, "out": out, "nav": nav,
                "queue": queue, "J": J, "J2": j2, "stubs": stubs, "pieces": pieces, "trunk": trunk,
                "out_trunk": out_trunk, "up": up, "down": down}

    def paintEvent(self, _):
        m = self.model()
        c = theme.C
        self.hits = []
        W, H = float(self.width()), float(self.height())
        key = (int(W), int(H), theme.mode)
        if getattr(self, "_bg_key", None) != key:  # ផ្ទៃខាងក្រោយ (មិនប្តូរ) — គូរម្តង រក្សាទុកជារូប
            self._L = self._layout(W, H)
            self._bg = self._paint_background(W, H, self._L)
            self._bg_key = key
        L = self._L
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(0, 0, self._bg)

        states = [self._stage_state(m, k) for k in self.INPUTS]
        ostate = self._stage_state(m, "video")
        if L.get("image"):
            self._paint_image_wires(p, L, m, states, ostate)
        else:
            self._paint_drawn_wires(p, L, m, states, ostate)
        for i, r in enumerate(L["mods"]):
            self._module(p, r, ORDER[i], states[i], m)
        self._render(p, L["out"], m)
        self._drawers(p, L["queue"], m)
        self._nav(p, L["nav"])
        comp = L["comp"]
        knobs = L.get("knobs") or [QPointF(comp.right() - 58 + i * 30, comp.top() - 50) for i in range(3)]
        self._knobs(p, knobs, m)
        self._computer(p, comp, m)
        self._start(p, comp, m)

    def _paint_image_wires(self, p, L, m, states, ostate):
        """សញ្ញាទឹកក្រូចហូរតាមខ្សែក្នុងរូប (ខ្សែប្រផេះមានក្នុងរូបរួចហើយ)"""
        w = 14 * L["scale"]  # ទទឹងខ្សែក្នុងរូប
        kw = {"lane": w * 0.2, "size": max(2.0, w * 0.24), "step": max(6.0, w * 0.72), "todo": False}
        for pts, st in zip(L["left"], states):
            self._packets(p, pts, st, **kw)
            self._pad_glow(p, pts[0], st, w)
        finished = m["finished"] and m["any_done"]
        for pts, st in zip(L["right"], (ostate, "done" if finished else ostate, "done" if finished else "todo")):
            self._packets(p, pts, st, **kw)
            self._pad_glow(p, pts[-1], st, w)

    def _pad_glow(self, p, ctr, state, w):
        if state == "todo":
            return
        on = state == "done" or (self.phase // 5) % 2 == 0
        col = QColor(theme.C["accent"])
        col.setAlpha(255 if on else 110)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(ctr, w * 0.22, w * 0.22)

    def _paint_drawn_wires(self, p, L, m, states, ostate):
        for pts, st in zip(L["stubs"], states):
            self._packets(p, pts, st)
        for pts, carriers in L["pieces"]:
            sts = [states[i] for i in carriers]
            self._packets(p, pts, "active" if "active" in sts else "done" if "done" in sts else "todo")
        trunk_state = ("active" if "active" in states else "done" if all(s == "done" for s in states)
                       else "done" if states[0] == "done" else "todo")
        self._packets(p, L["trunk"], trunk_state)
        self._packets(p, L["out_trunk"], ostate)
        self._packets(p, L["up"], ostate)
        self._packets(p, L["down"], "done" if m["finished"] and m["any_done"] else "todo")
        for i, pts in enumerate(L["stubs"]):
            self._node(p, pts[1], states[i])
        self._node(p, L["J"], trunk_state, big=True)
        self._node(p, L["J2"], ostate, big=True)

    # ---------- ផ្ទៃខាងក្រោយ (cache) ----------
    def _paint_background(self, W, H, L):
        pm = QPixmap(int(W), int(H))
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        if L.get("image"):  # រូបផ្ទៃខាងក្រោយ + ពណ៌គែម (ពេលសមាមាត្របង្អួចខុសពីរូប)
            g = QLinearGradient(0, 0, W, 0)
            g.setColorAt(0, QColor(188, 196, 204))
            g.setColorAt(1, QColor(208, 215, 223))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawRoundedRect(QRectF(0, 0, W, H), 8, 8)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(L["target"], _board_image(), QRectF(0, 0, IMG_W, IMG_H))
            p.end()
            return pm
        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0, QColor("#e4e5e6"))
        grad.setColorAt(1, QColor("#d9dadc"))
        p.setPen(QPen(QColor("#c6c8ca"), 1))
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), 8, 8)
        comp, plate, pad, bw = L["comp"], L["plate"], L["pad"], L["bay_w"]
        mods = L["mods"]

        # បន្ទះធំៗមានជ្រុងកាត់ (ស្រទាប់ផុស/លិច ដូចបន្ទះម៉ាស៊ីន)
        cx0, cx1 = comp.left() - W * 0.1, comp.right() + W * 0.07
        _bevel_poly(p, _chamfer(QRectF(cx0, 10, cx1 - cx0, H - 20), 34), raised=True, fill="#e1e2e3")
        _bevel_poly(p, _chamfer(QRectF(cx0 + 26, comp.top() - 78, cx1 - cx0 - 52, comp.height() + 150), 22),
                    raised=False, fill="#dddedf")
        left = QRectF(pad - 6, mods[0].top() - 30, bw + mods[0].width() + 30, mods[-1].bottom() - mods[0].top() + 60)
        _bevel_poly(p, _chamfer(left, 16), raised=False, fill="#dadbdd")
        # ថតទទេ (bay) ២ ជួរ — ជួរទី ២ ជាកន្លែង module អង្គុយ
        for r in mods:
            for x, wdt in ((pad, bw), (r.left() - 4, r.width() + 8)):
                bay = QRectF(x, r.top() - 4, wdt, r.height() + 8)
                _bevel_rect(p, bay, 6, raised=False, fill="#dcdddf")
                p.setPen(QPen(QColor("#c4c6c8"), 1.4, cap=Qt.FlatCap))
                if wdt == bw:
                    for k in range(5):
                        y = bay.center().y() - 12 + k * 6
                        p.drawLine(QPointF(bay.center().x() - 9, y), QPointF(bay.center().x() + 9, y))
        # ជើងទម្រកុំព្យូទ័រ (លិច) + រន្ធវិក្កយបត្រ
        _bevel_rect(p, comp.adjusted(-18, -18, 18, 18), 18, raised=False, fill="#d7d8da")
        slot = QRectF(comp.center().x() - comp.width() * 0.42, comp.bottom() + 24, comp.width() * 0.84, 22)
        _bevel_rect(p, slot.adjusted(-10, -8, 10, 8), 8, raised=False, fill="#d9dadc")
        # ថតខាងស្តាំ (បន្ទះផុសខ្ពស់)
        _soft_shadow(p, plate, 10)
        _bevel_rect(p, plate, 10, raised=True, fill="#e7e8e9")
        for k in range(3):  # ចំណុចតូចៗលើបន្ទះ
            for j in range(18):
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#cdcfd1"))
                p.drawEllipse(QPointF(plate.right() - 10 - k * 6, plate.top() + 40 + j * 9), 1.1, 1.1)

        # ផ្លូវខ្សែ 3D (រាង) — កញ្ចប់ទិន្នន័យគូរពេលក្រោយ (មានចលនា)
        xv = L["J"].x()
        ys = [pts[0].y() for pts in L["stubs"]]
        _tracks(p, L["stubs"] + [[QPointF(xv, min(ys)), QPointF(xv, max(ys))], L["trunk"], L["out_trunk"],
                                 L["up"], L["down"]])

        dark = QColor("#2d2f32")
        # រន្ធមូលខ្មៅ
        for ctr, r in ((QPointF(mods[0].left() + 16, mods[0].top() - 34), 7),
                       (QPointF(comp.left() - 46, comp.top() - 30), 9),
                       (QPointF(comp.left() + 30, comp.bottom() + 120), 11),
                       (QPointF(plate.left() - 40, plate.bottom() - 30), 9)):
            if 0 < ctr.y() < H - 8:
                _hole(p, ctr, r)
        # ចំណុចរន្ធ grid (ឧបាល័រ)
        gx, gy = comp.center().x() - 54, comp.top() - 76
        p.setPen(Qt.NoPen)
        for i in range(14):
            for j in range(5):
                p.setBrush(QColor("#ffffff"))
                p.drawEllipse(QPointF(gx + i * 8 + 0.5, gy + j * 7 + 0.6), 1.3, 1.3)
                p.setBrush(QColor("#bfc1c3"))
                p.drawEllipse(QPointF(gx + i * 8, gy + j * 7), 1.3, 1.3)
        # | | | | |  និង barcode
        p.setPen(QPen(dark, 2.4, cap=Qt.FlatCap))
        for i in range(5):
            x = comp.left() + 6 + i * 14
            p.drawLine(QPointF(x, comp.top() - 46), QPointF(x, comp.top() - 38 + (i % 2) * 2))
        for i, wdt in enumerate((3, 2, 4, 2, 3)):
            p.setPen(QPen(dark, wdt, cap=Qt.FlatCap))
            p.drawLine(QPointF(comp.right() - 140 + i * 7, comp.top() - 62), QPointF(comp.right() - 140 + i * 7, comp.top() - 40))
        for i, wdt in enumerate((2, 3, 2, 2, 4)):
            x = comp.left() - 38 + i * 6
            p.setPen(QPen(dark, wdt, cap=Qt.FlatCap))
            p.drawLine(QPointF(x, comp.bottom() + 58), QPointF(x, comp.bottom() + 76))
        # IC ខ្មៅមានជើង (ក្រោមឆ្វេង) + ដុំខ្មៅ ៣ (ស្តាំ)
        ic_y = min(H - 54, mods[-1].bottom() + 34)
        for i in range(2):
            _chip(p, QRectF(pad + 10 + i * 42, ic_y, 32, 22))
        for i in range(3):
            _chip(p, QRectF(plate.left() - 74 + i * 18, comp.bottom() + 40, 13, 30), pins=2)
        # បន្ទះតូចៗ (pads) ក្រោមឆ្វេង
        for i in range(3):
            for j in range(2):
                r = QRectF(mods[-1].right() + 30 + i * 20, mods[-1].bottom() - 40 + j * 18, 14, 10)
                _bevel_rect(p, r, 2, raised=True, fill="#e3e4e5")
        # វីស ៤ ជ្រុង
        for x, y in ((12, 12), (W - 12, 12), (12, H - 12), (W - 12, H - 12)):
            _hole(p, QPointF(x, y), 5, light=True)
        p.end()
        return pm

    # ---------- ខ្សែ: កញ្ចប់ទិន្នន័យ + ចំណុចតភ្ជាប់ ----------
    def _packets(self, p, pts, state, lane=2.6, size=3.4, step=10.0, todo=True):
        """ការ៉េតូចៗជាគូ (ពីរជួរ) តាមខ្សែ — ហូរពេលកំពុងធ្វើ"""
        c = theme.C
        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        length = path.length()
        if length < 4:
            return
        if state == "todo" and not todo:
            return
        if state == "active":
            col, shift = QColor(c["accent"]), (self.phase * 1.8) % step
        elif state == "done":
            col, shift = QColor(c["accent"]), 0.0
            col.setAlpha(120)
        else:
            col, shift = QColor("#cdd0d2"), 0.0
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        d = 6 + shift
        while d < length - 4:
            t = path.percentAtLength(d)
            pt = path.pointAtPercent(t)
            ang = math.radians(path.angleAtPercent(t))
            nx, ny = -math.sin(ang) * lane, -math.cos(ang) * lane  # ពីរជួរ (ស្របខ្សែ)
            for s in (-1, 1):
                p.drawRect(QRectF(pt.x() + s * nx - size / 2, pt.y() + s * ny - size / 2, size, size))
            d += step

    def _node(self, p, ctr, state, big=False):
        c = theme.C
        r = 6.5 if big else 5.0
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(ctr + QPointF(1, 1.6), r, r)
        p.setBrush(QColor("#c3c5c7"))
        p.drawEllipse(ctr, r, r)
        p.setBrush(QColor("#f4f4f3"))
        p.drawEllipse(ctr, r - 1.6, r - 1.6)
        on = state == "active" and (self.phase // 5) % 2 == 0 or state == "done"
        p.setBrush(QColor(c["accent"]) if on else QColor("#9a9ea3"))
        p.drawEllipse(ctr, r * 0.38, r * 0.38)

    def _module(self, p, r, key, state, m):
        c = theme.C
        lift = 2.0 if state == "active" else 0
        r = r.translated(0, -lift)
        _soft_shadow(p, r.translated(0, lift), 6, 7 if lift else 5)
        _bevel_rect(p, r, 6, raised=True, fill="#f7f7f6")
        if state == "active":
            p.setPen(QPen(QColor(c["accent"]), 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 6, 6)
        compact = r.height() < 86
        badge = (QRectF(r.left() + 10, r.center().y() - 11, 46, 22) if compact else
                 QRectF(r.left() + 10, r.top() + 10, 46, 22))  # ផ្លាកខ្មៅ icon (ដូចរូប)
        tx = badge.right() + 12 if compact else r.left() + 10
        ty = r.center().y() - 19 if compact else r.bottom() - 40
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["lcd"]))
        p.drawRoundedRect(badge, 3, 3)
        p.setPen(QColor("#ffffff"))
        p.setFont(_font(self.font(), [], 9.5))
        p.drawText(badge, Qt.AlignCenter, self.icons.get(key, ""))
        led = QColor(c["faint"])
        if state == "done":
            led = QColor(c["accent"])
        elif state == "active":
            led = QColor(c["accent"]) if (self.phase // 6) % 2 == 0 else QColor("#ffc2a8")
        p.setPen(Qt.NoPen)
        p.setBrush(led)
        p.drawEllipse(QPointF(r.right() - 13, r.top() + 15), 4.2, 4.2)
        title, sub = STEP_INFO[key]
        p.setPen(QColor(c["text"]))
        p.setFont(_condensed(self.font(), 10))
        p.drawText(QRectF(tx, ty, r.right() - tx - 10, 18), Qt.AlignLeft | Qt.AlignVCenter, title)
        p.setFont(_font(self.font(), [], 8))
        if state == "active":
            p.setPen(QColor(c["accent"]))
            sub = f"{sub} · {m['stage_pct']}%"
        else:
            p.setPen(QColor(c["muted"]))
            sub += "  ✓" if state == "done" else ""
        p.drawText(QRectF(tx, ty + 17, r.right() - tx - 10, 18), Qt.AlignLeft | Qt.AlignVCenter, sub)

    def _nav(self, p, r):
        """ប៊ូតុងទៅទំព័រ 🏷 Logo & Ads"""
        c = theme.C
        self.hits.append(("nav", r))
        hov = self.hover == "nav"
        rr = r.translated(0, -2 if hov else 0)
        _soft_shadow(p, rr.translated(0, 2 if hov else 0), 6, 7 if hov else 5)
        _bevel_rect(p, rr, 6, raised=True, fill="#f7f7f6")
        if hov:
            p.setPen(QPen(QColor(c["accent"]), 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rr, 6, 6)
        badge = QRectF(rr.left() + 12, rr.center().y() - 15, 58, 30)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["lcd"]))
        p.drawRoundedRect(badge, 4, 4)
        p.setPen(QColor(c["accent"]))
        p.setFont(_font(self.font(), [], 9.5, True))
        p.drawText(badge, Qt.AlignCenter, "🏷 📢")
        tx = badge.right() + 12
        p.setPen(QColor(c["text"]))
        p.setFont(_condensed(self.font(), 11))
        p.drawText(QRectF(tx, rr.top() + 12, rr.right() - tx - 40, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   "LOGO & ADS")
        p.setPen(QColor(c["muted"]))
        p.setFont(_font(self.font(), [], 8))
        p.drawText(QRectF(tx, rr.top() + 34, rr.right() - tx - 40, 34), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                   "ដាក់ Logo · Ads")
        p.setPen(QColor(c["accent"] if hov else c["faint"]))  # ព្រួញ ››
        p.setFont(_font(self.font(), [], 15, True))
        p.drawText(QRectF(rr.right() - 44, rr.top(), 36, rr.height()), Qt.AlignCenter, "››")

    def _render(self, p, r, m):
        c = theme.C
        state = self._stage_state(m, "video")
        done = m["finished"] and m["any_done"]
        if done:
            self.hits.append(("open", r))
        hov = self.hover == "open"
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 26))
        p.drawRoundedRect(r.translated(2, 3), 6, 6)
        p.setBrush(QColor("#ffffff" if hov else "#fbfbfa"))
        p.setPen(QPen(QColor(c["accent"]), 1.6) if state == "active" or hov else QPen(QColor("#cdcfd1"), 1))
        p.drawRoundedRect(r, 6, 6)
        p.setPen(QColor(c["text"]))
        p.setFont(_condensed(self.font(), 9))
        p.drawText(QRectF(r.left() + 12, r.top() + 8, 120, 16), Qt.AlignLeft | Qt.AlignVCenter, "RENDER · OUTPUT")
        badge = QRectF(r.right() - 82, r.top() + 8, 70, 20)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["lcd"]))
        p.drawRoundedRect(badge, 3, 3)
        p.setPen(QColor("#ffffff"))
        p.setFont(_font(self.font(), [], 8.5))
        p.drawText(badge, Qt.AlignCenter, f"{self.icons.get('video', '')}  📂")
        p.setPen(QColor(c["accent"] if state == "active" else c["muted"]))
        p.setFont(_font(self.font(), [], 8))
        sub = ("📂 ចុចបើកថត Output" if done else f"វីដេអូខ្មែរ · {m['stage_pct']}%" if state == "active"
               else "វីដេអូខ្មែរ")
        p.drawText(QRectF(r.left() + 12, r.top() + 28, r.width() - 24, 18), Qt.AlignLeft | Qt.AlignVCenter, sub)
        # LED ២ + tick render
        for i in range(2):
            on = done or (state == "active" and (self.phase // 5 + i) % 2 == 0)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["accent"]) if on else QColor(c["faint"]))
            p.drawEllipse(QPointF(r.left() + 16 + i * 9, r.bottom() - 13), 3, 3)
        frac = 1.0 if done else m["stage_pct"] / 100 if state == "active" else 0
        draw_ticks(p, QRectF(r.left() + 40, r.bottom() - 19, r.width() - 52, 12), frac, c["accent"], "#d3d5d7")

    def _drawers(self, p, area, m):
        """ថតរឿង (ដូចថតម៉ាស៊ីនខាងស្តាំក្នុងរូប): LED ស្ថានភាព · ឈ្មោះ · % · ✕"""
        c = theme.C
        stories = m["stories"]
        p.setPen(QColor(c["muted"]))
        p.setFont(theme.mono(_font(self.font(), [], 7.5)))
        p.drawText(QRectF(area.left(), area.top() - 20, area.width(), 16), Qt.AlignLeft | Qt.AlignVCenter,
                   f"QUEUE · {len(stories)} STORY" if stories else "QUEUE · EMPTY")
        if not stories:
            r = QRectF(area.left(), area.top(), area.width(), 36)
            p.setPen(QPen(QColor("#c3c5c7"), 1.2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 5, 5)
            p.setPen(QColor(c["faint"]))
            p.setFont(_font(self.font(), [], 8))
            p.drawText(r, Qt.AlignCenter, "ទម្លាក់ Folder លើកុំព្យូទ័រ")
            return
        h, gap = 36.0, 9.0
        fit = max(1, int((area.height() + gap) // (h + gap)))
        view = self._viewed(m)
        first = _clamp(view - fit + 1, 0, max(0, len(stories) - fit)) if len(stories) > fit else 0
        shown = stories[first:first + fit]
        for k, s in enumerate(shown):
            i = first + k
            r = QRectF(area.left(), area.top() + k * (h + gap), area.width(), h)
            self.hits.append((f"drawer:{i}", r))
            sel = i == view
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 24))
            p.drawRoundedRect(r.translated(1, 3), 4, 4)
            p.setBrush(QColor("#fbfbfa"))
            p.setPen(QPen(QColor(c["accent"]), 1.4) if sel else QPen(QColor("#cdcfd1"), 1))
            p.drawRoundedRect(r, 4, 4)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#c6c8ca"))  # ដៃទាញថត
            p.drawRect(QRectF(r.center().x() - 14, r.bottom() - 1, 28, 4))
            st = s["status"]
            for j in range(2):
                col = {"done": c["accent"], "failed": c["error"]}.get(st, c["faint"])
                if st == "running":
                    col = c["accent"] if (self.phase // 5 + j) % 2 == 0 else "#ffc2a8"
                p.setBrush(QColor(col))
                p.drawEllipse(QPointF(r.left() + 12 + j * 8, r.center().y()), 2.8, 2.8)
            right = {"running": f"{s['pct']}%", "done": "✓", "failed": "✕"}.get(st, "WAIT")
            removable = not m["running"]
            p.setFont(theme.mono(_font(self.font(), [], 8)))
            fm = p.fontMetrics()
            p.setPen(QColor(c["accent"] if st == "running" else c["success"] if st == "done" else
                            c["error"] if st == "failed" else c["faint"]))
            tail = 30 if removable else 8
            p.drawText(QRectF(r.right() - 70 - tail, r.top(), 70, h), Qt.AlignRight | Qt.AlignVCenter, right)
            p.setPen(QColor(c["text"]))
            p.setFont(_font(self.font(), [], 8.5))
            fm = p.fontMetrics()
            name_w = r.width() - 34 - 76 - tail
            p.drawText(QRectF(r.left() + 30, r.top(), name_w, h), Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(s["name"], Qt.ElideRight, int(name_w)))
            if removable:
                x = QRectF(r.right() - 22, r.top() + 8, 16, h - 16)
                self.hits.append((f"remove:{i}", x))
                p.setPen(QPen(QColor(c["error"] if self.hover == f"remove:{i}" else c["faint"]), 1.6))
                p.drawLine(x.center() + QPointF(-4, -4), x.center() + QPointF(4, 4))
                p.drawLine(x.center() + QPointF(-4, 4), x.center() + QPointF(4, -4))
        more = len(stories) - first - len(shown)
        if more > 0 or first > 0:
            p.setPen(QColor(c["muted"]))
            p.setFont(theme.mono(_font(self.font(), [], 7.5)))
            y = area.top() + len(shown) * (h + gap) - 4
            p.drawText(QRectF(area.left(), y, area.width(), 14), Qt.AlignRight,
                       (f"▲ {first}  " if first else "") + (f"▼ {more}" if more > 0 else ""))

    def _knobs(self, p, centers, m):
        """ប៊ូតុងមូលខ្មៅ ៣ (ដូចរូប): ⚙ ការកំណត់ · ➕ Folder មេ · ✕ សម្អាត"""
        c = theme.C
        for ctr, (name, label, glyph) in zip(centers, (("settings", "SET", "⚙"), ("parent", "ADD", "+"),
                                                        ("clear", "CLR", "×"))):
            enabled = name == "settings" or not m["running"]
            rect = QRectF(ctr.x() - 13, ctr.y() - 13, 26, 36)
            if enabled:
                self.hits.append((name, rect))
            hov = self.hover == name
            _hole(p, ctr, 12)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["accent"]) if hov else QColor(c["lcd"]) if enabled else QColor("#8d9195"))
            p.drawEllipse(ctr, 7.6, 7.6)
            p.setPen(QColor("#ffffff"))
            p.setFont(_font(self.font(), ["Segoe UI Symbol"], 9, True))
            p.drawText(QRectF(ctr.x() - 9, ctr.y() - 10, 18, 18), Qt.AlignCenter, glyph)
            p.setPen(QColor(c["muted"]))
            p.setFont(theme.mono(_font(self.font(), [], 6.5)))
            p.drawText(QRectF(ctr.x() - 15, ctr.y() + 13, 30, 10), Qt.AlignCenter, label)

    def _computer(self, p, r, m):
        c = theme.C
        idle = not m["running"]
        if idle:
            self.hits.append(("pick", r))
        hot = idle and (self.drag or self.hover == "pick")
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 38))
        p.drawRoundedRect(r.translated(3, 7), 11, 11)
        p.setBrush(QColor("#c8cacc"))
        p.drawRoundedRect(r, 11, 11)
        inner = r.adjusted(5, 4, -5, -8)
        p.setBrush(QColor("#fdfdfc"))
        p.drawRoundedRect(inner, 9, 9)
        if hot:  # កន្លែងទម្លាក់ Folder
            pen = QPen(QColor(c["accent"]), 2, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(QColor(255, 90, 31, 18))
            p.drawRoundedRect(inner.adjusted(4, 4, -4, -4), 7, 7)
            p.setPen(Qt.NoPen)
        for i in range(3):
            on = idle or (self.phase // 4) % 3 == i
            p.setBrush(QColor(c["accent"]) if on else QColor("#ffc9b3"))
            p.drawRect(QRectF(inner.left() + 16 + i * 7, inner.top() + 14, 5, 5))
        lc = QPointF(inner.right() - 32, inner.top() + 34)
        p.setBrush(QColor("#efefee"))
        p.setPen(QPen(QColor("#d9dbdc"), 1.4))
        p.drawEllipse(lc, 19, 19)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#8d9195"))
        p.drawPolygon(QPolygonF([QPointF(lc.x(), lc.y() - 9), QPointF(lc.x() + 6.5, lc.y()),
                                 QPointF(lc.x(), lc.y() + 9), QPointF(lc.x() - 6.5, lc.y())]))
        p.setPen(QColor(c["text"]))
        p.setFont(_condensed(self.font(), 19))
        p.drawText(QRectF(inner.left() + 16, inner.top() + 26, inner.width() - 80, 64),
                   Qt.AlignLeft | Qt.AlignTop, "THE DUB\nCOMPUTER")
        # សេចក្តីពិពណ៌នា / កន្លែងទម្លាក់
        p.setFont(theme.mono(_font(self.font(), [], 9.5)))
        fm = p.fontMetrics()
        text_r = QRectF(inner.left() + 16, inner.top() + 96, inner.width() - 32, 70)
        lines = m["lines"]
        if hot:
            lines = ["⤓ ទម្លាក់ Folder រឿងនៅទីនេះ", "ឬចុចដើម្បីជ្រើស Folder"]
        p.setPen(QColor(c["accent"] if hot else c["muted"]))
        p.drawText(text_r, Qt.AlignLeft | Qt.AlignTop,
                   "\n".join(fm.elidedText(t, Qt.ElideRight, int(text_r.width())) for t in lines[:3]))
        if hot or not m["stories"]:  # កន្លែងទម្លាក់: រូប Folder ធំនៅចំកណ្តាល
            well = QRectF(inner.left() + 16, text_r.bottom(), inner.width() - 32, inner.bottom() - 74 - text_r.bottom())
            if well.height() > 50:
                p.setPen(QPen(QColor(c["accent"] if hot else "#c3c5c7"), 1.4, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(well, 6, 6)
                p.setPen(QColor(c["text"]))
                p.setFont(_font(self.font(), [], min(30.0, well.height() * 0.3)))
                p.drawText(well.adjusted(0, 0, 0, -18), Qt.AlignCenter, "📂")
                p.setPen(QColor(c["accent"] if hot else c["muted"]))
                p.setFont(_font(self.font(), [], 9))
                p.drawText(well.adjusted(0, 0, 0, -8), Qt.AlignHCenter | Qt.AlignBottom, "ទម្លាក់ Folder រឿង · ឬចុច")
        self._lcd(p, QRectF(inner.left() + 12, inner.bottom() - 62, inner.width() - 24, 50), m)

    def _lcd(self, p, bar, m):
        """LCD ‹ › — រឿងមួយៗ: ឈ្មោះ + ជំហាន + progress tick"""
        c = theme.C
        p.setPen(QPen(QColor("#c5c7c9"), 1))
        p.setBrush(QColor("#ededec"))
        p.drawRoundedRect(bar, 5, 5)
        lcd = bar.adjusted(28, 4, -28, -4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["lcd"]))
        p.drawRoundedRect(lcd, 3, 3)
        stories = m["stories"]
        for name, x in (("prev", bar.left()), ("next", bar.right() - 28)):
            ar = QRectF(x, bar.top(), 28, bar.height())
            if len(stories) > 1:
                self.hits.append((name, ar))
            p.setPen(QColor(c["accent"] if self.hover == name else c["muted"] if len(stories) > 1 else "#c3c5c7"))
            p.setFont(_font(self.font(), [], 12, True))
            p.drawText(ar, Qt.AlignCenter, "‹" if name == "prev" else "›")
        p.setFont(theme.mono(_font(self.font(), [], 8.3)))
        fm = p.fontMetrics()
        if not stories:
            p.setPen(QColor(c["lcd_text"]))
            p.drawText(lcd, Qt.AlignCenter, "INSERT FOLDER")
            return
        i = self._viewed(m)
        s = stories[i]
        st = s["status"]
        stage = STEP_INFO.get(s.get("stage") or "", ("", ""))[0]
        tag = {"running": stage or "RUN", "done": "DONE", "failed": "FAIL"}.get(st, "WAIT")
        head = f"{i + 1:02d}/{len(stories):02d} "
        tag_w = fm.horizontalAdvance(tag) + 6
        name_w = lcd.width() - 16 - fm.horizontalAdvance(head) - tag_w
        top = QRectF(lcd.left() + 8, lcd.top() + 3, lcd.width() - 16, 18)
        p.setPen(QColor(c["lcd_text"]))
        p.drawText(top, Qt.AlignLeft | Qt.AlignVCenter, head + fm.elidedText(s["name"], Qt.ElideRight, int(name_w)))
        p.setPen(QColor(c["accent"] if st == "running" else "#7ee2a2" if st == "done" else
                        "#ff8a7a" if st == "failed" else c["faint"]))
        p.drawText(top, Qt.AlignRight | Qt.AlignVCenter, tag)
        pct = 100 if st == "done" else s["pct"]
        ticks = QRectF(lcd.left() + 8, lcd.bottom() - 17, lcd.width() - 64, 12)
        n = int(ticks.width() // 6)
        lit = round(pct / 100 * n)
        for k in range(n):
            on = k < lit
            if st == "running" and k == lit and (self.phase // 4) % 2 == 0:
                on = True  # tick បន្ទាប់ភ្លឹបភ្លែត
            p.setBrush(QColor(c["accent"]) if on else QColor("#45484c"))
            p.drawRect(QRectF(ticks.left() + k * 6, ticks.top() + 2, 3.5, ticks.height() - 4))
        p.setPen(QColor(c["accent"] if st == "running" else c["lcd_text"]))
        p.drawText(QRectF(lcd.right() - 56, ticks.top() - 3, 48, 18), Qt.AlignRight | Qt.AlignVCenter, f"{pct}%")

    def _start(self, p, comp, m):
        """រន្ធ (វិក្កយបត្រចេញ) + ប៊ូតុងការ៉េ START/STOP (ដូច "GET EARLY ACCESS")"""
        c = theme.C
        slot = QRectF(comp.center().x() - comp.width() * 0.42, comp.bottom() + 24, comp.width() * 0.84, 22)
        self.slot_rect = slot
        _bevel_rect(p, slot, 5, raised=True, fill="#efefee")
        inner = slot.adjusted(8, 4, -8, -4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["lcd"]))
        p.drawRoundedRect(inner, 3, 3)
        frac = 1.0 if m["finished"] else m["overall"] / 100 if m["running"] else 0
        p.setBrush(QColor(c["accent"]))
        p.drawRect(QRectF(inner.left() + 2, inner.top() + 2, max(0.0, (inner.width() - 4) * frac), inner.height() - 4))
        if m["running"]:
            name, glyph, label, enabled = "stop", "■", "STOP" if m["can_stop"] else "STOPPING...", m["can_stop"]
        else:
            name, glyph, label, enabled = "start", "▶", "START DUB", m["can_start"]
        b = QRectF(comp.center().x() - 30, slot.bottom() + 26, 60, 60)
        if enabled:
            self.hits.append((name, QRectF(b.left() - 20, b.top(), b.width() + 40, b.height() + 26)))
        hov = self.hover == name
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawRoundedRect(b.translated(1, 4), 9, 9)
        p.setBrush(QColor("#c8cacc"))
        p.drawRoundedRect(b, 9, 9)
        face = b.adjusted(3, 2, -3, -5)
        p.setBrush(QColor("#ffffff" if hov else "#f7f7f6"))
        p.drawRoundedRect(face, 7, 7)
        p.setBrush(QColor(c["accent"]) if enabled else QColor(c["faint"]))
        p.drawEllipse(face.center(), 13, 13)
        p.setPen(QColor("#ffffff"))
        p.setFont(_font(self.font(), ["Segoe UI Symbol"], 10, True))
        p.drawText(QRectF(face.center().x() - 13, face.center().y() - 13, 26, 26), Qt.AlignCenter, glyph)
        p.setPen(QColor(c["text"] if enabled else c["faint"]))
        p.setFont(_condensed(self.font(), 8.5))
        p.drawText(QRectF(b.left() - 40, b.bottom() + 6, b.width() + 80, 16), Qt.AlignCenter, label)


# ---------------- ឧបករណ៍គូរ (bevel 3D, ខ្សែ, រន្ធ, IC) ----------------

def _route45(a, b, diag_first=False):
    """ផ្លូវខ្សែបត់ 45° ពី a ទៅ b (ដូចបន្ទះសៀគ្វី) — ត្រង់ + ទ្រេត"""
    dx, dy = b.x() - a.x(), b.y() - a.y()
    sx, sy = (1 if dx >= 0 else -1), (1 if dy >= 0 else -1)
    if abs(dy) <= abs(dx):
        mid = (QPointF(a.x() + sx * abs(dy), b.y()) if diag_first else QPointF(b.x() - sx * abs(dy), a.y()))
    else:
        mid = (QPointF(b.x(), a.y() + sy * abs(dx)) if diag_first else QPointF(a.x(), b.y() - sy * abs(dx)))
    return [a, mid, b]


def _chamfer(r, k):
    return QPolygonF([QPointF(r.left() + k, r.top()), QPointF(r.right() - k, r.top()), QPointF(r.right(), r.top() + k),
                      QPointF(r.right(), r.bottom() - k), QPointF(r.right() - k, r.bottom()),
                      QPointF(r.left() + k, r.bottom()), QPointF(r.left(), r.bottom() - k),
                      QPointF(r.left(), r.top() + k)])


def _bevel_pen(rect, raised, width=1.6):
    """គែមភ្លឺខាងលើ-ឆ្វេង ងងឹតខាងក្រោម-ស្តាំ (ផុស) ឬផ្ទុយមកវិញ (លិច)"""
    g = QLinearGradient(rect.topLeft(), rect.bottomRight())
    hi, lo = QColor("#ffffff"), QColor("#b4b7ba")
    g.setColorAt(0, hi if raised else lo)
    g.setColorAt(1, lo if raised else hi)
    return QPen(QBrush(g), width)


def _bevel_poly(p, poly, raised, fill):
    p.setBrush(QColor(fill))
    p.setPen(_bevel_pen(poly.boundingRect(), raised))
    p.drawPolygon(poly)


def _bevel_rect(p, rect, radius, raised, fill):
    if raised:
        g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        g.setColorAt(0, QColor(fill).lighter(104))
        g.setColorAt(1, QColor(fill).darker(103))
        p.setBrush(g)
    else:
        p.setBrush(QColor(fill))
    p.setPen(_bevel_pen(rect, raised))
    p.drawRoundedRect(rect, radius, radius)
    if not raised:  # ស្រមោលខាងក្នុង (លិចចូល)
        p.setPen(QPen(QColor(0, 0, 0, 22), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect.adjusted(1.5, 1.5, -0.5, -0.5), radius, radius)


def _soft_shadow(p, rect, radius, depth=6):
    p.setPen(Qt.NoPen)
    for i in range(depth, 0, -1):
        p.setBrush(QColor(0, 0, 0, 7))
        p.drawRoundedRect(rect.translated(0, i * 0.8).adjusted(-i * 0.4, 0, i * 0.4, i * 0.4), radius + i * 0.5,
                          radius + i * 0.5)


def _tracks(p, lines, w=12.0):
    """ផ្លូវខ្សែផុស 3D: ស្រមោល → គែម → ខ្លួន → ពន្លឺ (ស្រទាប់នីមួយៗគូរគ្រប់ខ្សែ មុនស្រទាប់បន្ទាប់)"""
    path = QPainterPath()
    for pts in lines:
        path.moveTo(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
    p.setBrush(Qt.NoBrush)
    for pen, offset in ((QPen(QColor(0, 0, 0, 34), w + 1), QPointF(1.2, 2.2)),
                        (QPen(QColor("#c1c3c6"), w), QPointF(0, 0)),
                        (QPen(QColor("#eceded"), w - 3), QPointF(0, 0)),
                        (QPen(QColor(255, 255, 255, 150), 1.2), QPointF(-1.2, -1.2))):
        pen.setCapStyle(Qt.SquareCap)
        pen.setJoinStyle(Qt.MiterJoin)
        p.setPen(pen)
        p.drawPath(path.translated(offset))


def _hole(p, ctr, r, light=False):
    """រន្ធមូល: រង្វង់ផុស + ចំកណ្តាលខ្មៅ (ឬភ្លឺ សម្រាប់វីស)"""
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 36))
    p.drawEllipse(ctr + QPointF(0.8, 1.4), r + 1, r + 1)
    g = QLinearGradient(ctr - QPointF(r, r), ctr + QPointF(r, r))
    g.setColorAt(0, QColor("#ffffff"))
    g.setColorAt(1, QColor("#b9bcbf"))
    p.setBrush(g)
    p.drawEllipse(ctr, r + 1, r + 1)
    p.setBrush(QColor("#e9eaea") if light else QColor("#2b2d30"))
    p.drawEllipse(ctr, r * 0.62, r * 0.62)
    p.setBrush(QColor(255, 255, 255, 150))
    p.drawEllipse(ctr - QPointF(r * 0.22, r * 0.22), r * 0.16, r * 0.16)


def _chip(p, rect, pins=4):
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#a9acaf"))
    step = rect.width() / (pins + 1)
    for k in range(pins):
        x = rect.left() + step * (k + 1) - 1
        p.drawRect(QRectF(x, rect.bottom() - 1, 2, 4))
        p.drawRect(QRectF(x, rect.top() - 3, 2, 4))
    p.setBrush(QColor(0, 0, 0, 40))
    p.drawRoundedRect(rect.translated(1, 2), 2, 2)
    p.setBrush(QColor("#2d2f32"))
    p.drawRoundedRect(rect, 2, 2)
    p.setBrush(QColor(255, 255, 255, 40))
    p.drawRect(QRectF(rect.left() + 2, rect.top() + 1.5, rect.width() - 4, 1.2))


class SlideOverlay(QWidget):
    """រូបទំព័រចាស់ និងថ្មី រំកិលជាមួយគ្នា (forward = ទៅស្តាំ → ថ្មីចូលពីស្តាំ) រួចបាត់"""

    def __init__(self, parent, old_pm, new_pm, forward=True, ms=520):
        super().__init__(parent)
        self.old, self.new, self.forward = old_pm, new_pm, forward
        self.t = 0.0
        self.setGeometry(parent.rect())
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(ms)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.valueChanged.connect(self._step)
        anim.finished.connect(self.deleteLater)
        self.show()
        self.raise_()
        anim.start()

    def _step(self, v):
        self.t = float(v)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        w = self.width()
        d = 1 if self.forward else -1
        x_old = -d * w * self.t
        x_new = d * w * (1 - self.t)
        p.drawPixmap(int(x_old), 0, self.old)
        p.fillRect(QRectF(x_old, 0, w, self.height()), QColor(20, 22, 26, int(110 * self.t)))  # ទំព័រចាស់ងងឹត
        p.drawPixmap(int(x_new), 0, self.new)
        edge = x_new if self.forward else x_new + w  # ស្រមោលគែមទំព័រថ្មី
        g = QLinearGradient(edge - 18 * d, 0, edge, 0)
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(0, 0, 0, 60))
        p.fillRect(QRectF(min(edge, edge - 18 * d), 0, 18, self.height()), g)
