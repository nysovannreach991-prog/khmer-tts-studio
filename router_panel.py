"""🔀 Gemini Router Dashboard — ផ្ទាំងក្នុងទំព័រ Auto (បត់បាន) តាមគំរូ Dashboard របស់ Router:
🌐 Live Traffic Flow (Orbit: Router នៅកណ្តាល · គណនីជុំវិញ · ចំណុចរត់ពេល streaming)
👤 Configured Account Profiles (plan · usage · weekly · concurrency · requests · Pause / Off / Open Chrome / Delete)
📜 Live Request Logs (ពេល · req id · គណនី · model · status · tokens in/out · latency)
ទិន្នន័យពី gemini_direct (engine ក្នុងកម្មវិធី) ធ្វើឱ្យស្រស់រៀងរាល់ 1.5វិ ពេលផ្ទាំងបើក · Router server ខាងក្រៅ → ប៊ូតុងបើក Dashboard វា។
"""
import math
import random
import threading
import time

from PyQt5.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QUrl, QVariantAnimation, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient
from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

import gemini_direct
import theme

STATUS_KH = {"ready": "Ready", "needs_login": "ត្រូវចូល Google", "paused": "ផ្អាក", "error": "Error",
             "initializing": "កំពុងបើក", "logging_in": "កំពុងចូល", "manual_control": "Chrome បើក (manual)"}
# ពណ៌ Orbit (ផ្ទៃ LCD ខ្មៅ): streaming · ready · paused · offline
ORBIT = {"stream": "#00FFFF", "ready": "#00FF00", "paused": "#FFB020", "offline": "#FF4D4D", "line": "#0E5560",
         "ring": "#0E5560", "text": "#9FF3FF", "muted": "#5FC4D0", "card": "#07131F", "core_a": "#00FFFF", "core_b": "#0080FF"}


def _kind(a):
    if a.get("is_busy"):
        return "stream"
    if a.get("is_paused"):
        return "paused"
    if a.get("is_cooling_down") or a.get("raw_status") not in ("ready",):
        return "offline" if a.get("raw_status") in ("needs_login", "error", "initializing") else "paused"
    return "ready"


def _rgba(hex_color, alpha):
    c = QColor(hex_color)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


class TrafficFlow(QWidget):
    """Orbit 3D: Router (ស្វ៊ែរ wireframe) អណ្តែតកណ្តាល · គណនីវិលជុំវិញលើថាសផ្អៀង (perspective: ជិត=ធំ · ឆ្ងាយ=តូច/ស្រអាប់)
    · កញ្ចប់ទិន្នន័យរត់តាមខ្សែកោង ពេលគណនីកំពុងឆ្លើយ · អូស mouse ឆ្វេង/ស្តាំ = បង្វិល · reduced motion → រូបនៅស្ងៀម"""
    DEPTH = 0.25      # perspective: មាត្រដ្ឋាន 1±DEPTH តាមជម្រៅ
    LIFT = 26         # គណនីអណ្តែតលើថាស (px)
    REV_S = 80.0      # វិល 1 ជុំ (វិនាទី)

    def __init__(self):
        super().__init__()
        self.accounts, self.inflight, self.ready_txt = [], {}, ""
        self._still = theme.reduced_motion()
        self._t0 = time.monotonic()
        self._spin, self._drag = 0.35, None
        rnd = random.Random(7)
        self._stars = [(rnd.random(), rnd.random(), rnd.random() * 6.28, 0.5 + rnd.random() * 1.1) for _ in range(48)]
        self.setMinimumHeight(170)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("អូសឆ្វេង/ស្តាំ ដើម្បីបង្វិល Orbit")
        self.setAccessibleName("Live Traffic Flow")
        self._anim = QTimer(self)
        self._anim.setInterval(33)  # ~30fps តែពេល tab នេះបើក
        self._anim.timeout.connect(self.update)

    def showEvent(self, e):
        super().showEvent(e)
        if not self._still:
            self._anim.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._anim.stop()

    def mousePressEvent(self, e):
        self._drag = e.x()
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self._spin += (e.x() - self._drag) * 0.008
            self._drag = e.x()
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)

    def update_data(self, accounts, logs):
        self.accounts = accounts
        self.inflight = {}
        for e in logs:
            if e.get("status") == "in_flight":
                self.inflight[e.get("account_id")] = self.inflight.get(e.get("account_id"), 0) + 1
        self.update()

    # ---------- គណិត 3D ----------
    def _proj(self, ang, rad=1.0, lift=0.0):
        """ចំណុចលើថាស (មុំ ang · កាំ rad) → (x, y, scale) លើអេក្រង់ — sin>0 = ជិតអ្នកមើល"""
        cx, cy, rx, ry = self._g
        sc = 1 + self.DEPTH * math.sin(ang) * rad
        return cx + rx * rad * math.cos(ang) * sc, cy + ry * rad * math.sin(ang) * sc - lift * sc, sc

    def _ring(self, rad, a0=0.0, a1=2 * math.pi, steps=72):
        pts = []
        for i in range(steps + 1):
            x, y, _ = self._proj(a0 + (a1 - a0) * i / steps, rad)
            pts.append(QPointF(x, y))
        return QPolygonF(pts)

    def paintEvent(self, e):
        o = ORBIT
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        t = 0.0 if self._still else time.monotonic() - self._t0
        rot = self._spin + 2 * math.pi * t / self.REV_S
        p.fillRect(self.rect(), QColor(theme.C["lcd"]))
        f = theme.mono(self.font())
        p.setFont(f)
        fm = p.fontMetrics()
        cx, cy = w / 2, h * 0.45
        ry = max(26.0, min((cy - 60) / (1 - self.DEPTH), (h - cy - 56) / (1 + self.DEPTH)))
        rx = max(70.0, (w / 2 - 96) / (1 + self.DEPTH))
        ry = min(ry, rx * 0.5)
        self._g = (cx, cy, rx, ry)
        self._k = max(1.0, min(1.7, ry / 62))  # ផ្ទាំងធំ → core និងគណនីធំតាម
        n = len(self.accounts)
        busy_any = any(_kind(a) == "stream" or self.inflight.get(a.get("account_id")) for a in self.accounts)

        # ផ្កាយ (ភ្លឹបភ្លែតស្រាល)
        p.setPen(Qt.NoPen)
        for sx, sy, ph, sz in self._stars:
            p.setBrush(_rgba(o["text"], 50 + 55 * (0.5 + 0.5 * math.sin(t * 1.3 + ph))))
            p.drawEllipse(QPointF(sx * w, sy * h), sz, sz)

        # ថាស: ពន្លឺក្រោម core · រង្វង់ · ឆ្អឹង · សញ្ញាដឺក្រេវិលបញ្ច្រាស
        g = QRadialGradient(QPointF(cx, cy), rx * 0.75)
        g.setColorAt(0, _rgba(o["core_b"], 70 if n else 30))
        g.setColorAt(1, _rgba(o["core_b"], 0))
        p.save()
        p.translate(cx, cy)
        p.scale(1, ry / rx)
        p.translate(-cx, -cy)
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), rx * 0.75, rx * 0.75)
        p.restore()
        p.setBrush(Qt.NoBrush)
        for k in range(12):
            a = -rot * 0.5 + k * math.pi / 6
            x0, y0, _ = self._proj(a, 0.3)
            x1, y1, s1 = self._proj(a, 1.0)
            p.setPen(QPen(_rgba(o["ring"], 130 + 80 * (s1 - 1) / self.DEPTH), 1))
            p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        p.setPen(QPen(_rgba(o["stream"], 38), 4))
        p.drawPolyline(self._ring(1.0))
        p.setPen(QPen(_rgba(o["stream"], 150), 1.1))
        p.drawPolyline(self._ring(1.0))
        p.setPen(QPen(QColor(o["ring"]), 1, Qt.DashLine))
        p.drawPolyline(self._ring(0.64))
        p.setPen(QPen(_rgba(o["core_b"], 150), 1))
        p.drawPolyline(self._ring(0.3))
        for k in range(48):
            a = -rot * 0.5 + k * math.pi / 24
            x0, y0, s0 = self._proj(a, 1.03)
            x1, y1, _ = self._proj(a, 1.10 if k % 4 == 0 else 1.06)
            p.setPen(QPen(_rgba(o["stream"], 60 + 150 * (s0 - (1 - self.DEPTH)) / (2 * self.DEPTH)), 1))
            p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        if n and not self._still:  # radar: ផ្នែកពន្លឺបោសលើថាស
            sa = 2 * math.pi * (t / 6.0)
            p.setPen(Qt.NoPen)
            for k in range(14):
                a1, a0 = sa - k * 0.06, sa - (k + 1) * 0.06
                p.setBrush(_rgba(o["stream"], 52 * (1 - k / 14)))
                p.drawPolygon(QPolygonF([QPointF(cx, cy)] + list(self._ring(1.0, a0, a1, 2))))
            x1, y1, _ = self._proj(sa, 1.0)
            p.setPen(QPen(_rgba(o["stream"], 170), 1.2))
            p.drawLine(QPointF(cx, cy), QPointF(x1, y1))

        # ទីតាំងគណនី (វិលជាមួយថាស) — គូរពីឆ្ងាយទៅជិត · core នៅកណ្តាលលំដាប់
        ccy = cy - 16 * self._k + (0 if self._still else 3 * math.sin(t * 1.5))  # core អណ្តែត + យោលស្រាល
        nodes = []
        for i, a in enumerate(self.accounts):
            ang = -math.pi / 2 + 2 * math.pi * i / max(1, n) + rot
            fx, fy, sc = self._proj(ang)
            nodes.append((math.sin(ang), i, a, fx, fy, sc))
        nodes.sort(key=lambda v: v[0])
        for nd in nodes:
            if nd[0] < 0:
                self._node(p, fm, t, cx, ccy, *nd)
        p.setOpacity(1.0)
        self._core(p, t, cx, cy, ccy, n, busy_any)
        for nd in nodes:
            if nd[0] >= 0:
                self._node(p, fm, t, cx, ccy, *nd)

        # HUD: legend (ឆ្វេងលើ) · ស្ថានភាព (ស្តាំលើ) · readout (ឆ្វេងក្រោម)
        p.setOpacity(1.0)
        x = 12
        for key, label in (("stream", "Streaming"), ("ready", "Ready"), ("paused", "Paused"), ("offline", "Offline")):
            p.setBrush(QColor(o[key]))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(x, 10, 8, 8))
            p.setPen(QColor(o["muted"]))
            tw = fm.horizontalAdvance(label)
            p.drawText(QRectF(x + 12, 2, tw + 4, 24), Qt.AlignLeft | Qt.AlignVCenter, label)
            x += 12 + tw + 18
        cap = "GEMINI ROUTER · " + (self.ready_txt or "no accounts")
        p.setPen(QColor(o["ready"] if n else o["muted"]))
        p.drawText(QRectF(w - fm.horizontalAdvance(cap) - 16, 2, fm.horizontalAdvance(cap) + 8, 24),
                   Qt.AlignRight | Qt.AlignVCenter, cap)
        p.setPen(QColor(theme.C["faint"]))
        ro = f"ROT {math.degrees(rot) % 360:05.1f}°  NODES {n:02d}  IN-FLIGHT {sum(self.inflight.values()):02d}"
        p.drawText(QRectF(12, h - 24, fm.horizontalAdvance(ro) + 8, 20), Qt.AlignLeft | Qt.AlignVCenter, ro)

    def _core(self, p, t, cx, cy, ccy, n, busy):
        o = ORBIT
        r = 22 * self._k + (0 if self._still else (2.2 if busy else 1.2) * math.sin(t * (4.2 if busy else 2.0)))
        live = 1.0 if n else 0.45
        c = QPointF(cx, ccy)
        p.setPen(QPen(_rgba(o["stream"], 60 * live), 1, Qt.DashLine))  # ដើមពន្លឺ core → ថាស
        p.drawLine(QPointF(cx, ccy), QPointF(cx, cy))
        halo = QRadialGradient(c, r * 2.6)
        halo.setColorAt(0, _rgba(o["core_a"], 120 * live))
        halo.setColorAt(0.45, _rgba(o["core_b"], 50 * live))
        halo.setColorAt(1, _rgba(o["core_b"], 0))
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(c, r * 2.6, r * 2.6)
        ball = QRadialGradient(c, r, QPointF(cx - r * 0.38, ccy - r * 0.42))
        ball.setColorAt(0, _rgba("#E8FFFF", 255 * live))
        ball.setColorAt(0.3, _rgba(o["core_a"], 255 * live))
        ball.setColorAt(0.8, _rgba(o["core_b"], 255 * live))
        ball.setColorAt(1, QColor("#03204A"))
        p.setBrush(ball)
        p.drawEllipse(c, r, r)
        # wireframe: ខ្សែវណ្ឌវិល + ខ្សែស្រប
        p.save()
        clip = QPainterPath()
        clip.addEllipse(c, r, r)
        p.setClipPath(clip)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_rgba("#031428", 120), 1))
        for k in range(4):
            p.drawEllipse(c, r * abs(math.cos(t * 0.6 + k * math.pi / 4)), r)
        for lat in (-0.55, 0.0, 0.55):
            lr = r * math.sqrt(1 - lat * lat)
            p.drawEllipse(QPointF(cx, ccy + r * lat), lr, lr * 0.28)
        p.restore()
        # រង្វង់ gyroscope ២ + ផ្កាយរណប
        for deg, spd, ra, rb, sat in ((24, 16, 1.75, 0.5, 1.7), (-38, -11, 2.1, 0.66, -1.1)):
            p.save()
            p.translate(cx, ccy)
            p.rotate(deg + t * spd)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(_rgba(o["stream"], 150 * live), 1))
            p.drawEllipse(QPointF(0, 0), r * ra, r * rb)
            sa = t * sat
            p.setPen(Qt.NoPen)
            p.setBrush(_rgba("#E8FFFF", 255 * live))
            p.drawEllipse(QPointF(r * ra * math.cos(sa), r * rb * math.sin(sa)), 2.4, 2.4)
            p.restore()

    def _node(self, p, fm, t, cx, ccy, depth, i, a, fx, fy, sc):
        o = ORBIT
        w = self.width()
        k = _kind(a)
        acc_id = a.get("account_id", "")
        busy = k == "stream" or bool(self.inflight.get(acc_id))
        col = o["stream"] if busy else o[k]
        nx, ny = fx, fy - self.LIFT * self._k * sc
        p.setOpacity(0.5 + 0.5 * (depth + 1) / 2)  # ឆ្ងាយ = ស្រអាប់
        # ខ្សែកោង core → គណនី
        ctrl = QPointF((cx + nx) / 2, min(ccy, ny) - 30 * sc)
        path = QPainterPath(QPointF(cx, ccy))
        path.quadTo(ctrl, QPointF(nx, ny))
        p.setBrush(Qt.NoBrush)
        if busy:
            p.setPen(QPen(_rgba(col, 45), 5))
            p.drawPath(path)
            p.setPen(QPen(QColor(col), 1.6))
        elif k == "ready":
            p.setPen(QPen(_rgba(o["ready"], 110), 1))
        else:
            p.setPen(QPen(_rgba(o[k], 90), 1, Qt.DashLine))
        p.drawPath(path)

        def at(u):
            v = 1 - u
            return QPointF(v * v * cx + 2 * v * u * ctrl.x() + u * u * nx, v * v * ccy + 2 * v * u * ctrl.y() + u * u * ny)
        p.setPen(Qt.NoPen)
        if busy and not self._still:  # កញ្ចប់ទិន្នន័យ + កន្ទុយ: សំណើចេញ (cyan) · ចម្លើយត្រឡប់ (ស)
            for j in range(3):
                u = (t * 0.55 + j / 3) % 1.0
                for q in range(6):
                    uu = u - q * 0.022
                    if uu > 0:
                        p.setBrush(_rgba(col, 255 * (1 - q / 6)))
                        p.drawEllipse(at(uu), (3.4 - q * 0.45) * sc, (3.4 - q * 0.45) * sc)
            for j in range(2):
                u = 1 - (t * 0.4 + j / 2) % 1.0
                for q in range(4):
                    uu = u + q * 0.022
                    if uu < 1:
                        p.setBrush(_rgba("#E8FFFF", 230 * (1 - q / 4)))
                        p.drawEllipse(at(uu), (2.4 - q * 0.4) * sc, (2.4 - q * 0.4) * sc)
        elif k == "ready" and not self._still:  # heartbeat យឺត (គណនីរួចរាល់)
            u = (t * 0.22 + i * 0.37) % 1.0
            p.setBrush(_rgba(o["ready"], 190 * math.sin(math.pi * u)))
            p.drawEllipse(at(u), 2.2 * sc, 2.2 * sc)
        # ស្រមោលលើថាស + ដើម
        p.setBrush(_rgba(col, 60))
        p.drawEllipse(QPointF(fx, fy), 13 * self._k * sc, 4.2 * self._k * sc)
        p.setPen(QPen(_rgba(col, 130), 1))
        p.drawLine(QPointF(fx, fy), QPointF(nx, ny))
        r = 11 * self._k * sc
        if busy and not self._still:  # រលកពង្រីក
            p.setBrush(Qt.NoBrush)
            for j in range(2):
                u = (t * 0.9 + j / 2) % 1.0
                p.setPen(QPen(_rgba(col, 200 * (1 - u)), 1.3))
                p.drawEllipse(QPointF(nx, ny), r + 3 + 16 * u * sc, r + 3 + 16 * u * sc)
        glow = QRadialGradient(QPointF(nx, ny), r * 2.2)
        glow.setColorAt(0, _rgba(col, 110))
        glow.setColorAt(1, _rgba(col, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(nx, ny), r * 2.2, r * 2.2)
        ball = QRadialGradient(QPointF(nx, ny), r, QPointF(nx - r * 0.38, ny - r * 0.42))
        ball.setColorAt(0, QColor("#FFFFFF"))
        ball.setColorAt(0.35, QColor(col))
        ball.setColorAt(1, QColor(col).darker(330))
        p.setBrush(ball)
        p.setPen(QPen(QColor(col), 1.2))
        p.drawEllipse(QPointF(nx, ny), r, r)
        p.setPen(QColor(theme.C["on_accent"]))
        fb = p.font()
        bold = theme.mono(self.font())
        bold.setBold(True)
        p.setFont(bold)
        p.drawText(QRectF(nx - r, ny - r, 2 * r, 2 * r), Qt.AlignCenter, acc_id[:1].upper() or "?")
        p.setFont(fb)
        # ស្លាក (ក្រោមចំណុចលើថាស)
        sub = STATUS_KH.get("paused" if a.get("is_paused") else a.get("raw_status", ""), a.get("raw_status", ""))
        if a.get("is_cooling_down"):
            sub = f"cooldown {a.get('cooldown_remaining_seconds', 0)}s"
        elif busy:
            sub = f"streaming · {self.inflight.get(acc_id, 1)}"
        short = acc_id[:18 if w >= 820 else 11]
        if depth < -0.15:  # ខាងក្រោយ: ស្លាក 1 ជួរ លើគណនី (កុំបាំង core និងស្លាកខាងមុខ)
            tag = f"{short} · {sub}"
            tw = fm.horizontalAdvance(tag) + 12
            box = QRectF(max(4, min(w - tw - 4, nx - tw / 2)), max(26, ny - r - 22), tw, 17)
            p.setPen(Qt.NoPen)
            p.setBrush(_rgba(o["card"], 190))
            p.drawRect(box)
            p.setPen(QColor(col))
            p.drawText(box, Qt.AlignCenter, tag)
            return
        line1, line2 = f"{short}  {a.get('plan', '')}".strip(), f"{sub} · {a.get('total_requests', 0)}"
        bw = max(120, fm.horizontalAdvance(line1), fm.horizontalAdvance(line2)) + 20
        box = QRectF(max(4, min(w - bw - 4, fx - bw / 2)), fy + 8, bw, 34)
        p.setPen(QPen(_rgba(col, 150) if busy else QColor(o["ring"]), 1))
        p.setBrush(_rgba(o["card"], 225))
        p.drawRect(box)
        p.setPen(QPen(QColor(col), 1.4))  # ជ្រុង bracket តូច
        for bx, by, dx, dy in ((box.left(), box.top(), 5, 5), (box.right(), box.bottom(), -5, -5)):
            p.drawLine(QPointF(bx, by), QPointF(bx + dx, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy))
        p.setPen(QColor(o["text"]))
        p.drawText(QRectF(box.left(), box.top() + 1, bw, 17), Qt.AlignCenter, line1)
        p.setPen(QColor(col))
        p.drawText(QRectF(box.left(), box.top() + 17, bw, 16), Qt.AlignCenter, line2)


class RouterPanel(QFrame):
    collapsed_changed = pyqtSignal(bool)
    EXPANDED_H = 340

    def __init__(self):
        super().__init__()
        self.setObjectName("routerPanel")
        self._collapsed = False
        self._fold = None
        self._busy_action = False
        self._done_note = None
        self._row_ids = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(8)
        head = QHBoxLayout()
        self.btn_toggle = QPushButton("▾  🔀 Gemini Router Dashboard")
        self.btn_toggle.setObjectName("panelHead")
        self.btn_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_toggle.clicked.connect(lambda: self.set_collapsed(not self._collapsed))
        head.addWidget(self.btn_toggle)
        self.summary = QLabel("")
        self.summary.setObjectName("simpleMuted")
        head.addWidget(self.summary, 1)
        self.live = QLabel("● LIVE")
        self.live.setObjectName("liveChip")
        head.addWidget(self.live)
        self.btn_check = QPushButton("🩺 Check All Accounts")
        self.btn_check.setObjectName("ghost")
        self.btn_check.setToolTip("ផ្ញើសំណើខ្លីមួយទៅគណនី Gemini នីមួយៗ ដើម្បីដឹងថាដំណើរការទាំងអស់ឬអត់ (~10–40វិ)")
        self.btn_check.clicked.connect(self._check_all)
        head.addWidget(self.btn_check)
        self.btn_web = QPushButton("🌐 Dashboard ក្នុង browser")
        self.btn_web.setObjectName("ghost")
        self.btn_web.setToolTip("បើក Dashboard របស់ Router server ខាងក្រៅ (port 5550)")
        self.btn_web.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(f"http://127.0.0.1:{gemini_direct.ROUTER_PORT}")))
        self.btn_web.hide()
        head.addWidget(self.btn_web)
        lay.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("routerTabs")
        self.tabs.tabBar().setElideMode(Qt.ElideNone)  # ឈ្មោះ tab ពេញ (កុំកាត់ "Live Traffic Flo…")
        # 🌐 Live Traffic Flow
        self.flow = TrafficFlow()
        self.tabs.addTab(self.flow, "🌐 Live Traffic Flow")
        # 👤 Configured Account Profiles
        acc_w = QWidget()
        al = QVBoxLayout(acc_w)
        al.setContentsMargins(0, 6, 0, 0)
        al.setSpacing(6)
        bar = QHBoxLayout()
        self.acc_title = QLabel("Configured Account Profiles")
        self.acc_title.setObjectName("panelSub")
        bar.addWidget(self.acc_title)
        for text, fn, tip in [("➕ Manual Account Login", self._add, "បើក Chrome ឱ្យចូល Google ជាមួយគណនីថ្មី (profile ថ្មីក្នុង Router)"),
                              ("⏸ Pause All", lambda: self._act("pause_all"), "ផ្អាកគ្រប់គណនី"),
                              ("▶ Unpause All", lambda: self._act("unpause_all"), "បើកគ្រប់គណនីវិញ"),
                              ("🧹 Close Chrome (Free RAM)", lambda: self._act("close_all"), "បិទ Chrome ទាំងអស់ — បើកវិញស្វ័យប្រវត្តិពេលមានសំណើ"),
                              ("↺ Reset cooldown", lambda: self._act("reset_all_cooldowns"), "លុប cooldown (rate-limit) គ្រប់គណនី"),
                              ("📊 Sync Usage", lambda: self._act("refresh_usage"), "ទាញ % quota ពី Gemini គ្រប់គណនី (យឺត ~10វិ)")]:
            b = QPushButton(text)
            b.setObjectName("ghost")
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        self.act_note = QLabel("")
        self.act_note.setObjectName("simpleMuted")
        bar.addWidget(self.act_note)
        al.addLayout(bar)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(["ACCOUNT ID", "PLAN", "STATUS", "CURRENT USAGE", "WEEKLY LIMIT", "CONCURRENCY",
                                              "REQUESTS", "LATENCY", "CHECK", "ACTIONS"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 230)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        al.addWidget(self.table, 1)
        self.tabs.addTab(acc_w, "👤 Account Profiles")
        # 📜 Live Request Logs
        log_w = QWidget()
        ll = QVBoxLayout(log_w)
        ll.setContentsMargins(0, 6, 0, 0)
        ll.setSpacing(6)
        lbar = QHBoxLayout()
        self.log_title = QLabel("Live Request Logs · Real-time Telemetry")
        self.log_title.setObjectName("panelSub")
        lbar.addWidget(self.log_title, 1)
        b = QPushButton("🗑 Clear Logs")
        b.setObjectName("ghost")
        b.clicked.connect(lambda: self._act("clear_logs"))
        lbar.addWidget(b)
        ll.addLayout(lbar)
        self.log = QTableWidget(0, 9)
        self.log.setHorizontalHeaderLabels(["TIME", "REQ ID", "ROUTED ACCOUNT", "MODEL", "STATUS", "TOKENS in / out", "LATENCY",
                                            "PROMPT", "ERROR"])
        lh = self.log.horizontalHeader()
        lh.setSectionResizeMode(QHeaderView.ResizeToContents)
        lh.setSectionResizeMode(7, QHeaderView.Stretch)
        self.log.verticalHeader().setVisible(False)
        self.log.setEditTriggers(QTableWidget.NoEditTriggers)
        self.log.setWordWrap(False)
        ll.addWidget(self.log, 1)
        self.tabs.addTab(log_w, "📜 Live Request Logs")
        lay.addWidget(self.tabs, 1)
        # ភ្ជាប់ក្រោយសង់ tab គ្រប់ — addTab ដំបូង emit currentChanged (refresh មុន table/log មាន → qFatal)
        self.tabs.currentChanged.connect(lambda _: self.refresh())

        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh)

    # ---------- បត់ / លា ----------
    def set_collapsed(self, on, emit=True):
        changed = on != self._collapsed
        self._collapsed = on
        self.btn_toggle.setText(("▸" if on else "▾") + "  🔀 Gemini Router Dashboard")
        if self._fold is not None:
            self._fold.stop()
            self._fold = None
        if changed and emit and self.isVisible() and not theme.reduced_motion():
            # ចលនាបត់/លា 240ms — កម្ពស់អតិបរមារំកិល រួចដាក់កម្រិតចុងក្រោយ
            start = self.height()
            self.setMinimumHeight(0)
            self.tabs.setVisible(True)
            an = QVariantAnimation(self)
            an.setDuration(240)
            an.setEasingCurve(QEasingCurve.OutCubic)
            an.setStartValue(float(start))
            an.setEndValue(44.0 if on else float(max(self.EXPANDED_H, start)))
            an.valueChanged.connect(lambda v: self.setMaximumHeight(int(v)))
            an.finished.connect(self._fold_done)
            self._fold = an
            an.start()
        else:
            self._fold_done()
        if on:
            self.timer.stop()
        elif self.isVisible():
            self.timer.start()
            self.refresh()
        if emit:
            self.collapsed_changed.emit(on)

    def _fold_done(self):
        """កម្រិតចុងក្រោយ: បើក → កម្ពស់អប្បបរមា EXPANDED_H ហើយលាតពេញចន្លោះទំនេរ · បត់ → តែក្បាល"""
        on = self._collapsed
        self._fold = None
        self.tabs.setVisible(not on)
        self.setMaximumHeight(44 if on else 16777215)
        self.setMinimumHeight(0 if on else self.EXPANDED_H)

    def collapsed(self):
        return self._collapsed

    def showEvent(self, e):
        super().showEvent(e)
        if not self._collapsed:
            self.timer.start()
            self.refresh()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.timer.stop()

    # ---------- ទិន្នន័យ ----------
    def refresh(self):
        if self._done_note is not None:  # លទ្ធផលសកម្មភាព (ពី thread) → បង្ហាញ + សង់ជួរឡើងវិញ
            note, self._done_note = self._done_note, None
            self.act_note.setText(note if len(note) <= 90 else note[:88] + "…")
            self.act_note.setToolTip(note)
            self._reset_rows()
        mode = gemini_direct.dashboard_mode()  # engine (ក្នុងកម្មវិធី) · http (Router server ខាងក្រៅ) · None
        self.btn_web.setVisible(mode == "http")
        self.btn_check.setEnabled(bool(mode) and not self._busy_action)
        self.live.setProperty("state", "on" if mode else "off")
        self.live.setText("● LIVE" if mode == "engine" else "● LIVE (server)" if mode == "http" else "○ OFF")
        self.live.style().unpolish(self.live)
        self.live.style().polish(self.live)
        if not mode:
            self.summary.setText(gemini_direct.status_text())
            self.flow.ready_txt = ""
            self.flow.update_data([], [])
            self.table.setRowCount(0)
            self._row_ids = []
            self.log.setRowCount(0)
            return
        accounts = gemini_direct.accounts()
        logs = gemini_direct.logs(100)
        ready = sum(1 for a in accounts if a.get("raw_status", a.get("status")) == "ready" and not a.get("is_paused"))
        busy = sum(1 for a in accounts if a.get("is_busy"))
        inflight = sum(1 for e in logs if e.get("status") == "in_flight")
        done = [e for e in logs if e.get("status") == "success" and e.get("latency_seconds")]
        avg = sum(e["latency_seconds"] for e in done) / len(done) if done else 0
        src = "ក្នុងកម្មវិធី" if mode == "engine" else f"server :{gemini_direct.ROUTER_PORT}"
        self.summary.setText(f"{src} · {ready}/{len(accounts)} accounts active · streaming {busy} · in flight {inflight}"
                             + (f" · avg {avg:.1f}s" if avg else ""))
        self.flow.ready_txt = ("All accounts ready" if accounts and ready == len(accounts) else f"{ready}/{len(accounts)} ready")
        self.flow.update_data(accounts, logs)
        self.acc_title.setText(f"Configured Account Profiles  ·  {len(accounts)}")
        self.log_title.setText(f"Live Request Logs · Real-time Telemetry · {len(logs)} recent")
        if self.tabs.currentIndex() == 1:
            self._fill_accounts(accounts)
        elif self.tabs.currentIndex() == 2:
            self._fill_log(logs)

    @staticmethod
    def _pct(v, reset):
        if v is None:
            return "—"
        return f"{v}% used" + (f" · {str(reset)[:14]}" if reset else "")

    def _fill_accounts(self, accounts):
        c = theme.C
        t = self.table
        ids = [a["account_id"] for a in accounts]
        rebuild = ids != self._row_ids
        checks = gemini_direct.checks()
        if rebuild:
            t.setRowCount(len(accounts))
            self._row_ids = ids
        for r, a in enumerate(accounts):
            paused = a.get("is_paused")
            k = _kind(a)
            status = STATUS_KH.get("paused" if paused else a.get("raw_status", ""), a.get("raw_status", ""))
            if a.get("is_cooling_down"):
                status = f"cooldown {a.get('cooldown_remaining_seconds', 0)}s"
            if a.get("last_error") and a.get("raw_status") in ("error", "needs_login"):
                status += f" — {str(a['last_error'])[:60]}"
            cells = [a["account_id"], a.get("plan", ""), "● " + status,
                     self._pct(a.get("current_usage_pct"), a.get("current_usage_reset")),
                     self._pct(a.get("weekly_usage_pct"), a.get("weekly_usage_reset")),
                     "Streaming" if a.get("is_busy") else "Idle", str(a.get("total_requests", 0)),
                     f"{a['avg_latency']:.1f}s" if a.get("avg_latency") else "—"]
            ck = checks.get(a["account_id"], {})
            cells.append({"ok": f"✓ OK · {ck.get('seconds', 0):.1f}s", "fail": "✕ " + str(ck.get("msg", ""))[:46],
                          "skip": "⏭ " + str(ck.get("msg", ""))[:46], "testing": "⏳ កំពុងសាក..."}.get(ck.get("state"), "—"))
            ck_color = {"ok": c["success"], "fail": c["error"], "skip": c["warn"], "testing": c["accent"]}.get(ck.get("state"), c["muted"])
            color = {"stream": c["accent"], "ready": c["success"], "paused": c["warn"], "offline": c["error"]}[k]
            for col, text in enumerate(cells):
                item = t.item(r, col)
                if item is None:
                    item = QTableWidgetItem()
                    t.setItem(r, col, item)
                item.setText(text)
                item.setForeground(QColor(ck_color if col == 8 else
                                          color if col in (2, 5) and (col == 2 or a.get("is_busy")) else c["text"]))
                if col == 8:
                    item.setToolTip(str(ck.get("msg", "")))
                if col == 0:
                    item.setToolTip(a.get("email") or a.get("profile_dir") or "")
                elif col == 2 and a.get("last_error"):
                    item.setToolTip(str(a["last_error"]))
            if rebuild or t.cellWidget(r, 9) is None:
                box = QWidget()
                bl = QHBoxLayout(box)
                bl.setContentsMargins(2, 0, 2, 0)
                bl.setSpacing(4)
                acc_id = a["account_id"]
                for text, action, tip in [("▶ Resume" if paused else "⏸ Pause", "pause", "ផ្អាក / បើកវិញ (មិនប្រើគណនីនេះ)"),
                                          ("⚡ Off", "close_browser", "បិទ Chrome របស់គណនីនេះ (free RAM) — បើកវិញស្វ័យប្រវត្តិ"),
                                          ("🌐 Open Chrome", "relogin", "បើក Chrome មើលឃើញ (ចូល Google ម្តងទៀត) · ចុច ✓ Done ពេលរួច"),
                                          ("✓ Done", "finish_relogin", "ចូលរួច → បិទ Chrome ហើយដាក់គណនីចូលក្រុមវិញ"),
                                          ("↺", "reset_cooldown", "លុប cooldown"),
                                          ("🗑", "delete", "លុបគណនី (profile) ចេញពី Router")]:
                    b = QPushButton(text)
                    b.setFixedHeight(30)
                    b.setToolTip(tip)
                    if action == "delete":
                        b.clicked.connect(lambda _=False, i=acc_id: self._delete(i))
                    else:
                        b.clicked.connect(lambda _=False, act=action, i=acc_id: self._act(act, i))
                    bl.addWidget(b)
                t.setCellWidget(r, 9, box)
            else:
                t.cellWidget(r, 9).layout().itemAt(0).widget().setText("▶ Resume" if paused else "⏸ Pause")
        for r in range(t.rowCount()):
            t.setRowHeight(r, 44)

    def _fill_log(self, logs):
        c = theme.C
        g = self.log
        g.setRowCount(len(logs))
        color = {"success": c["success"], "in_flight": c["accent"], "error": c["error"], "cancelled": c["muted"]}
        for r, e in enumerate(logs):
            st = e.get("status", "")
            lat = e.get("latency_seconds")
            code = e.get("status_code")
            status = {"success": f"{code or 200} OK", "in_flight": "● in flight", "error": f"✕ {code or ''} error",
                      "cancelled": "■ cancelled"}.get(st, st)
            cells = [str(e.get("timestamp", "")), str(e.get("request_id", ""))[:16], str(e.get("account_id", "")),
                     str(e.get("model", "")), status,
                     f"{e.get('prompt_tokens', 0)} / {e.get('completion_tokens', 0)}",
                     f"{lat:.2f}s" if lat else ("…" if st == "in_flight" else "—"),
                     " ".join(str(e.get("prompt", "")).split())[:100], str(e.get("error_message") or "")[:80]]
            for col, text in enumerate(cells):
                item = g.item(r, col)
                if item is None:
                    item = QTableWidgetItem()
                    g.setItem(r, col, item)
                item.setText(text)
                item.setForeground(QColor(color.get(st, c["text"]) if col == 4 else c["text"]))
                if col == 7:
                    item.setToolTip(str(e.get("prompt_full") or e.get("prompt") or "")[:2000])
                elif col == 8 and e.get("response_preview"):
                    item.setToolTip(str(e.get("response_preview"))[:1000])

    # ---------- សកម្មភាព (thread — កុំឱ្យ UI គាំង) ----------
    def _add(self):
        acc_id, ok = QInputDialog.getText(self, "Manual Account Login", "ឈ្មោះគណនី (អក្សរ/លេខ/_/- ឧ. ឈ្មោះ Gmail មុន @):")
        acc_id = (acc_id or "").strip()
        if not ok or not acc_id:
            return
        if not all(ch.isalnum() or ch in "_-" for ch in acc_id):
            return QMessageBox.warning(self, "ឈ្មោះមិនត្រឹមត្រូវ", "ប្រើតែអក្សរឡាតាំង លេខ _ និង -")
        self._act("add_account", acc_id)

    def _check_all(self):
        """🩺 សាកគ្រប់គណនី → លទ្ធផលក្នុងជួរ CHECK (tab Account Profiles) + សង្ខេបលើក្បាលផ្ទាំង"""
        if self._collapsed:
            self.set_collapsed(False)
        self.tabs.setCurrentIndex(1)
        self.btn_check.setEnabled(False)
        self._act("check_all")

    def _delete(self, acc_id):
        if QMessageBox.question(self, "លុបគណនី", f"លុបគណនី '{acc_id}' និង Chrome profile របស់វាចេញពី Router?\n"
                                               "(ត្រូវចូល Google ម្តងទៀត បើចង់ប្រើវិញ)") == QMessageBox.Yes:
            self._act("delete", acc_id)

    def _act(self, action, acc_id=""):
        if self._busy_action:
            return
        self._busy_action = True
        self.act_note.setText(f"⏳ {action} {acc_id}…")

        def run():
            try:
                msg = gemini_direct.action(action, acc_id)
            except Exception as e:  # noqa: BLE001
                msg = f"✕ {str(e)[:120]}"
            self._done_note = msg or ""  # refresh() (thread GUI · រៀងរាល់ 1.5វិ) បង្ហាញ — កុំប៉ះ widget ពី thread នេះ
            self._busy_action = False
        threading.Thread(target=run, daemon=True).start()

    def _reset_rows(self):
        self._row_ids = []  # បង្ខំសង់ជួរឡើងវិញ (ប៊ូតុង Pause/Resume ប្តូរ)

    def restyle(self):
        c = theme.C
        self.setStyleSheet(theme.hackify(f"""
QFrame#routerPanel {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
QPushButton#panelHead {{ background: transparent; border: none; font-weight: bold; font-size: 11pt; text-align: left;
    padding: 2px 4px; color: {c['text']}; }}
QPushButton#panelHead:hover {{ color: {c['accent']}; border: none; background: transparent; }}
QLabel#panelSub {{ font-weight: bold; }}
QLabel#liveChip {{ color: {c['faint']}; font-weight: bold; padding: 2px 8px; border: 1px solid {c['border']}; border-radius: 8px; }}
QLabel#liveChip[state="on"] {{ color: {c['success']}; border-color: {c['success']}; }}
QTabWidget#routerTabs::pane {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QLabel#simpleMuted {{ color: {c['muted']}; }}
QTabWidget#routerTabs QTabBar::tab {{ min-width: 190px; }}
"""))
