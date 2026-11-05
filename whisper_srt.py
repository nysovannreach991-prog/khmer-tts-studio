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

# process កូន (python <ឯកសារនេះ>.py) លើ Python embedded (._pth) — Folder របស់ script មិនស្ថិតក្នុង sys.path
# → រកម៉ូឌុលកម្មវិធី (whisper_srt, ffmpeg_setup ...) មិនឃើញ · បន្ថែមវាដោយខ្លួនឯង
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AITeam1")
MODEL_DIR = os.path.join(DATA_DIR, "whisper")
# (ឈ្មោះ model, ស្លាក, ទំហំប្រហែល MB)
# សាកលើរឿងចិន: turbo ចាប់ការនិយាយបានច្រើនជាង large-v3 (large-v3 រំលងប្រយោគពេលមានភ្លេង) ហើយលឿនជាង 5–10 ដង
MODELS = [("small", "⚡ លឿន · small", 480), ("large-v3-turbo", "🏆 ល្អបំផុត · turbo", 1600),
          ("large-v3", "large-v3 (យឺត)", 3100)]
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


_cap = []


def gpu_compute_cap():
    """compute capability របស់កាត NVIDIA (ឧ. 8.9 = RTX 40, 12.0 = RTX 50) · 0 = មិនដឹង/គ្មាន"""
    if not _cap:
        smi = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
        try:
            out = subprocess.run([smi, "--query-gpu=compute_cap", "--format=csv,noheader"], capture_output=True,
                                 text=True, timeout=10, creationflags=_NO_WINDOW).stdout
            _cap.append(max(float(v) for v in out.split()))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            _cap.append(0.0)
    return _cap[0]


def torch_device(torch):
    """"cuda" បើ torch ដំណើរការលើកាតនេះពិតប្រាកដ — torch CUDA ខ្លះគ្មាន kernel សម្រាប់កាតថ្មី
    (ឧ. cu126 + RTX 50 → cudaErrorNoKernelImageForDevice) → ប្រើ "cpu" ជំនួសការបរាជ័យ"""
    if not torch.cuda.is_available():
        return "cpu"
    try:
        (torch.ones(8, device="cuda") * 2).sum().item()
        return "cuda"
    except Exception:  # noqa: BLE001
        return "cpu"


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


# វឌ្ឍនភាពទាញយករបស់ pip ដែលកំពុងដំណើរការ (GUI អានដើម្បីបង្ហាញ progress bar)
# {"name": ឈ្មោះ package, "done": bytes, "total": bytes} ឬ None
pip_progress = None
_PIP_FILE = re.compile(r"^Downloading (?:\S*/)?([A-Za-z0-9_.]+?)-\d\S*\.(?:whl|tar\.gz|zip)\b(?!\.metadata)")
_PIP_RAW = re.compile(r"^Progress (\d+) of (\d+)")


def pip_install(packages, on_line=None, raw_progress=True):
    """ដំឡើង package ដោយ pip — on_line(text) ទទួលបន្ទាត់វឌ្ឍនភាព · pip_progress = bytes ដែលទាញយករួច"""
    global pip_progress
    proc = subprocess.Popen([_python(), "-m", "pip", "install", "--disable-pip-version-check",
                             "--no-warn-script-location", "--progress-bar", "raw" if raw_progress else "off",
                             *packages],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=_NO_WINDOW)
    tail, name = [], ""
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").strip()
        m = _PIP_RAW.match(line)
        if m:  # "Progress 262144 of 12608406" — មិនដាក់ក្នុង tail/on_line (ច្រើនពេក)
            pip_progress = {"name": name, "done": int(m.group(1)), "total": int(m.group(2))}
            continue
        f = _PIP_FILE.match(line)
        if f:
            name = f.group(1).replace("_", "-")
            pip_progress = None
        if line:
            tail = (tail + [line])[-15:]
            if on_line:
                on_line(line)
    pip_progress = None
    if proc.wait() != 0:
        if raw_progress and any("invalid choice" in t and "raw" in t for t in tail):
            return pip_install(packages, on_line, raw_progress=False)  # pip ចាស់ (< 24.1) មិនស្គាល់ "raw"
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


def _load_audio(media):
    """ទាញសំឡេង 16 kHz mono ដោយ ffmpeg (មិនប្រើ PyAV របស់ faster-whisper — កំណែ av ខ្លះមិនត្រូវគ្នា
    ដូចជា "open() got an unexpected keyword argument 'metadata_errors'")"""
    import numpy as np
    import ffmpeg_setup
    ffmpeg_setup.add_to_path()
    if not ffmpeg_setup.available():
        try:
            ffmpeg_setup.download()  # កុំព្យូទ័រគ្មាន ffmpeg → ទាញយកឥឡូវ (~5 វិនាទី តាម GitHub)
        except Exception:  # noqa: BLE001
            return media  # ទាញយកមិនបាន → ឲ្យ faster-whisper (PyAV) អានខ្លួនឯង
    try:
        proc = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", media,
                               "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                              capture_output=True, creationflags=_NO_WINDOW)
    except OSError:
        return media
    err = proc.stderr.decode("utf-8", "replace").strip()
    no_audio = re.search(r"matches no streams|does not contain any stream", err)
    if no_audio or (proc.returncode == 0 and not proc.stdout):
        raise RuntimeError(f"វីដេអូនេះគ្មានសំឡេង (audio) — សូមពិនិត្យឯកសារ ឬទាញយកវីដេអូម្តងទៀត: "
                           f"{os.path.basename(media)}")
    if proc.returncode:
        # កុំប្តូរទៅ PyAV (វាបង្ហាញតែ "tuple index out of range") — ប្រាប់មូលហេតុពិតពី ffmpeg
        raise RuntimeError(f"ffmpeg អានវីដេអូមិនបាន (ឯកសារខូច ឬទាញយកមិនទាន់ចប់?): "
                           f"{os.path.basename(media)} — {err[-300:]}")
    return np.frombuffer(proc.stdout, np.float32).copy()


def _patch_av():
    """PyAV ថ្មី (>=15) លុប metadata_errors ចោល ប៉ុន្តែ faster-whisper នៅតែផ្ញើវា → ទម្លាក់វាចោល"""
    try:
        import av
    except ImportError:
        return
    real = av.open
    if getattr(real, "_aiteam1", False):
        return

    def open_(*args, **kwargs):
        try:
            return real(*args, **kwargs)
        except TypeError as e:
            if "metadata_errors" not in str(e):
                raise
            kwargs.pop("metadata_errors", None)
            return real(*args, **kwargs)
    open_._aiteam1 = True
    av.open = open_


# សាកលើរឿងចិនពិត: VAD ដើម (threshold 0.5, ស្ងាត់ 2វិ) រំលងពាក្យខ្លីៗ ("好了", "我送你吧") និងសំឡេងតិចៗក្រោមភ្លេង
ASR_OPTS = dict(
    vad_filter=True, vad_parameters=dict(threshold=0.25, min_silence_duration_ms=500, speech_pad_ms=300),
    condition_on_previous_text=False,  # កុំឱ្យជាប់ធ្វើដដែលៗ (hallucination) ក្នុងរឿងវែង
    no_speech_threshold=0.9, log_prob_threshold=None,  # កុំបោះចោលប្រយោគដែល model មិនសូវប្រាកដ
    compression_ratio_threshold=2.6, beam_size=5, temperature=[0.0, 0.2, 0.4])
# អក្សរដែល Whisper បង្កើតលើភ្លេង/ស្ងាត់ (មិនមែនការនិយាយពិត)
_FAKE = re.compile(r"字幕|订阅|點贊|点赞|关注|謝謝觀看|谢谢观看|独播剧场|獨播劇場|Amara|Subtitles? by|"
                   r"Thanks for watching|請不吝|请不吝|明镜", re.I)


def _fill_gaps(model, audio, found, language, prompt):
    """កន្លែងដែល VAD រសើប (0.2) ឮសំឡេងនិយាយ តែជុំទី 1 គ្មាន subtitle → ស្តាប់តែឃ្លានោះម្តងទៀត"""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    sr = 16000
    spans = [(words[0][0], words[-1][1]) for words in found]
    extra = []
    chunks = get_speech_timestamps(audio, VadOptions(threshold=0.2, min_silence_duration_ms=300, speech_pad_ms=100))
    for c in chunks:
        a, b = c["start"] / sr, c["end"] / sr
        covered = sum(max(0.0, min(b, y) - max(a, x)) for x, y in spans)
        if b - a < 0.4 or covered > 0.3 * (b - a):
            continue  # មាន subtitle រួចហើយ (ឬខ្លីពេក)
        start = max(0.0, a - 0.3)
        segs, _ = model.transcribe(audio[int(start * sr): int((b + 0.3) * sr)], language=language,
                                   word_timestamps=True, initial_prompt=prompt, vad_filter=False,
                                   condition_on_previous_text=False, beam_size=5)
        for s in segs:
            text = s.text.strip()
            if not text or s.no_speech_prob > 0.6 or s.avg_logprob < -1.0 or _FAKE.search(text):
                continue
            words = [(start + w.start, start + w.end, w.word) for w in (s.words or [])] or \
                    [(start + s.start, start + s.end, s.text)]
            if all(max(0.0, min(words[-1][1], y) - max(words[0][0], x)) < 0.3 for x, y in spans):
                extra.append(words)
    return extra


def _compute_type(device):
    """កាត GPU ចាស់ (ឧ. GTX 10xx) មិនគាំទ្រ float16 → ប្រើប្រភេទផ្សេងលើ GPU ដដែល (កុំធ្លាក់ទៅ CPU)"""
    if device != "cuda":
        return "int8"
    import ctranslate2
    ok = ctranslate2.get_supported_compute_types("cuda")
    return next((t for t in ("float16", "int8_float16", "int8_float32", "float32") if t in ok), "float32")


# GPU បរាជ័យ → មូលហេតុជាភាសាខ្មែរ (កម្មវិធីមិនប្តូរទៅ CPU ទេ ព្រោះ CPU យឺតជាង 10–20 ដង)
_GPU_HINTS = [
    (r"driver version is insufficient|CUDA driver|cudaErrorInsufficientDriver",
     "Driver NVIDIA ចាស់ពេក — សូម Update driver ពី nvidia.com/drivers (ឬ GeForce Experience / NVIDIA App) "
     "រួចបើកកម្មវិធីម្តងទៀត"),
    (r"out of memory|CUBLAS_STATUS_ALLOC_FAILED",
     "GPU អស់ VRAM — សូមបិទកម្មវិធីផ្សេងដែលប្រើ GPU (ហ្គេម, Chrome ...) រួចព្យាយាមម្តងទៀត"),
    (r"cublas|cudnn|\.dll|LoadLibrary|Could not load library",
     "ឯកសារ CUDA (cuBLAS / cuDNN) ខូច ឬខ្វះ — កម្មវិធីបានដំឡើងឡើងវិញ តែនៅតែមិនដំណើរការ"),
]


def _gpu_error(text):
    for pattern, hint in _GPU_HINTS:
        if re.search(pattern, text, re.I):
            return f"GPU ប្រើមិនបាន: {hint} ({text[-150:]})"
    return f"GPU ប្រើមិនបាន: {text[-200:]}"


def _transcribe(media, out_srt, model_name, lang, device):
    """ដំណើរការក្នុង process កូន — បោះពុម្ព JSON មួយបន្ទាត់ៗសម្រាប់វឌ្ឍនភាព"""
    for d in gpu_dll_dirs():  # cuBLAS / cuDNN ពី pip (nvidia-*-cu12)
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(d)
        except OSError:
            pass
    from faster_whisper import WhisperModel
    _patch_av()

    def say(**msg):
        print(json.dumps(msg, ensure_ascii=False), flush=True)

    say(phase="load", device=device)
    model = WhisperModel(model_name, device=device, compute_type=_compute_type(device), download_root=MODEL_DIR)
    say(phase="listen", device=device)
    audio = _load_audio(media)
    prompt = "以下是普通话的句子。" if lang == "zh" else None  # ចិនអក្សរសាមញ្ញ
    segments, info = model.transcribe(audio, language=lang or None, word_timestamps=True, **ASR_OPTS,
                                      initial_prompt=prompt)
    say(phase="listen", total=round(info.duration, 1), language=info.language)
    found = []
    for seg in segments:
        say(progress=round(seg.end, 1))
        if _FAKE.search(seg.text) and len(seg.text.strip()) < 30:
            continue
        found.append([(w.start, w.end, w.word) for w in (seg.words or [])] or [(seg.start, seg.end, seg.text)])
    if not isinstance(audio, str):  # ជុំទី 2: ស្តាប់កន្លែងដែលមានសំឡេងនិយាយ តែគ្មាន subtitle ម្តងទៀត
        found += _fill_gaps(model, audio, found, info.language, prompt)
    found.sort(key=lambda words: words[0][0])
    lines = []
    for words in found:
        lines += split_words(words)
    with open(out_srt, "w", encoding="utf-8") as f:
        for i, (a, b, text) in enumerate(lines, 1):
            b = max(b, a + 0.3)  # ពាក្យដែល timestamp ចាប់ផ្តើម = បញ្ចប់ → subtitle 0 វិនាទី (អានមិនបាន)
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
            # កុំព្យូទ័រមានកាត NVIDIA → ប្រើតែ GPU ជានិច្ច (មិនធ្លាក់ទៅ CPU ស្ងាត់ៗទេ) · គ្មាន → CPU
            # use_gpu ទុកសម្រាប់ភាពត្រូវគ្នាជាមួយកូដចាស់ (GUI ខ្លះផ្ញើ False ពេល CUDA មិនទាន់ដំឡើង)
            if not has_nvidia():
                run_once("cpu")
            else:
                if not gpu_ready():  # cuBLAS / cuDNN មិនទាន់មាន → ដំឡើងឥឡូវ (~1.5 GB ម្តងគត់)
                    job.update(status="download", done=0, total=100)
                    pip_install(GPU_PACKAGES)
                try:
                    run_once("cuda")
                except RuntimeError as e:
                    if not re.search(r"cuda|cublas|cudnn|gpu|\.dll|LoadLibrary|float16|out of memory", str(e), re.I):
                        raise  # មិនមែនបញ្ហា GPU (ឧ. វីដេអូគ្មានសំឡេង) → ប្រាប់ដដែល
                    if not re.search(_GPU_HINTS[2][0], str(e), re.I):
                        raise RuntimeError(_gpu_error(str(e))) from None
                    pip_install(["--force-reinstall", "--no-deps", *GPU_PACKAGES])  # DLL ខូច → ដំឡើងឡើងវិញម្តង
                    try:
                        run_once("cuda")
                    except RuntimeError as e2:
                        raise RuntimeError(_gpu_error(str(e2))) from None
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
