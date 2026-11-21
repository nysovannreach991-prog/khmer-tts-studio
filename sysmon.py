"""វាស់កុំព្យូទ័រ — CPU / RAM / GPU (ប្រើប្រាស់ + សីតុណ្ហភាព) · បង្ហាញជារបារខាងក្រោមកម្មវិធី
គ្មាន package បន្ថែម: CPU/RAM ពី Windows API, GPU NVIDIA ពី nvml.dll (មកជាមួយ driver),
GPU ផ្សេងៗ និងសីតុណ្ហភាព CPU ពី Performance Counter (PDH)
SysBar ក៏បង្ហាញថា AI (ស្តាប់សំឡេង / លុបការនិយាយ) និង encode វីដេអូ ប្រើ GPU ឬ CPU"""
import ctypes
import os
import threading
import time
from ctypes import wintypes

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PyQt5.QtWidgets import QWidget

import theme

INTERVAL = 1.5  # វិនាទី រវាងការវាស់


# ---------------- CPU / RAM (kernel32) ----------------

class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _ram():
    m = _MemStatus()
    m.dwLength = ctypes.sizeof(m)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return None
    return (m.ullTotalPhys - m.ullAvailPhys) / 2 ** 30, m.ullTotalPhys / 2 ** 30


def _cpu_times():
    idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
    v = [(t.dwHighDateTime << 32) | t.dwLowDateTime for t in (idle, kern, user)]
    return v[0], v[1] + v[2]  # kernel រួមបញ្ចូល idle រួចហើយ


# ---------------- PDH (Performance Counter) ----------------

class _PdhValue(ctypes.Structure):
    _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]


class _PdhItem(ctypes.Structure):
    _fields_ = [("szName", wintypes.LPWSTR), ("FmtValue", _PdhValue)]


class _Pdh:
    """counter ច្រើន instance (ឧ. GPU Engine(*)) → {ឈ្មោះ instance: តម្លៃ}"""

    def __init__(self, paths):
        self.pdh = ctypes.WinDLL("pdh")
        self.query = ctypes.c_void_p()
        self.counters = {}
        if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) != 0:
            raise OSError("PdhOpenQuery")
        for key, path in paths.items():
            c = ctypes.c_void_p()
            if self.pdh.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(c)) == 0:
                self.counters[key] = c
        self.pdh.PdhCollectQueryData(self.query)

    def read(self):
        self.pdh.PdhCollectQueryData(self.query)
        out = {}
        for key, c in self.counters.items():
            size, count = wintypes.DWORD(0), wintypes.DWORD(0)
            self.pdh.PdhGetFormattedCounterArrayW(c, 0x200, ctypes.byref(size), ctypes.byref(count), None)
            if not size.value:
                continue
            buf = (ctypes.c_byte * size.value)()
            if self.pdh.PdhGetFormattedCounterArrayW(c, 0x200, ctypes.byref(size), ctypes.byref(count), buf) != 0:
                continue
            items = ctypes.cast(buf, ctypes.POINTER(_PdhItem))
            out[key] = {items[i].szName: items[i].FmtValue.doubleValue
                        for i in range(count.value) if items[i].FmtValue.CStatus in (0, 1)}
        return out


# ---------------- NVIDIA (nvml.dll) ----------------

class _NvUtil(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _NvMem(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _Nvml:
    def __init__(self):
        dll = None
        for path in ("nvml.dll", r"C:\Windows\System32\nvml.dll",
                     os.path.join(os.environ.get("ProgramW6432", r"C:\Program Files"),
                                  r"NVIDIA Corporation\NVSMI\nvml.dll")):
            try:
                dll = ctypes.CDLL(path)
                break
            except OSError:
                continue
        if dll is None or dll.nvmlInit_v2() != 0:
            raise OSError("nvml")
        self.dll, self.dev = dll, ctypes.c_void_p()
        if dll.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self.dev)) != 0:
            raise OSError("nvml device")
        name = ctypes.create_string_buffer(96)
        dll.nvmlDeviceGetName(self.dev, name, 96)
        self.name = name.value.decode("utf-8", "replace")

    def read(self):
        temp, util, mem = ctypes.c_uint(), _NvUtil(), _NvMem()
        ok_t = self.dll.nvmlDeviceGetTemperature(self.dev, 0, ctypes.byref(temp)) == 0
        ok_u = self.dll.nvmlDeviceGetUtilizationRates(self.dev, ctypes.byref(util)) == 0
        ok_m = self.dll.nvmlDeviceGetMemoryInfo(self.dev, ctypes.byref(mem)) == 0
        return {"gpu": util.gpu if ok_u else None, "gpu_temp": temp.value if ok_t else None,
                "vram": (mem.used / 2 ** 30, mem.total / 2 ** 30) if ok_m else None}


# ---------------- អ្នកវាស់ (thread ខាងក្រោយ) ----------------

class Monitor:
    def __init__(self):
        self.data = {}
        self.nvml = None
        self.lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return dict(self.data)

    def _loop(self):
        try:
            self.nvml = _Nvml()
        except Exception:  # noqa: BLE001 — គ្មានកាត NVIDIA / driver ចាស់
            self.nvml = None
        paths = {"temp": r"\Thermal Zone Information(*)\Temperature"}
        if not self.nvml:
            paths["engine"] = r"\GPU Engine(*)\Utilization Percentage"
        try:
            pdh = _Pdh(paths)
        except Exception:  # noqa: BLE001
            pdh = None
        prev = _cpu_times()
        while True:
            time.sleep(INTERVAL)
            d = {"gpu_name": self.nvml.name if self.nvml else None}
            idle, total = _cpu_times()
            if total > prev[1]:
                d["cpu"] = max(0.0, min(100.0, 100.0 * (1 - (idle - prev[0]) / (total - prev[1]))))
            prev = (idle, total)
            d["ram"] = _ram()
            if self.nvml:
                try:
                    d.update(self.nvml.read())
                except Exception:  # noqa: BLE001
                    pass
            if pdh:
                try:
                    c = pdh.read()
                except Exception:  # noqa: BLE001
                    c = {}
                temps = [v - 273.15 for v in c.get("temp", {}).values() if v > 200]
                d["cpu_temp"] = max(temps) if temps else None
                if "engine" in c:  # GPU Intel/AMD: យក engine ដែលរវល់ជាងគេ (ដូច Task Manager)
                    by = {}
                    for name, v in c["engine"].items():
                        kind = name.rsplit("engtype_", 1)[-1]
                        by[kind] = by.get(kind, 0.0) + v
                    d["gpu"] = min(100.0, max(by.values())) if by else None
            with self.lock:
                self.data = d


_monitor = None


def monitor():
    global _monitor
    if _monitor is None:
        _monitor = Monitor()
    return _monitor


# ---------------- របារខាងក្រោម ----------------

class SysBar(QWidget):
    """CPU · RAM · GPU · AI/Encode ប្រើ GPU ឬ CPU — ចុចលើ "AI" ពេលនៅ CPU ដើម្បីបើក GPU,
    ចុចលើ "⚠ Driver" ដើម្បីបើកទំព័រទាញយក driver NVIDIA"""
    gpu_fix = pyqtSignal()
    driver_fix = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.mon = monitor()
        self.status = {}  # {"ai": "gpu"|"cpu"|"fixable", "enc": "h264_nvenc"..., "driver": "610.00"}
        self.active = None  # ឧបករណ៍ដែលការងារកំពុងប្រើ ("cuda"/"cpu")
        self.fix_rects = {}  # ប៊ូតុងក្នុងរបារ: "gpu"/"driver" → QRectF
        self.upload = None  # ដូច download — Upload ទៅ Google Drive
        self.download = None  # (frac 0..1 ឬ -1 = មិនដឹង %, អក្សរ) ពេលកំពុងទាញយក/ដំឡើង
        self.setMouseTracking(True)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(1000)

    def set_status(self, **kw):
        self.status.update(kw)
        self.update()

    def set_download(self, frac, text=""):
        """បង្ហាញ progress bar ទាញយកនៅខាងស្តាំរបារ — frac=None បិទវិញ"""
        self.download = None if frac is None else (frac, text)
        self._sync_timer()

    def set_upload(self, frac, text=""):
        """progress bar Upload ទៅ Google Drive (ការទាញយក GPU មានអាទិភាពជាង) — frac=None បិទវិញ"""
        self.upload = None if frac is None else (frac, text)
        self._sync_timer()

    def _sync_timer(self):
        self.timer.setInterval(120 if self.download or self.upload else 1000)  # ចលនាលឿនពេលមាន progress
        self.update()

    def _paint_download(self, p, r, x_min, frac, text, lab_f, val_f):
        c = theme.C
        w = min(640.0, r.right() - 12 - x_min - 20)
        if w < 260:
            return
        box = QRectF(r.right() - 12 - w, r.top() + 6, w, r.height() - 12)
        p.setPen(QPen(QColor(c["accent"]), 1))
        p.setBrush(QColor("#1f2123"))
        p.drawRoundedRect(box, 3, 3)
        label = text + ("" if frac < 0 else f"   {frac * 100:.0f}%")
        text_w = QFontMetricsF(val_f).horizontalAdvance(label) + 20
        inner = box.adjusted(3, 3, -3 - text_w, -3)
        p.setPen(Qt.NoPen)
        n = max(12, int(inner.width() / 7))
        step = inner.width() / n
        if frac < 0:  # មិនដឹង % → សញ្ញារត់ពីឆ្វេងទៅស្តាំ
            head = (time.time() * 14) % (n + 6)
            lit = [i for i in range(n) if 0 <= head - i < 6]
        else:
            lit = range(round(max(0.0, min(1.0, frac)) * n))
        for i in lit:
            p.setBrush(QColor(c["accent"]))
            p.drawRect(QRectF(inner.left() + i * step, inner.top(), step - 1.5, inner.height()))
        p.setFont(val_f)
        p.setPen(QColor("#ffffff"))
        p.drawText(box.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignRight, label)

    def set_active(self, device):
        if device != self.active:
            self.active = device
            self.update()

    def _hit(self, pos):
        return next((k for k, rect in self.fix_rects.items() if rect.contains(QPointF(pos))), None)

    def mousePressEvent(self, e):
        hit = self._hit(e.pos())
        if hit:
            (self.gpu_fix if hit == "gpu" else self.driver_fix).emit()

    def mouseMoveEvent(self, e):
        self.setCursor(Qt.PointingHandCursor if self._hit(e.pos()) else Qt.ArrowCursor)

    @staticmethod
    def _temp_color(t):
        if t is None:
            return theme.C["faint"]
        return "#ff6b5a" if t >= 85 else theme.C["accent"] if t >= 70 else "#7ee2a2"

    def paintEvent(self, _):
        import vector_fx
        c, d = theme.C, self.mon.snapshot()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor("#1b1c1e"), 1))
        p.setBrush(QColor(c["lcd"]))
        p.drawRoundedRect(r, 5, 5)
        lab_f = vector_fx._condensed(self.font(), 8)
        val_f = theme.mono(self.font())
        val_f.setPointSizeF(8.5)
        lab_m, val_m = QFontMetricsF(lab_f), QFontMetricsF(val_f)
        x, cy = 12.0, r.center().y()
        compact = self.width() < 1100

        def text(s, color, font, fm):
            nonlocal x
            p.setFont(font)
            p.setPen(QColor(color))
            w = fm.horizontalAdvance(s)
            p.drawText(QRectF(x, r.top(), w + 2, r.height()), Qt.AlignVCenter | Qt.AlignLeft, s)
            x += w

        def meter(frac, w=58):
            nonlocal x
            box = QRectF(x + 6, cy - 4, w, 8)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#1f2123"))
            p.drawRoundedRect(box, 2, 2)
            n = 12
            lit = 0 if frac is None else round(max(0.0, min(1.0, frac)) * n)
            step = (w - 6) / n
            for i in range(n):
                hot = frac is not None and frac > 0.9
                col = ("#ff6b5a" if hot else c["accent"]) if i < lit else "#44474b"
                p.setBrush(QColor(col))
                p.drawRect(QRectF(box.left() + 3 + i * step, box.top() + 2, step - 1.5, 4))
            x += w + 12

        def sep():
            nonlocal x
            p.setPen(QPen(QColor("#4a4d51"), 1))
            p.drawLine(QPointF(x + 10, r.top() + 8), QPointF(x + 10, r.bottom() - 8))
            x += 22

        def pct(v):
            return "--%" if v is None else f"{v:>3.0f}%"

        def deg(v):
            return "--°C" if v is None else f"{v:.0f}°C"

        # CPU
        text("CPU", c["faint"], lab_f, lab_m)
        cpu = d.get("cpu")
        meter(None if cpu is None else cpu / 100)
        text(pct(cpu), c["lcd_text"], val_f, val_m)
        x += 8
        text(deg(d.get("cpu_temp")), self._temp_color(d.get("cpu_temp")), val_f, val_m)
        sep()
        # RAM
        text("RAM", c["faint"], lab_f, lab_m)
        ram = d.get("ram")
        meter(None if not ram else ram[0] / ram[1])
        text("--" if not ram else f"{ram[0]:.1f}/{ram[1]:.0f} GB", c["lcd_text"], val_f, val_m)
        sep()
        # GPU
        name = d.get("gpu_name")
        label = "GPU"
        if name and not compact:
            label = "GPU " + name.replace("NVIDIA ", "").replace("GeForce ", "")
        text(label, c["faint"], lab_f, lab_m)
        gpu = d.get("gpu")
        meter(None if gpu is None else gpu / 100)
        text(pct(gpu), c["lcd_text"], val_f, val_m)
        if "gpu_temp" in d:
            x += 8
            text(deg(d.get("gpu_temp")), self._temp_color(d.get("gpu_temp")), val_f, val_m)
        if d.get("vram") and not compact:
            x += 8
            text(f"VRAM {d['vram'][0]:.1f}/{d['vram'][1]:.0f} GB", c["faint"], val_f, val_m)

        if self.download or self.upload:  # ទាញយក GPU / Upload → progress bar ជំនួសស្លាក AI/Encode
            self.fix_rects = {}
            self._paint_download(p, r, x, *(self.download or self.upload), lab_f, val_f)
            p.end()
            return
        # ខាងស្តាំ: AI / Encode ប្រើ GPU ឬ CPU
        st = self.status
        ai = st.get("ai")
        enc = st.get("enc")
        parts = []
        if enc:
            gpu_enc = enc != "libx264"
            enc_name = {"h264_nvenc": "NVENC", "h264_qsv": "Intel QSV", "h264_amf": "AMD AMF"}.get(enc, "x264")
            parts.append(("ENCODE", ("⚡ " if gpu_enc else "💻 ") + enc_name, "#7ee2a2" if gpu_enc else c["accent"], None))
            if st.get("driver") and enc != "h264_nvenc":  # NVENC ប្រើមិនបាន ព្រោះ driver ចាស់
                parts.append(("", f"⚠ Update Driver NVIDIA ≥{st['driver'].split('.')[0]}", "#ff6b5a", "driver"))
        if ai:
            if ai == "gpu":
                parts.append(("AI", "⚡ GPU", "#7ee2a2", None))
            elif ai == "fixable":
                parts.append(("AI", "💻 CPU · ⚡ បើក GPU", "#ff6b5a", "gpu"))
            else:
                parts.append(("AI", "💻 CPU", c["faint"], None))
        if self.active:
            on_gpu = "cuda" in self.active
            parts.append(("ឥឡូវ", "⚡ GPU" if on_gpu else "💻 CPU", "#7ee2a2" if on_gpu else "#ff6b5a", None))
        self.fix_rects = {}
        xr = r.right() - 12
        for lab, val, col, fix in reversed(parts):
            vw, lw = val_m.horizontalAdvance(val), lab_m.horizontalAdvance(lab)
            box = QRectF(xr - vw - 14, r.top() + 5, vw + 14, r.height() - 10)
            if box.left() - lw - 8 < x + 12:
                break
            p.setPen(QPen(QColor(col), 1))
            p.setBrush(QColor(col).darker(400) if fix else Qt.NoBrush)
            p.drawRoundedRect(box, 3, 3)
            p.setFont(val_f)
            p.drawText(box, Qt.AlignCenter, val)
            if fix:
                self.fix_rects[fix] = box
            p.setFont(lab_f)
            p.setPen(QColor(c["faint"]))
            p.drawText(QRectF(box.left() - lw - 6, r.top(), lw, r.height()), Qt.AlignVCenter | Qt.AlignRight, lab)
            xr = box.left() - (lw + 18 if lw else 8)
        p.end()
