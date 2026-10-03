"""បង្កើត .srt ពីវីដេអូដោយមិនប្រើ Gemini (ឥតគិតថ្លៃ, គ្មាន API Key):

1. subtitle ដែលមានស្រាប់ក្នុងវីដេអូ (.mkv/.mp4 subtitle track) — ទាញចេញភ្លាមៗ
2. Whisper (faster-whisper) — ស្តាប់សំឡេងលើកុំព្យូទ័រផ្ទាល់ (GPU NVIDIA បើមាន, មិនដូច្នោះទេ CPU)

faster-whisper ដំឡើងតែពេលប្រើលើកដំបូង (មិនមែនគ្រប់គ្នាត្រូវការ)។ Whisper ដំណើរការក្នុង process ដាច់ដោយឡែក
(python whisper_srt.py ...) — GPU មានបញ្ហា មិនធ្វើឱ្យកម្មវិធីគាំង ហើយប្តូរទៅ CPU ដោយស្វ័យប្រវត្តិ។
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AITeam1")
MODEL_DIR = os.path.join(DATA_DIR, "whisper")
# (ឈ្មោះ model, ស្លាក, ទំហំប្រហែល MB)
MODELS = [("small", "⚡ លឿន · small", 480), ("large-v3-turbo", "👍 ល្អ · turbo", 1600),
          ("large-v3", "🏆 ល្អបំផុត · large-v3", 3100)]
LANGUAGES = [("", "🔍 រកឃើញខ្លួនឯង"), ("zh", "中文 ចិន"), ("en", "English"), ("th", "ไทย ថៃ"), ("ko", "한국어 កូរ៉េ"),
             ("ja", "日本語 ជប៉ុន"), ("vi", "Tiếng Việt"), ("id", "Indonesia"), ("hi", "हिन्दी ហិណ្ឌូ"),
             ("km", "ខ្មែរ")]
WHISPER_PACKAGES = ["faster-whisper>=1.1"]
GPU_PACKAGES = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12>=9,<10"]
TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}
MAX_SEC, MAX_CHARS_CJK, MAX_CHARS = 6.0, 18, 42  # ប្រវែងអតិបរមានៃ subtitle មួយបន្ទាត់
_END = re.compile(r"[。！？!?…]$|[.]$")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\u0e00-\u0e7f\u1780-\u17ff]")
jobs = None  # ប្រើ srt_dub.jobs (កំណត់ក្នុង start_job)


# ---------------- subtitle ស្រាប់ក្នុងវីដេអូ ----------------

def embedded_tracks(video):
    """[{"index", "codec", "lang", "title", "text", "default"}] — text=False: subtitle ជារូបភាព (PGS/VobSub) ទាញមិនបាន"""
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries",
                           "stream=index,codec_name:stream_tags=language,title:stream_disposition=default",
                           "-of", "json", video], capture_output=True, creationflags=_NO_WINDOW)
    try:
        streams = json.loads(proc.stdout.decode("utf-8", "replace")).get("streams", [])
    except ValueError:
        return []
    return [{"index": i, "codec": s.get("codec_name", ""), "lang": s.get("tags", {}).get("language", ""),
             "title": s.get("tags", {}).get("title", ""), "text": s.get("codec_name", "") in TEXT_SUBS,
             "default": bool(s.get("disposition", {}).get("default"))} for i, s in enumerate(streams)]


def pick_track(tracks, lang=""):
    """subtitle អក្សរ: ភាសាដែលជ្រើស → default → ទីមួយ"""
    text = [t for t in tracks if t["text"]]
    if not text:
        return None
    want = {"zh": ("zh", "chi", "zho"), "en": ("en", "eng"), "th": ("th", "tha"), "ko": ("ko", "kor"),
            "ja": ("ja", "jpn"), "vi": ("vi", "vie"), "km": ("km", "khm")}.get(lang, (lang,) if lang else ())
    return (next((t for t in text if t["lang"].lower() in want), None) if want else None) or \
        next((t for t in text if t["default"]), None) or text[0]


def extract_embedded(video, track, out_srt):
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", video,
                           "-map", f"0:s:{track['index']}", "-c:s", "srt", out_srt],
                          capture_output=True, creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(f"ទាញ subtitle មិនបាន: {proc.stderr.decode('utf-8', 'replace')[-200:]}")
    with open(out_srt, encoding="utf-8-sig", errors="replace") as f:
        return f.read().count("-->")


# ---------------- ដំឡើង ----------------

def _site_packages():
    import site
    paths = list(site.getsitepackages()) + [site.getusersitepackages()]
    return [p for p in paths if p and os.path.isdir(p)]


def installed():
    """faster-whisper >= 1.1 (កំណែចាស់មានបញ្ហាពេលវីដេអូគ្មានការនិយាយ)"""
    try:
        from importlib.metadata import version
        major, minor = (int(x) for x in version("faster-whisper").split(".")[:2])
        return (major, minor) >= (1, 1)
    except Exception:  # noqa: BLE001
        return False


def gpu_dll_dirs():
    return [d for sp in _site_packages() for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin"))]


def has_nvidia():
    smi = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    try:
        return subprocess.run([smi, "-L"], capture_output=True, timeout=10,
                              creationflags=_NO_WINDOW).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def gpu_ready():
    dirs = gpu_dll_dirs()
    return any(glob.glob(os.path.join(d, "cublas64_12.dll")) for d in dirs) and \
        any(glob.glob(os.path.join(d, "cudnn*64_9.dll")) for d in dirs)


def _python():
    """python.exe (មិនមែន pythonw) — សម្រាប់ pip និង process Whisper"""
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        console = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(console):
            return console
    return exe


def pip_install(packages, on_line=None):
    """ដំឡើង package ដោយ pip — on_line(text) ទទួលបន្ទាត់វឌ្ឍនភាព"""
    proc = subprocess.Popen([_python(), "-m", "pip", "install", "--disable-pip-version-check",
                             "--no-warn-script-location", "--progress-bar", "off", *packages],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=_NO_WINDOW)
    tail = []
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").strip()
        if line:
            tail = (tail + [line])[-15:]
            if on_line:
                on_line(line)
    if proc.wait() != 0:
        raise RuntimeError("ដំឡើងមិនបាន — ពិនិត្យអ៊ីនធឺណិត ហើយព្យាយាមម្តងទៀត\n" + "\n".join(tail[-5:]))


# ---------------- Whisper → SRT ----------------

def _fmt(sec):
    ms = int(round(max(sec, 0) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def split_words(words):
    """ពាក្យ (start, end, text) → បន្ទាត់ subtitle ខ្លីៗ (មិនលើស MAX_SEC / MAX_CHARS, កាត់នៅចុងប្រយោគ ឬចន្លោះស្ងាត់)"""
    lines, cur = [], []

    def flush():
        if cur:
            text = "".join(w[2] for w in cur).strip()
            if text:
                lines.append((cur[0][0], cur[-1][1], text))
            cur.clear()

    for w in words:
        if cur and w[0] - cur[-1][1] > 0.7:  # ចន្លោះស្ងាត់ → បន្ទាត់ថ្មី
            flush()
        cur.append(w)
        text = "".join(x[2] for x in cur).strip()
        limit = MAX_CHARS_CJK if _CJK.search(text) else MAX_CHARS
        if _END.search(w[2].strip()) or len(text) >= limit or cur[-1][1] - cur[0][0] >= MAX_SEC:
            flush()
    flush()
    return lines


def _transcribe(media, out_srt, model_name, lang, device):
    """ដំណើរការក្នុង process កូន — បោះពុម្ព JSON មួយបន្ទាត់ៗសម្រាប់វឌ្ឍនភាព"""
    for d in gpu_dll_dirs():  # cuBLAS / cuDNN ពី pip (nvidia-*-cu12)
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(d)
        except OSError:
            pass
    from faster_whisper import WhisperModel

    def say(**msg):
        print(json.dumps(msg, ensure_ascii=False), flush=True)

    say(phase="load", device=device)
    model = WhisperModel(model_name, device=device, compute_type="float16" if device == "cuda" else "int8",
                         download_root=MODEL_DIR)
    say(phase="listen", device=device)
    segments, info = model.transcribe(
        media, language=lang or None, vad_filter=True, word_timestamps=True,
        condition_on_previous_text=False,  # កុំឱ្យជាប់ធ្វើដដែលៗ (hallucination) ក្នុងរឿងវែង
        initial_prompt="以下是普通话的句子。" if lang == "zh" else None)  # ចិនអក្សរសាមញ្ញ
    say(phase="listen", total=round(info.duration, 1), language=info.language)
    lines = []
    for seg in segments:
        words = [(w.start, w.end, w.word) for w in (seg.words or [])] or [(seg.start, seg.end, seg.text)]
        lines += split_words(words)
        say(progress=round(seg.end, 1))
    with open(out_srt, "w", encoding="utf-8") as f:
        for i, (a, b, text) in enumerate(lines, 1):
            f.write(f"{i}\n{_fmt(a)} --> {_fmt(b)}\n{text}\n\n")
    say(done=len(lines), language=info.language)


def _model_dir_mb():
    total = 0
    for root, _, files in os.walk(MODEL_DIR):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total / 1e6


def start_job(media, out_srt, model_name, lang, use_gpu):
    """Whisper ក្នុង process ដាច់ដោយឡែក → job (srt_dub.jobs): status "download" → "running" → "done"
    job["done"]/["total"] = វិនាទីដែលបានស្តាប់ / រយៈពេលសរុប"""
    from srt_dub import jobs as _jobs
    job_id = uuid.uuid4().hex[:12]
    job = _jobs[job_id] = {"status": "download", "done": 0, "total": 100, "error": None, "warnings": [],
                           "device": None, "language": None, "lines": 0}
    size_mb = dict((m, s) for m, _, s in MODELS).get(model_name, 1000)

    def run_once(device):
        before = _model_dir_mb()
        proc = subprocess.Popen([_python(), os.path.abspath(__file__), media, out_srt, model_name, lang or "-", device],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
        err = []
        threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True).start()
        state = {"phase": "load"}

        def watch_download():  # ទាញយក model លើកដំបូង → បង្ហាញភាគរយពីទំហំថត
            while proc.poll() is None and state["phase"] == "load":
                grown = _model_dir_mb() - before
                if grown > 5:
                    job.update(status="download", done=min(99, int(grown * 100 / size_mb)), total=100)
                time.sleep(0.5)
        threading.Thread(target=watch_download, daemon=True).start()
        for raw in proc.stdout:
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if msg.get("phase") == "listen":
                state["phase"] = "listen"
                job.update(status="running", device=device)
                if "total" in msg:
                    job.update(total=max(int(msg["total"]), 1), done=0, language=msg.get("language"))
            elif "progress" in msg:
                job["done"] = min(int(msg["progress"]), job["total"])
            elif "done" in msg:
                job.update(lines=msg["done"], language=msg.get("language"))
        proc.wait()
        state["phase"] = "end"
        if proc.returncode != 0:
            text = b"".join(err).decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError(text[-1] if text else f"Whisper exit {proc.returncode}")

    def run():
        try:
            devices = ["cuda", "cpu"] if use_gpu else ["cpu"]
            for device in devices:
                try:
                    run_once(device)
                    break
                except RuntimeError as e:
                    if device == "cpu":
                        raise
                    job["warnings"].append(f"GPU ប្រើមិនបាន ({str(e)[-120:]}) — ប្រើ CPU ជំនួស (យឺតជាង)")
            job["done"] = job["total"]
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "No module named 'faster_whisper'" in msg:
                msg = "មិនទាន់ដំឡើង Whisper"
            job.update(status="error", error=f"Whisper: {msg}")

    threading.Thread(target=run, daemon=True).start()
    return job_id


if __name__ == "__main__":  # process កូន: whisper_srt.py <media> <out.srt> <model> <lang|-> <cuda|cpu>
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    _media, _out, _model, _lang, _device = sys.argv[1:6]
    _transcribe(_media, _out, _model, "" if _lang == "-" else _lang, _device)
