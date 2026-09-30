"""Watermark អក្សរ — គូរអក្សរ (font ណាមួយលើកុំព្យូទ័រ) ជា PNG ថ្លាដោយ Qt ហើយ overlay.py រំកិលវាលើវីដេអូ។

Qt គូរអក្សរខ្មែរបានត្រឹមត្រូវ (ជើង, ស្រៈ) ហើយប្រើ font ដែលមានក្នុង Windows ដោយផ្ទាល់ — មិនចាំបាច់រកឯកសារ font។
"""
import hashlib
import os
import tempfile

from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

RENDER_PX = 160  # ទំហំអក្សរពេលគូរ (ធំ → ច្បាស់ពេលពង្រីក)


def render(text, family, bold=False, color="#ffffff", outline=True):
    """គូរអក្សរជា PNG ថ្លា (cache តាមការកំណត់) → ត្រឡប់ path"""
    key = hashlib.sha1(f"{text}|{family}|{bold}|{color}|{outline}|{RENDER_PX}".encode()).hexdigest()[:16]
    path = os.path.join(tempfile.gettempdir(), f"aiteam1_wm_{key}.png")
    if os.path.isfile(path):
        return path
    font = QFont(family)
    font.setPixelSize(RENDER_PX)
    font.setBold(bold)
    font.setHintingPreference(QFont.PreferNoHinting)
    metrics = QFontMetricsF(font)
    stroke = RENDER_PX * 0.08 if outline else 0
    pad = stroke + RENDER_PX * 0.06
    rect = metrics.boundingRect(text)
    width = int(max(rect.width(), metrics.horizontalAdvance(text)) + 2 * pad) + 2
    height = int(metrics.height() + 2 * pad) + 2
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    path_ = QPainterPath()
    path_.addText(QPointF(pad - min(rect.left(), 0), pad + metrics.ascent()), font, text)
    if outline:  # គែមងងឹត — អានបានលើផ្ទៃភ្លឺ និងងងឹត
        painter.strokePath(path_, QPen(QColor(0, 0, 0, 200), stroke * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.fillPath(path_, QColor(color))
    painter.end()
    image.save(path, "PNG")
    return path
