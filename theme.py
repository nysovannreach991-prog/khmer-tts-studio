"""រចនាប័ទ្ម HUD / Sci-Fi FUI — ខ្សែស្តើង ពណ៌ neon លើផ្ទៃងងឹត (theme តែមួយ)។

ពណ៌: cyan #00FFFF · blue #0080FF · alert #FF0000 · bg #0A0F1C · ok #00FF00
C = ពណ៌បច្ចុប្បន្ន (dict) — gui.py / simple_ui.py / shell.py … ប្រើវាសម្រាប់ rich text, តារាង និង QSS ក្នុងទំព័រ។
អក្សរ: ខ្មែរ = Kantumruy Pro (ផ្ទុកពីថត fonts/) · ស្លាកបច្ចេកទេស (ក្បាលតារាង, pill, readout) = mono (JetBrains Mono → Consolas)។
WCAG AA: អក្សរធម្មតាប្រើ cyan ស្រាល (មិនមែន neon សុទ្ធ) · neon សុទ្ធសម្រាប់ស៊ុម/ចំណងជើង · ប៊ូតុងចម្បង = ផ្ទៃ cyan អក្សរងងឹត។
ចលនា (ចំណុចភ្លឹបភ្លែត, radar) បិទពេល Windows "Animation effects" បិទ (prefers-reduced-motion)។
"""
import os
import re
import tempfile

from PyQt5.QtCore import QEvent, QObject, Qt, QTimer
from PyQt5.QtGui import QColor, QImage, QPainter, QPalette, QPen
from PyQt5.QtWidgets import QGraphicsDropShadowEffect, QLabel, QPushButton, QToolButton, QWidget

# ពណ៌តាម logo Dubber Ultra: មាស · ក្រហម · navy ងងឹត (ឈ្មោះអថេរ CYAN/BLUE រក្សាទុក — accent / accent2)
CYAN, BLUE, ALERT, BG, OK = "#F5C451", "#D7263D", "#E11D2E", "#0B0D1A", "#4ADE80"

HUD = {
    "bg": BG, "surface": "#12152A", "card": "#191D38", "input": "#0A0C18", "hover": "#2A2440",
    "border": "#4A3B1E", "border_hi": "#B8892E", "text": "#F7EBCB", "muted": "#C9B27A", "faint": "#9A8A62",
    "accent": CYAN, "accent2": BLUE, "accent3": "#FFE08A", "on_accent": "#1A1203",
    "grad_a": CYAN, "grad_b": "#FF9A1F",
    "success": OK, "error": "#FF5A5F", "warn": "#FFB020", "alert": ALERT,
    "female": "#FF7AB8", "male": "#5AA9FF", "selection": "#4A2F12",
    "lcd": "#070812", "lcd_text": "#FFD98A", "bevel": "#4A3B1E",
}
THEMES = {"hud": HUD}
C = dict(HUD)
mode = "hud"
_base_families = None
MONO = ["JetBrains Mono", "Consolas"]
_MONO_QSS = '"JetBrains Mono", "Consolas", "Kantumruy Pro"'

GRAD = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {a}, stop:1 {b})"


def _assets(c):
    """រូបតូចៗសម្រាប់ QSS: SVG checkbox / combo / spinbox + ក្រឡា grid 50px (PNG) សម្រាប់ផ្ទៃខាងក្រោយ"""
    folder = os.path.join(tempfile.gettempdir(), f"khmer_tts_theme_{mode}")
    os.makedirs(folder, exist_ok=True)
    stroke = 'fill="none" stroke-width="2.2" stroke-linecap="square" stroke-linejoin="miter"'
    svgs = {
        "check": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" '
                 f'stroke="{c["on_accent"]}" {stroke}/></svg>',
        "down": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M4 6l4 4 4-4" '
                f'stroke="{c["accent"]}" {stroke}/></svg>',
        "up": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M4 10l4-4 4 4" '
              f'stroke="{c["accent"]}" {stroke}/></svg>',
        "dot": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect x="5" y="5" width="6" height="6" '
               f'fill="{c["on_accent"]}"/></svg>',
    }
    paths = {}
    for name, svg in svgs.items():
        path = os.path.join(folder, name + ".svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
        paths[name] = path.replace("\\", "/")
    grid = os.path.join(folder, "grid.png")
    try:
        img = QImage(50, 50, QImage.Format_RGB32)
        img.fill(QColor(c["bg"]))
        p = QPainter(img)
        p.setPen(QPen(QColor(245, 196, 81, 12), 1))  # cyan 4–5%
        p.drawLine(0, 0, 49, 0)
        p.drawLine(0, 0, 0, 49)
        p.end()
        img.save(grid)
    except Exception:  # noqa: BLE001
        pass
    paths["grid"] = grid.replace("\\", "/")
    return paths


def stylesheet():
    c = C
    i = _assets(c)
    line = "rgba(245, 196, 81, 0.30)"
    line_hi = "rgba(245, 196, 81, 0.55)"
    tint = "rgba(245, 196, 81, 0.05)"
    tint2 = "rgba(245, 196, 81, 0.10)"
    return f"""
* {{ outline: none; }}
QMainWindow, QDialog, QMessageBox {{ background-color: {c['bg']}; background-image: url({i['grid']}); }}
QWidget {{ color: {c['text']}; }}
QToolTip {{ background: {c['lcd']}; color: {c['lcd_text']}; border: 1px solid {c['accent']}; padding: 6px 8px; }}

/* ---------- header ---------- */
QFrame#header {{ background: {c['surface']}; border: 1px solid {line}; }}
QLabel#logo {{ background: transparent; border: none; font-size: 20pt; color: {c['accent']}; }}
QLabel#appTitle {{ font-size: 16pt; font-weight: bold; color: {c['accent']}; font-family: {_MONO_QSS}; }}
QLabel#appSub {{ color: {c['muted']}; }}
QLabel#pill {{ background: {c['lcd']}; color: {c['accent']}; border: 1px solid {line_hi};
               padding: 2px 10px; font-weight: bold; font-size: 9pt; font-family: {_MONO_QSS}; }}
QLabel#hudDot {{ color: {c['accent']}; font-size: 11pt; }}
QLabel#hudDot[dim="true"] {{ color: {c['border_hi']}; }}
QPushButton#ghost {{ background: transparent; border: 1px solid {line_hi}; padding: 6px 14px; color: {c['accent']}; }}
QPushButton#ghost::menu-indicator {{ image: none; width: 0; }}
QPushButton#ghost:hover {{ background: {c['accent']}; color: {c['on_accent']}; border-color: {c['accent']}; }}

/* ---------- tabs ---------- */
QTabWidget::pane {{ background: {c['surface']}; border: 1px solid {line}; top: -1px; }}
QTabWidget > QStackedWidget > QWidget {{ background: transparent; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; border: 1px solid transparent;
                padding: 8px 9px; margin: 0 1px 8px 0; font-weight: bold; }}
QTabBar::tab:hover {{ background: {tint2}; color: {c['accent']}; border: 1px solid {line}; }}
QTabBar::tab:selected {{ background: {tint2}; color: {c['accent']}; border: 1px solid {c['accent']}; }}
QTabBar QToolButton {{ background: {c['card']}; border: 1px solid {line}; }}

/* ---------- panels ---------- */
QGroupBox {{ background: {c['surface']}; border: 1px solid {line}; margin-top: 24px; font-weight: bold; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 10px; top: 0px;
                    padding: 0 4px; color: {c['muted']}; }}
QGroupBox QLabel, QGroupBox QCheckBox, QGroupBox QRadioButton {{ font-weight: normal; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QSplitter::handle {{ background: transparent; width: 10px; }}

/* ---------- buttons ---------- */
QPushButton {{ background: {tint2}; border: 1px solid {line_hi}; padding: 7px 14px; color: {c['accent']}; }}
QPushButton:hover {{ background: {c['accent']}; color: {c['on_accent']}; border-color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['grad_b']}; color: {c['on_accent']}; }}
QPushButton:checked {{ background: {c['selection']}; border: 1px solid {c['accent']}; color: {c['accent']}; }}
QPushButton:focus {{ border: 1px solid #FFFFFF; }}
QPushButton:disabled {{ color: {c['faint']}; background: transparent; border: 1px dashed {c['border']}; }}
QPushButton#primary, QPushButton#bigGo {{ background: {c['accent']}; color: {c['on_accent']}; border: 1px solid {c['accent']};
                       padding: 0 24px; font-weight: bold; font-size: 11pt; }}
QPushButton#primary:hover, QPushButton#bigGo:hover {{ background: #FFFFFF; border-color: #FFFFFF; color: {c['on_accent']}; }}
QPushButton#primary:focus, QPushButton#bigGo:focus {{ border: 2px solid #FFFFFF; }}
QPushButton#primary:disabled, QPushButton#bigGo:disabled {{ background: {c['lcd']}; color: {c['faint']};
                       border: 1px dashed {c['border_hi']}; }}

/* ---------- inputs ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {c['input']}; border: 1px solid {line}; padding: 6px 10px; color: {c['text']};
    selection-background-color: {c['accent']}; selection-color: {c['on_accent']}; }}
QPlainTextEdit, QTextEdit {{ padding: 8px; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover,
QPlainTextEdit:hover {{ border-color: {line_hi}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus {{ border: 1px solid {c['accent']}; background: {c['card']}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {c['faint']}; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({i['down']}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {c['lcd']}; border: 1px solid {c['accent']}; padding: 4px; color: {c['text']};
                               selection-background-color: {c['selection']}; selection-color: {c['accent']}; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    border: none; background: transparent; width: 20px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({i['up']}); width: 12px; height: 12px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({i['down']}); width: 12px; height: 12px; }}

/* ---------- checkbox / radio ---------- */
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {line_hi};
                                                 background: {c['input']}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox:focus, QRadioButton:focus {{ color: {c['accent']}; }}
QCheckBox::indicator:focus, QRadioButton::indicator:focus {{ border: 1px solid #FFFFFF; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({i['check']}); }}
QRadioButton::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; image: url({i['dot']}); }}
QCheckBox::indicator:disabled {{ background: transparent; border: 1px dashed {c['border']}; }}

/* ---------- slider ---------- */
QSlider::groove:horizontal {{ height: 2px; background: {c['border']}; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; }}
QSlider::handle:horizontal {{ background: {c['bg']}; border: 2px solid {c['accent']}; width: 10px; height: 14px;
                              margin: -8px 0; }}

/* ---------- tables / lists ---------- */
QTableWidget, QTreeWidget, QTreeView, QListWidget {{ background: {c['input']}; alternate-background-color: {c['surface']};
    border: 1px solid {line}; gridline-color: {c['border']}; color: {c['text']};
    selection-background-color: {c['selection']}; selection-color: {c['accent']}; }}
QListWidget::item {{ padding: 6px 8px; margin: 1px 4px; }}
QListWidget::item:hover, QTreeWidget::item:hover, QTreeView::item:hover {{ background: {tint2}; }}
QListWidget::item:selected, QTreeWidget::item:selected, QTreeView::item:selected {{ background: {c['selection']}; color: {c['accent']}; }}
QTreeWidget::item, QTreeView::item {{ padding: 4px 0; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {c['lcd']}; color: {c['muted']}; border: none;
    border-bottom: 1px solid {line_hi}; padding: 7px 8px; font-weight: bold; font-family: {_MONO_QSS}; }}
QTableCornerButton::section {{ background: {c['lcd']}; border: none; }}

/* ---------- progress ---------- */
QProgressBar {{ background: {c['lcd']}; border: 1px solid {line_hi}; height: 20px; padding: 2px;
                text-align: center; font-weight: bold; color: {c['text']}; font-family: {_MONO_QSS}; }}
QProgressBar::chunk {{ background: {c['accent']}; width: 4px; margin-right: 2px; }}
QProgressBar#cardBar, QProgressBar#bigBar {{ background: {c['lcd']}; border: 1px solid {line}; padding: 2px; }}
QProgressBar#cardBar::chunk, QProgressBar#bigBar::chunk {{ background: {c['accent']}; width: 3px; margin-right: 2px; }}

/* ---------- labels ---------- */
QLabel[role="muted"] {{ color: {c['muted']}; }}
QLabel[role="warn"] {{ color: {c['warn']}; }}
QLabel[role="error"] {{ color: {c['error']}; }}
QLabel#stageLabel {{ background: {c['lcd']}; border: 1px solid {line}; padding: 8px 12px; }}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
QScrollBar::handle {{ background: {c['border_hi']}; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {c['accent']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- menu ---------- */
QMenu {{ background: {c['lcd']}; border: 1px solid {c['accent']}; padding: 6px; color: {c['text']}; }}
QMenu::item {{ padding: 7px 18px; }}
QMenu::item:selected {{ background: {c['accent']}; color: {c['on_accent']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
"""


# QSS បន្ថែមដែលភ្ជាប់ចុងគ្រប់ stylesheet (កម្មវិធី + ទំព័រ: simple_ui / shell / router_panel / logo_page ហៅ hackify)
# → ផ្ទាំង (panel) ខ្សែស្តើង ជ្រុងមុត · ស្លាកស្ថានភាពជា readout · គ្មាន bevel/gradient ពណ៌ស របស់ theme ចាស់
_HUD_QSS = """
QFrame#header, QFrame#simpleHero, QFrame#simpleSide, QFrame#simpleFoot, QFrame#sideNav, QFrame#routerPanel,
QFrame#routerBar {{ background: {c[surface]}; border: 1px solid rgba(245, 196, 81, 0.30); }}
QFrame#simpleSide {{ background: {c[bg]}; border: 1px solid {c[accent]}; }}
QLabel#simpleTitle {{ color: {c[accent]}; }}
QLabel#leds {{ color: {c[accent]}; font-size: 8pt; }}
QLabel#serial {{ color: {c[muted]}; font-size: 8pt; font-family: """ + _MONO_QSS + """; }}
QLabel#led {{ color: {c[faint]}; font-size: 11pt; }}
QLabel#led[state="running"] {{ color: {c[accent]}; }}
QLabel#led[state="done"] {{ color: {c[success]}; }}
QLabel#led[state="failed"] {{ color: {c[error]}; }}
QLabel#chip {{ background: transparent; color: {c[muted]}; border: 1px solid rgba(245, 196, 81, 0.30); }}
QLabel#chip[state="active"] {{ background: rgba(245, 196, 81, 0.12); color: {c[accent]}; border: 1px solid {c[accent]}; }}
QLabel#chip[state="done"] {{ background: transparent; color: {c[success]}; border: 1px solid {c[success]}; }}
QLabel#simpleArrow {{ color: {c[accent2]}; }}
QFrame#dropZone {{ background: rgba(245, 196, 81, 0.03); border: 1px dashed rgba(245, 196, 81, 0.55); }}
QFrame#dropZone:hover, QFrame#dropZone[hover="true"] {{ background: rgba(245, 196, 81, 0.10); border: 1px dashed {c[accent]}; }}
QLabel#dropTitle {{ color: {c[accent]}; }}
QFrame#storyCard {{ background: {c[surface]}; border: 1px solid rgba(245, 196, 81, 0.30);
    border-left: 3px solid {c[border_hi]}; }}
QFrame#storyCard[state="running"] {{ border: 1px solid rgba(245, 196, 81, 0.30); border-left: 3px solid {c[accent]}; }}
QFrame#storyCard[state="done"] {{ border: 1px solid rgba(245, 196, 81, 0.30); border-left: 3px solid {c[success]}; }}
QFrame#storyCard[state="failed"] {{ border: 1px solid rgba(245, 196, 81, 0.30); border-left: 3px solid {c[alert]}; }}
QLabel#statusChip {{ background: {c[lcd]}; color: {c[muted]}; border: 1px solid {c[border]}; font-family: """ + _MONO_QSS + """; }}
QLabel#statusChip[state="running"] {{ color: {c[accent]}; border-color: {c[accent]}; }}
QLabel#statusChip[state="done"] {{ color: {c[success]}; border-color: {c[success]}; }}
QLabel#statusChip[state="failed"] {{ color: {c[error]}; border-color: {c[alert]}; }}
QLabel#simpleHeadline, QLabel#lcd {{ background: {c[lcd]}; color: {c[lcd_text]}; border: 1px solid rgba(245, 196, 81, 0.55);
    padding: 7px 12px; }}
QLabel#sideHead {{ color: {c[accent]}; border-bottom: 1px solid rgba(245, 196, 81, 0.30); padding-bottom: 6px; }}
QPushButton#cardX {{ background: transparent; border: none; color: {c[muted]}; padding: 0; }}
QPushButton#cardX:hover {{ background: transparent; color: {c[error]}; border: none; }}
QPushButton#cardOpen {{ background: transparent; border: 1px solid rgba(245, 196, 81, 0.55); padding: 3px 10px; }}
QPushButton#cardOpen:hover {{ background: {c[accent]}; color: {c[on_accent]}; }}
QPushButton#bigGo {{ background: {c[accent]}; color: {c[on_accent]}; border: 1px solid {c[accent]}; }}
QPushButton#bigGo:hover {{ background: #FFFFFF; border-color: #FFFFFF; color: {c[on_accent]}; }}
QPushButton#bigGo:disabled {{ background: {c[lcd]}; color: {c[faint]}; border: 1px dashed {c[border_hi]}; }}
QProgressBar#cardBar, QProgressBar#bigBar {{ background: {c[lcd]}; border: 1px solid rgba(245, 196, 81, 0.30); padding: 2px; }}
QProgressBar#cardBar::chunk, QProgressBar#bigBar::chunk {{ background: {c[accent]}; width: 3px; margin-right: 2px; }}
QPushButton#navItem:checked {{ background: rgba(245, 196, 81, 0.12); color: {c[accent]}; border: none;
    border-left: 3px solid {c[accent]}; }}
QPushButton#navItem:hover {{ background: rgba(245, 196, 81, 0.10); color: {c[accent]}; border: none; }}
QPushButton#navItem:focus {{ border: 1px solid #FFFFFF; }}
QPushButton#navGroup {{ font-family: """ + _MONO_QSS + """; }}
QPushButton#navGroup:focus {{ border: 1px solid #FFFFFF; }}
QPushButton#panelHead {{ color: {c[accent]}; }}
QPushButton#panelHead:hover {{ color: #FFFFFF; background: transparent; border: none; }}
QTabWidget#routerTabs::pane {{ background: {c[lcd]}; border: 1px solid rgba(245, 196, 81, 0.30); }}
"""


def hackify(qss):
    """QSS របស់ទំព័រ → រចនាប័ទ្ម HUD: ជ្រុងមុត (radius ≤ 2px) + ច្បាប់ HUD ភ្ជាប់ចុង (ឈ្នះច្បាប់ក្នុងទំព័រ)"""
    qss = re.sub(r"border-radius:\s*(\d+)px", lambda m: f"border-radius: {min(int(m.group(1)), 2)}px", qss)
    return qss + _HUD_QSS.format(c=C)


def palette():
    c = C
    p = QPalette()
    for role, key in [(QPalette.Window, "bg"), (QPalette.Base, "input"), (QPalette.AlternateBase, "surface"),
                      (QPalette.Button, "card"), (QPalette.WindowText, "text"), (QPalette.Text, "text"),
                      (QPalette.ButtonText, "text"), (QPalette.ToolTipBase, "lcd"), (QPalette.ToolTipText, "lcd_text"),
                      (QPalette.Highlight, "accent"), (QPalette.PlaceholderText, "faint"), (QPalette.Link, "accent"),
                      (QPalette.BrightText, "accent")]:
        p.setColor(role, QColor(c[key]))
    p.setColor(QPalette.HighlightedText, QColor(c["on_accent"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(c["faint"]))
    return p


def mono(font):
    """font monospace (JetBrains Mono → Consolas) — អក្សរខ្មែរនៅតែប្រើ font កម្មវិធី (Kantumruy Pro)"""
    font.setFamilies(MONO + [f for f in (_base_families or font.families()) if f not in MONO])
    return font


def apply(qt_app, new_mode="hud"):
    """ប្រើ theme HUD (តែមួយ — តម្លៃ theme ចាស់ក្នុង settings ត្រូវបានមិនអើពើ) · ខ្មែរ = Kantumruy Pro ជានិច្ច"""
    global mode, _base_families
    mode = "hud"
    C.clear()
    C.update(THEMES[mode])
    if _base_families is None:
        _base_families = qt_app.font().families() or [qt_app.font().family()]
    fams = [f for f in _base_families if f not in MONO and f != "Kantumruy Pro"]
    font = qt_app.font()
    font.setFamilies(["Kantumruy Pro"] + fams)  # Kantumruy Pro មុនគេ → អក្សរខ្មែរ (និងឡាតាំងធម្មតា)
    qt_app.setFont(font)
    _base_families = font.families()
    qt_app.setPalette(palette())
    qt_app.setStyleSheet(hackify(stylesheet()))


# ---------------------------------------------------------------- ផលប៉ះពាល់ HUD (glow · ជ្រុង bracket · ចំណុចភ្លឹបភ្លែត)
def reduced_motion():
    """Windows: Settings → Accessibility → Visual effects → Animation effects = Off → កុំដំណើរការចលនាតុបតែង"""
    try:
        import ctypes
        on = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)  # SPI_GETCLIENTAREAANIMATION
        return not bool(on.value)
    except Exception:  # noqa: BLE001
        return False


def glow(widget, color=None, blur=18):
    """ពន្លឺ neon ជុំវិញ widget (កុំប្រើលើ widget ក្នុងទំព័រដែលមាន graphics effect ផ្សេង — effect មួយក្នុងមួយ widget)"""
    fx = QGraphicsDropShadowEffect(widget)
    fx.setOffset(0, 0)
    fx.setBlurRadius(blur)
    col = QColor(color or C["accent"])
    col.setAlpha(150)
    fx.setColor(col)
    widget.setGraphicsEffect(fx)
    return fx


class _Brackets(QWidget):
    """ជ្រុង bracket មួយគូ (លើ-ឆ្វេង · ក្រោម-ស្តាំ) គូរពីលើ panel — មិនទទួល mouse · មិនប៉ះប្លង់"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)
        parent.installEventFilter(self)
        self.resize(parent.size())
        self.raise_()
        self.show()

    def eventFilter(self, obj, e):
        if obj is self.parent() and e.type() in (QEvent.Resize, QEvent.Show, QEvent.ChildAdded):
            self.resize(obj.size())
            self.raise_()
        return False

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(QPen(QColor(C["accent"]), 1))
        w, h, m, a = self.width() - 1, self.height() - 1, 5, 12
        p.drawLine(m, m, m + a, m)
        p.drawLine(m, m, m, m + a)
        p.drawLine(w - m, h - m, w - m - a, h - m)
        p.drawLine(w - m, h - m, w - m, h - m - a)


def add_brackets(widget):
    """ដាក់ជ្រុង bracket មួយគូលើ panel (ម្តងគត់ក្នុងមួយ panel)"""
    if widget is None or widget.findChild(_Brackets, options=Qt.FindDirectChildrenOnly):
        return
    _Brackets(widget)


class StatusDot(QLabel):
    """ចំណុចស្ថានភាពតុបតែង ភ្លឹបភ្លែតយឺតៗ (2វិ) — មិនមែនអក្សរដែលត្រូវអាន · ឈប់ពេល reduced motion"""

    def __init__(self, parent=None):
        super().__init__("●", parent)
        self.setObjectName("hudDot")
        self.setAccessibleName("")
        self._dim = False
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        if not reduced_motion():
            self._timer.start()

    def _tick(self):
        self._dim = not self._dim
        self.setProperty("dim", self._dim)
        self.style().unpolish(self)
        self.style().polish(self)


def name_icon_buttons(root):
    """ប៊ូតុងដែលមានតែ icon/សញ្ញា (គ្មានអក្សរ) → accessible name ពី tooltip (សម្រាប់ screen reader)"""
    for b in root.findChildren((QPushButton, QToolButton)):
        if b.accessibleName():
            continue
        text = b.text().replace("&", "")
        if any(ch.isalnum() for ch in text):
            continue
        tip = (b.toolTip() or "").split("\n")[0].strip()
        if tip:
            b.setAccessibleName(tip)
