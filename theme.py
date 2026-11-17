"""រចនាប័ទ្ម Vector — ម៉ាស៊ីនកុំព្យូទ័របុរាណ (theme តែមួយ)។

C = ពណ៌បច្ចុប្បន្ន (dict ដែលប្តូរតាម mode) — gui.py ប្រើវាសម្រាប់ rich text និងពណ៌ក្នុងតារាង។
"""
import os
import re
import tempfile

from PyQt5.QtGui import QColor, QPalette

# Vector — ម៉ាស៊ីនកុំព្យូទ័របុរាណ: ផ្ទៃប្រផេះស្រាល, module ពណ៌ស (bevel), ភ្លើង LED ទឹកក្រូច, អេក្រង់ LCD ខ្មៅ
VECTOR = {
    "bg": "#d6d7d8", "surface": "#e4e5e6", "card": "#f3f3f2", "input": "#fafaf9", "hover": "#ebeceb",
    "border": "#c3c5c7", "border_hi": "#a7aaad", "text": "#2a2c2f", "muted": "#686c71", "faint": "#9a9ea3",
    "accent": "#ff5a1f", "accent2": "#ef4f16", "accent3": "#ff7c48", "on_accent": "#ffffff",
    "grad_a": "#ff7c48", "grad_b": "#ef4f16",
    "success": "#2e9a58", "error": "#d6392a", "warn": "#dd7418",
    "female": "#d2437a", "male": "#3a6cd4", "selection": "#ffdccd",
    "lcd": "#2d2f32", "lcd_text": "#d4d6d0", "bevel": "#8d9195",
}
THEMES = {"vector": VECTOR}
C = dict(VECTOR)
mode = "vector"
_base_families = None

GRAD = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {a}, stop:1 {b})"


def _icons(c):
    """SVG តូចៗសម្រាប់ checkbox / combo / spinbox (QSS ត្រូវការឯកសាររូបភាព)"""
    folder = os.path.join(tempfile.gettempdir(), f"khmer_tts_theme_{mode}")
    os.makedirs(folder, exist_ok=True)
    stroke = 'fill="none" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"'
    svgs = {
        "check": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" '
                 f'stroke="{c["on_accent"]}" {stroke}/></svg>',
        "down": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M4 6l4 4 4-4" '
                f'stroke="{c["muted"]}" {stroke}/></svg>',
        "up": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M4 10l4-4 4 4" '
              f'stroke="{c["muted"]}" {stroke}/></svg>',
        "dot": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><circle cx="8" cy="8" r="3.5" '
               f'fill="{c["on_accent"]}"/></svg>',
    }
    paths = {}
    for name, svg in svgs.items():
        path = os.path.join(folder, name + ".svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
        paths[name] = path.replace("\\", "/")
    return paths


def stylesheet():
    c = C
    i = _icons(c)
    grad = GRAD.format(a=c["grad_a"], b=c["grad_b"])
    grad_hover = GRAD.format(a=QColor(c["grad_a"]).lighter(108).name(), b=QColor(c["grad_b"]).lighter(110).name())
    return f"""
* {{ outline: none; }}
QMainWindow, QDialog, QMessageBox {{ background: {c['bg']}; }}
QWidget {{ color: {c['text']}; }}
QToolTip {{ background: {c['card']}; color: {c['text']}; border: 1px solid {c['border_hi']};
            border-radius: 8px; padding: 6px 8px; }}

/* ---------- header ---------- */
QFrame#header {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 18px; }}
QLabel#logo {{ background: {grad}; border-radius: 14px;
               font-size: 20pt; color: {c['on_accent']}; }}
QLabel#appTitle {{ font-size: 16pt; font-weight: bold; }}
QLabel#appSub {{ color: {c['muted']}; }}
QLabel#pill {{ background: {c['hover']}; color: {c['accent']}; border: 1px solid {c['border_hi']};
               border-radius: 11px; padding: 2px 10px; font-weight: bold; font-size: 9pt; }}
QPushButton#ghost {{ background: transparent; border: 1px solid {c['border']}; border-radius: 12px;
                     padding: 6px 14px; }}
QPushButton#ghost::menu-indicator {{ image: none; width: 0; }}
QPushButton#ghost:hover {{ background: {c['hover']}; border-color: {c['accent']}; }}

/* ---------- tabs ---------- */
QTabWidget::pane {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 18px;
                    top: -1px; }}
QTabWidget > QStackedWidget > QWidget {{ background: transparent; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; border: none; border-radius: 12px;
                padding: 8px 7px; margin: 0 1px 8px 0; font-weight: bold; }}
QTabBar::tab:hover {{ background: {c['hover']}; color: {c['text']}; }}
QTabBar::tab:selected {{ background: {grad}; color: {c['on_accent']}; }}
QTabBar QToolButton {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 8px; }}

/* ---------- cards ---------- */
QGroupBox {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 16px;
             margin-top: 24px; font-weight: bold; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 10px; top: 0px;
                    padding: 0 4px; color: {c['accent']}; }}
QGroupBox QLabel, QGroupBox QCheckBox, QGroupBox QRadioButton {{ font-weight: normal; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QSplitter::handle {{ background: transparent; width: 10px; }}

/* ---------- buttons ---------- */
QPushButton {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 11px;
               padding: 7px 14px; }}
QPushButton:hover {{ background: {c['hover']}; border-color: {c['border_hi']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:checked {{ background: {c['selection']}; border-color: {c['accent']}; color: {c['text']}; }}
QPushButton:disabled {{ color: {c['faint']}; background: {c['surface']}; border-color: {c['border']}; }}
QPushButton#primary {{ background: {grad}; color: {c['on_accent']}; border: none; border-radius: 14px;
                       padding: 0 24px; font-weight: bold; font-size: 11pt; }}
QPushButton#primary:hover {{ background: {grad_hover}; }}
QPushButton#primary:disabled {{ background: {c['border']}; color: {c['faint']}; }}

/* ---------- inputs ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {c['input']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 6px 10px;
    selection-background-color: {c['grad_b']}; selection-color: {c['on_accent']}; }}
QPlainTextEdit, QTextEdit {{ padding: 8px; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover,
QPlainTextEdit:hover {{ border-color: {c['border_hi']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus {{ border: 1px solid {c['accent']}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {c['faint']}; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({i['down']}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {c['card']}; border: 1px solid {c['border_hi']}; border-radius: 10px;
                               padding: 4px; selection-background-color: {c['selection']};
                               selection-color: {c['text']}; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    border: none; background: transparent; width: 20px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({i['up']}); width: 12px; height: 12px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({i['down']}); width: 12px; height: 12px; }}

/* ---------- checkbox / radio ---------- */
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; border: 2px solid {c['border_hi']};
                                                 background: {c['input']}; }}
QCheckBox::indicator {{ border-radius: 6px; }}
QRadioButton::indicator {{ border-radius: 11px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox::indicator:checked {{ background: {grad}; border-color: {c['accent']}; image: url({i['check']}); }}
QRadioButton::indicator:checked {{ background: {grad}; border-color: {c['accent']}; image: url({i['dot']}); }}
QCheckBox::indicator:disabled {{ background: {c['surface']}; border-color: {c['border']}; }}

/* ---------- slider ---------- */
QSlider::groove:horizontal {{ height: 6px; background: {c['border']}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {grad}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: white; border: 3px solid {c['accent']}; width: 12px; height: 12px;
                              margin: -6px 0; border-radius: 9px; }}

/* ---------- tables / lists ---------- */
QTableWidget, QTreeWidget, QTreeView, QListWidget {{ background: {c['input']}; alternate-background-color: {c['card']};
    border: 1px solid {c['border']}; border-radius: 12px; gridline-color: {c['border']};
    selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 8px; margin: 1px 4px; }}
QListWidget::item:hover, QTreeWidget::item:hover, QTreeView::item:hover {{ background: {c['hover']}; }}
QListWidget::item:selected, QTreeWidget::item:selected, QTreeView::item:selected {{ background: {c['selection']}; color: {c['text']}; }}
QTreeWidget::item, QTreeView::item {{ padding: 4px 0; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {c['card']}; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 7px 8px; font-weight: bold; }}
QTableCornerButton::section {{ background: {c['card']}; border: none; }}

/* ---------- progress ---------- */
QProgressBar {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 10px; height: 20px;
                text-align: center; font-weight: bold; color: {c['text']}; }}
QProgressBar::chunk {{ border-radius: 9px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {c['grad_a']}, stop:0.6 {c['grad_b']},
                                stop:1 {c['accent3']}); }}

/* ---------- labels ---------- */
QLabel[role="muted"] {{ color: {c['muted']}; }}
QLabel[role="warn"] {{ color: {c['warn']}; }}
QLabel[role="error"] {{ color: {c['error']}; }}
QLabel#stageLabel {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: 12px;
                     padding: 8px 12px; }}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {c['border_hi']}; border-radius: 3px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {c['accent']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- menu ---------- */
QMenu {{ background: {c['card']}; border: 1px solid {c['border_hi']}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 7px; }}
QMenu::item:selected {{ background: {c['selection']}; }}
"""


def hackify(qss):
    """Vector → ជ្រុងតូច + module bevel (ប៊ូតុងដូចគ្រាប់ចុចម៉ាស៊ីន, LCD ខ្មៅ)"""
    qss = re.sub(r"border-radius:\s*(\d+)px", lambda m: f"border-radius: {min(int(m.group(1)), 6)}px", qss)
    return qss + _VECTOR_QSS.format(c=C)


# Vector: module ពណ៌សមាន bevel ខាងក្រោម, ប៊ូតុងដូចគ្រាប់ចុចម៉ាស៊ីន, LCD ខ្មៅ, progress ជាសសរតូចៗ (tick)
_MODULE = ("background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fbfbfa, stop:1 {c[card]}); "
           "border: 1px solid {c[border]}; border-bottom: 2px solid {c[border_hi]};")
_VECTOR_QSS = """
QFrame#header, QGroupBox, QFrame#simpleHero, QFrame#simpleSide, QFrame#simpleFoot {{ """ + _MODULE + """ }}
QTabWidget::pane {{ background: {c[surface]}; border: 1px solid {c[border]}; border-bottom: 2px solid {c[border_hi]}; }}
QGroupBox::title {{ color: {c[muted]}; }}
QLabel#pill {{ background: {c[lcd]}; color: {c[accent]}; border: 1px solid #1b1c1e; }}

QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #e5e6e6);
    color: {c[text]}; border: 1px solid {c[border_hi]}; border-bottom: 2px solid {c[bevel]}; }}
QPushButton:hover {{ border-color: {c[accent]}; border-bottom: 2px solid {c[accent2]}; }}
QPushButton:pressed {{ background: #dcdddd; border-bottom: 1px solid {c[bevel]}; padding-top: 8px; }}
QPushButton:checked {{ background: {c[lcd]}; color: {c[accent]}; border: 1px solid #1b1c1e;
    border-bottom: 2px solid #111213; }}
QPushButton:disabled {{ background: {c[surface]}; color: {c[faint]}; border: 1px solid {c[border]}; }}
QPushButton#ghost {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #e5e6e6);
    border: 1px solid {c[border_hi]}; border-bottom: 2px solid {c[bevel]}; }}
QPushButton#ghost:hover {{ background: #ffffff; border-color: {c[accent]}; border-bottom: 2px solid {c[accent2]}; }}
QPushButton#primary, QPushButton#bigGo {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {c[accent3]}, stop:1 {c[accent2]});
    color: #ffffff; border: 1px solid #c63f0e; border-bottom: 3px solid #9c3109; font-weight: bold; }}
QPushButton#primary:hover, QPushButton#bigGo:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ff8f62, stop:1 {c[accent]}); }}
QPushButton#primary:pressed, QPushButton#bigGo:pressed {{ border-bottom: 1px solid #9c3109; padding-top: 10px; }}
QPushButton#primary:disabled, QPushButton#bigGo:disabled {{ background: {c[lcd]}; color: {c[lcd_text]};
    border: 1px solid #1b1c1e; border-bottom: 3px solid #111213; }}

QTabBar::tab {{ color: {c[muted]}; }}
QTabBar::tab:hover {{ background: {c[card]}; color: {c[text]}; }}
QTabBar::tab:selected {{ background: {c[lcd]}; color: {c[accent]}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {c[input]};
    border: 1px solid {c[border]}; border-top: 2px solid {c[border_hi]}; }}
QCheckBox::indicator, QRadioButton::indicator {{ background: {c[input]}; border: 1px solid {c[bevel]}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {c[accent]}; border-color: #c63f0e; }}

QProgressBar {{ background: {c[lcd]}; color: {c[lcd_text]}; border: 1px solid #1b1c1e; padding: 2px; }}
QProgressBar::chunk {{ background: {c[accent]}; width: 4px; margin-right: 2px; border-radius: 0px; }}
QProgressBar#cardBar, QProgressBar#bigBar {{ background: {c[lcd]}; border: 1px solid #1b1c1e; padding: 2px; }}
QProgressBar#cardBar::chunk, QProgressBar#bigBar::chunk {{ background: {c[accent]}; width: 3px; margin-right: 2px;
    border-radius: 0px; }}

QHeaderView::section {{ background: {c[surface]}; }}
QMenu::item:selected {{ background: {c[lcd]}; color: {c[accent]}; }}
QToolTip {{ background: {c[lcd]}; color: {c[lcd_text]}; border: 1px solid #1b1c1e; }}

QLabel#leds {{ color: {c[accent]}; font-size: 8pt; }}
QLabel#serial {{ color: {c[muted]}; font-size: 8pt; }}
QLabel#led {{ color: {c[faint]}; font-size: 11pt; }}
QLabel#led[state="running"] {{ color: {c[accent]}; }}
QLabel#led[state="done"] {{ color: {c[success]}; }}
QLabel#led[state="failed"] {{ color: {c[error]}; }}
QLabel#chip {{ background: {c[card]}; color: {c[muted]}; border: 1px solid {c[border_hi]};
    border-bottom: 2px solid {c[bevel]}; }}
QLabel#chip[state="active"] {{ background: {c[lcd]}; color: {c[accent]}; border: 1px solid #1b1c1e;
    border-bottom: 2px solid #111213; }}
QLabel#chip[state="done"] {{ background: {c[card]}; color: {c[success]}; border-color: {c[success]}; }}
QLabel#simpleArrow {{ color: {c[accent]}; }}
QFrame#dropZone {{ background: {c[surface]}; border: 2px dashed {c[border_hi]}; }}
QFrame#dropZone:hover, QFrame#dropZone[hover="true"] {{ background: #ffffff; border-color: {c[accent]}; }}
QFrame#storyCard {{ """ + _MODULE + """ border-left: 3px solid {c[border_hi]}; }}
QFrame#storyCard[state="running"] {{ border-left: 3px solid {c[accent]}; }}
QFrame#storyCard[state="done"] {{ border-left: 3px solid {c[success]}; }}
QFrame#storyCard[state="failed"] {{ border-left: 3px solid {c[error]}; }}
QLabel#statusChip {{ background: {c[lcd]}; color: {c[lcd_text]}; }}
QLabel#statusChip[state="running"] {{ color: {c[accent]}; }}
QLabel#statusChip[state="done"] {{ color: #7ee2a2; }}
QLabel#statusChip[state="failed"] {{ color: #ff8a7a; }}
QLabel#simpleHeadline, QLabel#lcd {{ background: {c[lcd]}; color: {c[lcd_text]}; border: 1px solid #1b1c1e;
    border-radius: 4px; padding: 7px 12px; }}
QLabel#sideHead {{ border-bottom: 1px dashed {c[border_hi]}; padding-bottom: 6px; }}
"""


def palette():
    c = C
    p = QPalette()
    for role, key in [(QPalette.Window, "bg"), (QPalette.Base, "input"), (QPalette.AlternateBase, "card"),
                      (QPalette.Button, "card"), (QPalette.WindowText, "text"), (QPalette.Text, "text"),
                      (QPalette.ButtonText, "text"), (QPalette.ToolTipBase, "card"), (QPalette.ToolTipText, "text"),
                      (QPalette.Highlight, "grad_b"), (QPalette.PlaceholderText, "faint"), (QPalette.Link, "accent")]:
        p.setColor(role, QColor(c[key]))
    p.setColor(QPalette.HighlightedText, QColor(c["on_accent"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(c["faint"]))
    return p


def mono(font):
    """font monospace (Consolas) — អក្សរខ្មែរនៅតែប្រើ font កម្មវិធី (Kantumruy)"""
    font.setFamilies(["Consolas"] + [f for f in (_base_families or font.families()) if f != "Consolas"])
    return font


def apply(qt_app, new_mode="vector"):
    global mode
    mode = "vector"  # theme តែមួយ (Dark/Light/Matrix ចាស់ → Vector)
    C.clear()
    C.update(THEMES[mode])
    global _base_families
    if _base_families is None:
        _base_families = qt_app.font().families() or [qt_app.font().family()]
    font = qt_app.font()
    font.setFamilies([f for f in _base_families if f != "Consolas"])
    qt_app.setFont(font)
    qt_app.setPalette(palette())
    qt_app.setStyleSheet(hackify(stylesheet()))
