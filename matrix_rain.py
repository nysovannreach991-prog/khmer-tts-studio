"""Matrix rain — អក្សរធ្លាក់ពណ៌បៃតង (theme "matrix")

MatrixRain(parent) — គូរពីក្រោយ widget ផ្សេងក្នុង parent (ឧ. header) ហើយប្តូរទំហំតាម parent
cascade(window) — ភ្លៀងអក្សរពេញបង្អួចមួយភ្លែត ពេលប្តូរទៅ theme Matrix
"""
import random

from PyQt5.QtCore import QEvent, QObject, QPropertyAnimation, QRect, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QPainter
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QWidget

# katakana ពាក់កណ្តាល + លេខ + អក្សរ/លេខខ្មែរ
GLYPHS = ("ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ"
          "0123456789Z:.=*+<>|"
          "០១២៣៤៥៦៧៨៩កខគឃងចឆជឈញដឋឌឍណតថទធនបផពភមយរលវសហឡអ")
HEAD = QColor("#e8ffe8")
TRAIL = QColor("#00ff41")


class _Column:
    __slots__ = ("y", "speed", "length", "chars")

    def __init__(self, rows, fresh):
        self.length = random.randint(6, max(8, rows))
        self.y = random.uniform(-rows, rows) if fresh else random.uniform(-self.length * 2, 0)
        self.speed = random.uniform(0.25, 0.9)  # ជួរ ក្នុងមួយ frame
        self.chars = [random.choice(GLYPHS) for _ in range(self.length)]


class MatrixRain(QWidget):
    def __init__(self, parent, cell=16, alpha=1.0, fps=24, density=0.75, keep_clear=()):
        """keep_clear = widget (ក្នុង parent) ដែលភ្លៀងស្រអាប់ពេលធ្លាក់កាត់ — កុំឱ្យអក្សរ/ប៊ូតុងពិបាកអាន"""
        super().__init__(parent)
        self.keep_clear = list(keep_clear)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.cell, self.alpha, self.density = cell, alpha, density
        self.font_ = QFont("Consolas")
        self.font_.setPixelSize(cell - 2)
        self.cols = {}
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._step)
        self.timer.setInterval(1000 // fps)
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())
        self.lower()

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() == QEvent.Resize:
            self.setGeometry(self.parent().rect())
        return False

    def start(self):
        self.show()
        self.lower()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.hide()

    def _step(self):
        rows = max(1, self.height() // self.cell)
        ncols = max(1, self.width() // self.cell)
        for x in list(self.cols):
            if x >= ncols:
                del self.cols[x]
        for x in range(ncols):
            col = self.cols.get(x)
            if col is None:
                if random.random() < self.density:
                    self.cols[x] = _Column(rows, fresh=True)
                continue
            col.y += col.speed
            if random.random() < 0.08:  # អក្សរប្តូរខ្លួនឯងម្តងម្កាល
                col.chars[random.randrange(col.length)] = random.choice(GLYPHS)
            if col.y - col.length > rows:
                if random.random() < self.density:
                    self.cols[x] = _Column(rows, fresh=False)
                else:
                    del self.cols[x]
        self.update()

    def paintEvent(self, _):
        if not self.cols:
            return
        p = QPainter(self)
        p.setFont(self.font_)
        c = self.cell
        clear = [w.geometry().adjusted(-c // 2, -c // 2, c // 2, c // 2)
                 for w in self.keep_clear if w.isVisible()]
        cell_rect = lambda cx, cy: QRect(cx * c, cy * c, c, c)  # noqa: E731
        for x, col in self.cols.items():
            head = int(col.y)
            for k in range(col.length):
                row = head - k
                if row < 0 or row * c > self.height():
                    continue
                if k == 0:
                    color = QColor(HEAD)
                    color.setAlphaF(min(1.0, self.alpha * 1.2))
                else:
                    color = QColor(TRAIL)
                    color.setAlphaF(self.alpha * (1 - k / col.length) ** 1.6)
                if clear and any(r.intersects(cell_rect(x, row)) for r in clear):
                    color.setAlphaF(color.alphaF() * 0.12)
                p.setPen(color)
                p.drawText(x * c, row * c, c, c, Qt.AlignCenter, col.chars[k % col.length])
        p.end()


class _Cascade(QObject):
    """ភ្លៀងអក្សរពេញបង្អួច ~1.6s ហើយរសាត់បាត់"""

    def __init__(self, window, ms=1600):
        super().__init__(window)
        self.rain = MatrixRain(window, cell=18, alpha=0.95, fps=30, density=1.0)
        self.rain.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.rain.raise_()
        self.rain.show()
        self.rain.timer.start()
        for _ in range(6):  # ចាប់ផ្តើមពេញអេក្រង់ភ្លាម
            self.rain._step()
        fx = QGraphicsOpacityEffect(self.rain)
        self.rain.setGraphicsEffect(fx)
        self.anim = QPropertyAnimation(fx, b"opacity", self)
        self.anim.setDuration(ms)
        self.anim.setKeyValueAt(0, 1.0)
        self.anim.setKeyValueAt(0.55, 0.85)
        self.anim.setKeyValueAt(1, 0.0)
        self.anim.finished.connect(self._done)
        self.anim.start()

    def _done(self):
        self.rain.timer.stop()
        self.rain.deleteLater()
        self.deleteLater()


def cascade(window, ms=1600):
    return _Cascade(window, ms)
