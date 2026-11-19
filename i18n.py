"""English version — បកប្រែអក្សរ UI ខ្មែរ → អង់គ្លេស ពេលបង្ហាញ (កូដដើមនៅជាខ្មែរដដែល)។

* ភាសារក្សាក្នុង QSettings("KhmerTTS", "Studio") key "lang" = "km" (លំនាំដើម) ឬ "en"
* i18n_en.json = {អក្សរខ្មែរក្នុងកូដ: អង់គ្លេស} — ឃ្លាពេញ ឬបំណែកនៃ f-string
* install(app) (ហៅមុនបង្កើតផ្ទាំង): ចាប់ setText / setToolTip / addItem / drawText ... ហើយបកប្រែពេលបង្ហាញ
  មិនប៉ះ៖ អត្ថបទដែលអ្នកប្រើវាយ (QLineEdit / តារាង / QPlainTextEdit.setPlainText) ដើម្បីកុំឱ្យខូចទិន្នន័យ
"""
import functools
import json
import os
import re

_KH = re.compile(r"[ក-៿]")
_HERE = os.path.dirname(os.path.abspath(__file__))
LANG = "km"
_exact, _frags = {}, []


def _load():
    global _exact, _frags
    try:
        with open(os.path.join(_HERE, "i18n_en.json"), encoding="utf-8") as f:
            _exact = json.load(f)
    except (OSError, ValueError):
        _exact = {}
    # បំណែក: វែងមុន · មិនជំនួសពេលជាប់អក្សរខ្មែរផ្សេង (កុំឱ្យកាត់ពាក់កណ្តាលពាក្យ ឬអត្ថបទរបស់អ្នកប្រើ)
    _frags = [(re.compile(r"(?<![ក-៿])" + re.escape(k) + r"(?![ក-៿])"), v)
              for k, v in sorted(_exact.items(), key=lambda kv: -len(kv[0])) if k.strip()]


@functools.lru_cache(maxsize=8192)
def _tr(s):
    hit = _exact.get(s)
    if hit is not None:
        return hit
    for rx, en in _frags:
        if rx.search(s):
            s = rx.sub(lambda _m, en=en: en, s)
            if not _KH.search(s):
                break
    return s


def tr(s):
    """ខ្មែរ → អង់គ្លេស (ពេល LANG == "en") · អ្វីផ្សេង → ដដែល"""
    if LANG != "en" or not isinstance(s, str) or not s or not _KH.search(s):
        return s
    return _tr(s)


def current():
    from PyQt5.QtCore import QSettings
    if os.environ.get("DUBBER_LANG") in ("en", "km"):  # សម្រាប់សាកល្បង
        return os.environ["DUBBER_LANG"]
    return "en" if str(QSettings("KhmerTTS", "Studio").value("lang", "km")) == "en" else "km"


def set_lang(lang):
    from PyQt5.QtCore import QSettings
    QSettings("KhmerTTS", "Studio").setValue("lang", "en" if lang == "en" else "km")


def _wrap(cls, name, idx=(0,), kw=()):
    """cls.name(...) → បកប្រែ argument str ត្រង់ idx (និង keyword kw) មុនហៅដើម"""
    orig = getattr(cls, name, None)
    if orig is None:
        return

    def patched(self, *args, **kwargs):
        if args:
            args = list(args)
            for i in idx:
                if i < len(args) and isinstance(args[i], str):
                    args[i] = tr(args[i])
        for k in kw:
            if isinstance(kwargs.get(k), str):
                kwargs[k] = tr(kwargs[k])
        return orig(self, *args, **kwargs)
    patched.__name__ = name
    setattr(cls, name, patched)


def _wrap_static(cls, name, idx):
    orig = getattr(cls, name, None)
    if orig is None:
        return

    def patched(*args, **kwargs):
        args = list(args)
        for i in idx:
            if i < len(args) and isinstance(args[i], str):
                args[i] = tr(args[i])
        return orig(*args, **kwargs)
    setattr(cls, name, staticmethod(patched))


def _wrap_any(cls, name):
    """បកប្រែ argument str ទាំងអស់ (drawText / horizontalAdvance មាន overload ច្រើន)"""
    orig = getattr(cls, name, None)
    if orig is None:
        return

    def patched(self, *args, **kwargs):
        return orig(self, *[tr(a) if isinstance(a, str) else a for a in args], **kwargs)
    setattr(cls, name, patched)


def install(app):
    """ហៅម្តង មុនបង្កើត widget ណាមួយ · LANG == "km" → មិនធ្វើអ្វីទេ"""
    global LANG
    LANG = current()
    if LANG != "en":
        return False
    _load()
    from PyQt5.QtCore import QEvent, QObject
    from PyQt5.QtGui import QFontMetrics, QFontMetricsF, QPainter
    from PyQt5.QtWidgets import (
        QAbstractButton, QAbstractSpinBox, QAction, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QInputDialog,
        QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QSpinBox, QStatusBar, QTabWidget, QTableWidget,
        QTextEdit, QToolTip, QTreeWidget, QWidget,
    )
    for cls in (QLabel, QAbstractButton, QAction, QMessageBox):
        _wrap(cls, "setText")
    for cls in (QWidget, QAction):
        _wrap(cls, "setToolTip")
    for cls, names in ((QWidget, ("setWindowTitle",)), (QGroupBox, ("setTitle",)),
                       (QLineEdit, ("setPlaceholderText",)), (QPlainTextEdit, ("setPlaceholderText", "appendPlainText")),
                       (QTextEdit, ("setPlaceholderText", "append")), (QSpinBox, ("setSuffix", "setPrefix")),
                       (QDoubleSpinBox, ("setSuffix", "setPrefix")), (QAbstractSpinBox, ("setSpecialValueText",)),
                       (QProgressBar, ("setFormat",)), (QStatusBar, ("showMessage",)),
                       (QMessageBox, ("setInformativeText", "setDetailedText")), (QMenu, ("addSection", "setTitle"))):
        for name in names:
            _wrap(cls, name)
    _wrap(QTabWidget, "addTab", idx=(1, 2))
    _wrap(QTabWidget, "setTabText", idx=(1,))
    _wrap(QTabWidget, "setTabToolTip", idx=(1,))
    _wrap(QFormLayout, "addRow")
    _wrap(QMenu, "addAction", idx=(0, 1))
    _wrap(QMenu, "addMenu", idx=(0, 1))
    _wrap(QComboBox, "setItemText", idx=(1,))
    # combo: កូដប្រើ currentData / currentIndex · currentText() ប្រើតែលើ combo ឈ្មោះ model/format (គ្មានអក្សរខ្មែរ)
    orig_add = QComboBox.addItem

    def add_item(self, *args, **kwargs):
        if args and isinstance(args[0], str):
            args = (tr(args[0]),) + tuple(args[1:])
        elif len(args) >= 3 and isinstance(args[1], str):  # (icon, text, data)
            args = (args[0], tr(args[1])) + tuple(args[2:])
        return orig_add(self, *args, **kwargs)
    QComboBox.addItem = add_item
    orig_items = QComboBox.addItems
    QComboBox.addItems = lambda self, texts: orig_items(self, [tr(x) for x in texts])
    for cls in (QTableWidget,):
        orig_h = cls.setHorizontalHeaderLabels
        cls.setHorizontalHeaderLabels = lambda self, labels, _o=orig_h: _o(self, [tr(x) for x in labels])
    orig_th = QTreeWidget.setHeaderLabels
    QTreeWidget.setHeaderLabels = lambda self, labels: orig_th(self, [tr(x) for x in labels])
    for name in ("information", "warning", "critical", "question", "about"):
        _wrap_static(QMessageBox, name, (1, 2))
    for name in ("getInt", "getDouble", "getText", "getItem", "getMultiLineText"):
        _wrap_static(QInputDialog, name, (1, 2))
    for name in ("getOpenFileName", "getOpenFileNames", "getSaveFileName", "getExistingDirectory"):
        _wrap_static(QFileDialog, name, (1, 3))
    _wrap_static(QToolTip, "showText", (1,))
    _wrap_any(QPainter, "drawText")
    for cls in (QFontMetrics, QFontMetricsF):
        for name in ("horizontalAdvance", "boundingRect", "width", "elidedText"):
            _wrap_any(cls, name)

    class Polish(QObject):
        """អក្សរដែលដាក់ក្នុង constructor (QLabel("…"), QPushButton("…")) → បកប្រែពេល widget ត្រូវ polish (មុនបង្ហាញលើកដំបូង)"""

        def eventFilter(self, w, e):
            if e.type() == QEvent.Polish and isinstance(w, QWidget):
                try:
                    _retext(w)
                except Exception:  # noqa: BLE001 — ការបកប្រែមិនត្រូវបំបែកកម្មវិធីទេ
                    pass
            return False

    def _retext(w):
        if isinstance(w, (QLabel, QAbstractButton)):
            t = w.text()
            if _KH.search(t):
                w.setText(t)
        elif isinstance(w, QGroupBox):
            if _KH.search(w.title()):
                w.setTitle(w.title())
        elif isinstance(w, (QLineEdit, QPlainTextEdit, QTextEdit)):
            if _KH.search(w.placeholderText()):
                w.setPlaceholderText(w.placeholderText())
        elif isinstance(w, QAbstractSpinBox):
            for get, put in (("suffix", "setSuffix"), ("prefix", "setPrefix"), ("specialValueText", "setSpecialValueText")):
                if hasattr(w, get) and _KH.search(getattr(w, get)()):
                    getattr(w, put)(getattr(w, get)())
        elif isinstance(w, QMenu):
            for a in w.actions():
                if _KH.search(a.text()):
                    a.setText(a.text())
        if _KH.search(w.toolTip()):
            w.setToolTip(w.toolTip())
        if w.isWindow() and _KH.search(w.windowTitle()):
            w.setWindowTitle(w.windowTitle())

    app._i18n_polish = Polish(app)
    app.installEventFilter(app._i18n_polish)
    return True
