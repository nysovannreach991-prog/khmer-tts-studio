"""រចនាប័ទ្ម Modern / Gen Z — Dark & Light mode, ពណ៌ gradient លឿង→ទឹកក្រូច, ជ្រុងមូល។

C = ពណ៌បច្ចុប្បន្ន (dict ដែលប្តូរតាម mode) — gui.py ប្រើវាសម្រាប់ rich text និងពណ៌ក្នុងតារាង។
"""
import os
import tempfile

from PyQt5.QtGui import QColor, QPalette

DARK = {
    "bg": "#0e0d0a", "surface": "#17150f", "card": "#1f1c14", "input": "#13110c", "hover": "#2a261b",
    "border": "#2f2a1e", "border_hi": "#4a4230", "text": "#fbf8ef", "muted": "#b0a88f", "faint": "#756d58",
    "accent": "#facc15", "accent2": "#f59e0b", "accent3": "#fb923c", "on_accent": "#1a1400",
    "grad_a": "#fde047", "grad_b": "#f59e0b",
    "success": "#34d399", "error": "#fb7185", "warn": "#fb923c",
    "female": "#f472b6", "male": "#60a5fa", "selection": "#3d3418",
}
LIGHT = {
    "bg": "#fbf9f1", "surface": "#ffffff", "card": "#fffdf6", "input": "#ffffff", "hover": "#fdf6dc",
    "border": "#efe7cc", "border_hi": "#e2d29a", "text": "#1f1a0d", "muted": "#6f6650", "faint": "#aca38a",
    "accent": "#b45309", "accent2": "#d97706", "accent3": "#ea580c", "on_accent": "#1f1a0d",
    "grad_a": "#fcd34d", "grad_b": "#f59e0b",
    "success": "#059669", "error": "#e11d48", "warn": "#c2410c",
    "female": "#db2777", "male": "#2563eb", "selection": "#fdefb2",
}
C = dict(DARK)
mode = "dark"

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
QPushButton#ghost:hover {{ background: {c['hover']}; border-color: {c['accent']}; }}

/* ---------- tabs ---------- */
QTabWidget::pane {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 18px;
                    top: -1px; }}
QTabWidget > QStackedWidget > QWidget {{ background: transparent; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; border: none; border-radius: 12px;
                padding: 8px 16px; margin: 0 4px 8px 0; font-weight: bold; }}
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
QTableWidget, QTreeWidget, QListWidget {{ background: {c['input']}; alternate-background-color: {c['card']};
    border: 1px solid {c['border']}; border-radius: 12px; gridline-color: {c['border']};
    selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 8px; margin: 1px 4px; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: {c['hover']}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: {c['selection']}; color: {c['text']}; }}
QTreeWidget::item {{ padding: 4px 0; }}
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


def apply(qt_app, new_mode):
    global mode
    mode = new_mode if new_mode in ("dark", "light") else "dark"
    C.clear()
    C.update(DARK if mode == "dark" else LIGHT)
    qt_app.setPalette(palette())
    qt_app.setStyleSheet(stylesheet())
