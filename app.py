"""កម្មវិធីបម្លែងអត្ថបទទៅជាសំឡេង (TTS) — edge-tts និង Gemini TTS"""
import asyncio
import base64
import hashlib
import io
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave

import aiohttp
import aiohttp.abc
import edge_tts
from flask import Flask, jsonify, render_template, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # Python ឯកជន (embeddable) មិនបន្ថែមថតកម្មវិធីខ្លួនឯង

import srt_dub
import transcribe
import video_dub
import video_merge

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCAL = os.environ.get("LOCALAPPDATA", "")
# ដំឡើងដោយ Setup (.exe) → កម្មវិធីនៅក្នុង AppData (មើលមិនឃើញ) → លទ្ធផលទៅ Videos\AI Team 1 ដែលងាយរក
INSTALLED = bool(_LOCAL) and os.path.normcase(BASE_DIR).startswith(os.path.normcase(os.path.join(_LOCAL, "AITeam1")))
if INSTALLED:
    _home = os.path.expanduser("~")
    _videos = next((d for d in (os.path.join(_home, "Videos"), os.path.join(_home, "OneDrive", "Videos"),
                                os.path.join(_home, "Documents")) if os.path.isdir(d)), _home)
    OUTPUT_DIR = os.path.join(_videos, "AI Team 1")
else:
    OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODELS = [
    "gemini-3.8-flash-tts",
    "gemini-3.8-flash-lite-tts",
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-pro-preview-tts",
    "gemini-2.5-flash-preview-tts",
]
_GEMINI_FEMALE = [
    "Kore", "Zephyr", "Leda", "Aoede", "Callirrhoe", "Autonoe", "Despina", "Erinome",
    "Laomedeia", "Achernar", "Gacrux", "Pulcherrima", "Vindemiatrix", "Sulafat",
]
_GEMINI_MALE = [
    "Puck", "Charon", "Fenrir", "Orus", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Algenib", "Rasalgethi", "Alnilam", "Schedar", "Achird", "Zubenelgenubi", "Sadachbia", "Sadaltager",
]
GEMINI_VOICES = [{"name": v, "gender": "Female"} for v in _GEMINI_FEMALE] + \
                [{"name": v, "gender": "Male"} for v in _GEMINI_MALE]
GEMINI_CHUNK_CHARS = 3000

EDGE_CHUNK_CHARS = 800      # ទំហំផ្នែកនីមួយៗសម្រាប់បង្កើតស្របគ្នា
EDGE_CONCURRENCY = 16       # ចំនួនការភ្ជាប់ទៅ Edge ក្នុងពេលតែមួយ
EDGE_RETRIES = 5          # រង់ចាំ 1+2+4+8 វិនាទី ពេលបណ្តាញដាច់មួយភ្លែត
EDGE_CACHE_DIR = (os.path.join(_LOCAL, "AITeam1", "cache", "edge") if INSTALLED
                  else os.path.join(OUTPUT_DIR, ".cache", "edge"))
os.makedirs(EDGE_CACHE_DIR, exist_ok=True)

app = Flask(__name__)
_edge_voices_cache = None


def _out_name(engine, ext):
    return f"{engine}_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.{ext}"


# ---------------- Edge TTS ----------------

def _signed(value, unit):
    value = int(value)
    return f"{'+' if value >= 0 else ''}{value}{unit}"


class _CachingResolver(aiohttp.abc.AbstractResolver):
    """ចងចាំ DNS — ការភ្ជាប់ស្របគ្នាច្រើនមិនចាំបាច់រក DNS ម្តងទៀតទេ ហើយប្រើលទ្ធផលចាស់ពេល DNS ខូច។"""
    TTL = 600
    _cache = {}
    _lock = threading.Lock()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        key = (host, port, family)
        with self._lock:
            hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.TTL:
            return hit[1]
        try:
            addrs = await aiohttp.ThreadedResolver().resolve(host, port, family)
        except OSError:
            if hit:
                return hit[1]
            raise
        with self._lock:
            self._cache[key] = (time.time(), addrs)
        return addrs

    async def close(self):
        pass


_dns_resolver = _CachingResolver()


def _edge_cache_path(text, voice, rate, pitch, volume):
    key = hashlib.sha1(f"{voice}|{rate}|{pitch}|{volume}|{text}".encode("utf-8")).hexdigest()
    return os.path.join(EDGE_CACHE_DIR, key + ".mp3")


async def edge_audio_async(text, voice, rate=0, pitch=0, volume=0):
    """ត្រឡប់ MP3 bytes — ប្រើ cache ប្រសិនបើធ្លាប់បង្កើតរួច ហើយព្យាយាមម្តងទៀតពេលបណ្តាញខូច។"""
    cache = _edge_cache_path(text, voice, rate, pitch, volume)
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            return f.read()

    audio = bytearray()
    for attempt in range(EDGE_RETRIES):
        try:
            communicate = edge_tts.Communicate(
                text, voice,
                rate=_signed(rate, "%"), pitch=_signed(pitch, "Hz"), volume=_signed(volume, "%"),
                connector=aiohttp.TCPConnector(resolver=_dns_resolver),
            )
            audio = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio.extend(chunk.get("data", b""))
            if not audio:
                raise RuntimeError("Edge TTS មិនបានផ្ញើសំឡេងមកវិញ")
            break
        except Exception as e:
            if attempt == EDGE_RETRIES - 1:
                raise RuntimeError(f"Edge TTS: {e}") from None
            await asyncio.sleep(2 ** attempt)

    tmp = f"{cache}.{uuid.uuid4().hex}.tmp"
    with open(tmp, "wb") as f:
        f.write(audio)
    os.replace(tmp, cache)
    return bytes(audio)


def edge_audio(text, voice, rate=0, pitch=0, volume=0):
    return asyncio.run(edge_audio_async(text, voice, rate, pitch, volume))


async def _edge_parallel(chunks, voice, rate, pitch, volume):
    sem = asyncio.Semaphore(EDGE_CONCURRENCY)

    async def one(chunk):
        async with sem:
            return await edge_audio_async(chunk, voice, rate, pitch, volume)

    return await asyncio.gather(*(one(c) for c in chunks))


def edge_tts_generate(text, voice, rate=0, pitch=0, volume=0):
    # បំបែកអត្ថបទវែងជាផ្នែកតូចៗ ហើយបង្កើតស្របគ្នា — MP3 frames អាចភ្ជាប់គ្នាដោយផ្ទាល់
    chunks = split_text(text, EDGE_CHUNK_CHARS)
    parts = asyncio.run(_edge_parallel(chunks, voice, rate, pitch, volume))
    name = _out_name("edge", "mp3")
    with open(os.path.join(OUTPUT_DIR, name), "wb") as f:
        for part in parts:
            f.write(part)
    return name


def get_edge_voices():
    global _edge_voices_cache
    if _edge_voices_cache is None:
        voices = asyncio.run(edge_tts.list_voices())
        voices.sort(key=lambda v: (not v["Locale"].startswith("km-"), v["Locale"], v["ShortName"]))
        _edge_voices_cache = [
            {"name": v["ShortName"], "locale": v["Locale"], "gender": v["Gender"]} for v in voices
        ]
    return _edge_voices_cache


# ---------------- Gemini TTS ----------------

def split_text(text, limit=GEMINI_CHUNK_CHARS):
    """បំបែកអត្ថបទវែងជាផ្នែកៗ តាមកថាខណ្ឌ/ប្រយោគ ដើម្បីកុំឱ្យលើសដែនកំណត់។"""
    chunks, current = [], ""
    for para in re.split(r"\n\s*\n", text.strip()):
        pieces = [para] if len(para) <= limit else re.split(r"(?<=[។៕!?.])\s*", para)
        for piece in pieces:
            while len(piece) > limit:
                chunks.append(piece[:limit])
                piece = piece[limit:]
            if len(current) + len(piece) + 2 > limit and current:
                chunks.append(current)
                current = ""
            current = f"{current}\n\n{piece}" if current else piece
    if current.strip():
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def gemini_post(api_key, model, body):
    """ហៅ Gemini generateContent ហើយត្រឡប់ JSON response"""
    req = urllib.request.Request(
        GEMINI_URL.format(model=model),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except Exception:
            pass
        raise RuntimeError(f"Gemini API error {e.code}: {detail}") from None
    return data


def _gemini_call(api_key, model, voice, text, style):
    prompt = f"{style.strip()}:\n{text}" if style.strip() else text
    data = gemini_post(api_key, model, {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    })
    try:
        part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Gemini មិនបានផ្ញើសំឡេងមកវិញ: {json.dumps(data)[:500]}") from None
    rate_match = re.search(r"rate=(\d+)", part.get("mimeType", ""))
    return base64.b64decode(part["data"]), int(rate_match.group(1)) if rate_match else 24000


def gemini_tts_generate(text, api_key, model, voice, style=""):
    if not api_key:
        raise RuntimeError("សូមបញ្ចូល Gemini API Key (ឬកំណត់ GEMINI_API_KEY)")
    pcm, sample_rate = io.BytesIO(), 24000
    for chunk in split_text(text):
        audio, sample_rate = _gemini_call(api_key, model, voice, chunk, style)
        pcm.write(audio)

    name = _out_name("gemini", "wav")
    with wave.open(os.path.join(OUTPUT_DIR, name), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.getvalue())
    return name


# ---------------- Text cleanup ----------------

_PAREN_RE = re.compile(r"\([^()]*\)|（[^（）]*）")


def strip_parens(text):
    """លុបអត្ថបទក្នុងវង់ក្រចក (ឈ្មោះតួអង្គ) — ឧ. "(សុខា): សួស្តី" → "សួស្តី" """
    prev = None
    while prev != text:  # វង់ក្រចកជាន់គ្នា
        prev, text = text, _PAREN_RE.sub(" ", text)
    lines = [re.sub(r"^[\s:：៖\-–—]+", "", line) for line in text.split("\n")]
    return "\n".join(re.sub(r"[ \t]{2,}", " ", line).strip() for line in lines).strip()


# ---------------- Routes ----------------

@app.route("/")
def index():
    return render_template(
        "index.html",
        gemini_voices=GEMINI_VOICES,
        gemini_models=GEMINI_MODELS,
        stt_models=transcribe.MODELS,
        has_env_key=bool(os.environ.get("GEMINI_API_KEY")),
    )


@app.route("/api/edge-voices")
def edge_voices():
    try:
        return jsonify(get_edge_voices())
    except Exception as e:
        return jsonify({"error": f"មិនអាចទាញយកបញ្ជីសំឡេង: {e}"}), 500


@app.route("/api/tts", methods=["POST"])
def tts():
    p = request.get_json(force=True)
    text = (p.get("text") or "").strip()
    if p.get("strip_parens", True):
        text = strip_parens(text)
    if not text:
        return jsonify({"error": "សូមបញ្ចូលអត្ថបទ"}), 400
    try:
        if p.get("engine") == "gemini":
            name = gemini_tts_generate(
                text,
                api_key=(p.get("api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", ""),
                model=p.get("model") or GEMINI_MODELS[0],
                voice=p.get("voice") or "Kore",
                style=p.get("style") or "",
            )
        else:
            name = edge_tts_generate(
                text,
                voice=p.get("voice") or "km-KH-SreymomNeural",
                rate=p.get("rate", 0), pitch=p.get("pitch", 0), volume=p.get("volume", 0),
            )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"file": name, "url": f"/outputs/{name}"})


@app.route("/api/srt/parse", methods=["POST"])
def srt_parse():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "សូមជ្រើសរើសឯកសារ .srt"}), 400
    cues = srt_dub.parse_srt(f.read().decode("utf-8-sig", "replace"))
    if not cues:
        return jsonify({"error": "មិនមាន subtitle ក្នុងឯកសារនេះទេ"}), 400
    return jsonify({"cues": cues})


def safe_name(name):
    """ឈ្មោះឯកសារសុវត្ថិភាព — រក្សាអក្សរខ្មែរ (រួមទាំងស្រៈ) ហើយប្តូរតែតួអក្សរដែល Windows មិនអនុញ្ញាត"""
    return re.sub(r'[\\/:*?"<>|\s.]+', "_", name).strip("_")


class BadRequest(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def _dub_setup(p):
    """អានការកំណត់ពី request ហើយត្រឡប់ arguments សម្រាប់ srt_dub.start_job"""
    cues = p.get("cues") or []
    if p.get("strip_parens", True):
        cues = [dict(c, text=strip_parens(c["text"])) for c in cues]
        cues = [c for c in cues if c["text"]]
    if not cues:
        raise BadRequest("មិនមាន subtitle")
    if not shutil.which("ffmpeg"):
        raise BadRequest("រកមិនឃើញ ffmpeg — សូមដំឡើង ffmpeg ហើយដាក់ក្នុង PATH", 500)

    engine = "gemini" if p.get("engine") == "gemini" else "edge"
    if engine == "gemini":
        opts = {
            "api_key": (p.get("api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", ""),
            "model": p.get("model") or GEMINI_MODELS[0],
            "voice": p.get("voice") or "Kore",
            "style": p.get("style") or "",
        }
        if not opts["api_key"]:
            raise BadRequest("សូមបញ្ចូល Gemini API Key (ឬកំណត់ GEMINI_API_KEY)")
    else:
        opts = {
            "voice": p.get("voice") or "km-KH-SreymomNeural",
            "rate": p.get("rate", 0), "pitch": p.get("pitch", 0), "volume": p.get("volume", 0),
        }

    if p.get("auto_gender"):
        # ជ្រើសសំឡេងតាមភេទរបស់បន្ទាត់នីមួយៗ (បន្ទាត់មិនស្គាល់ភេទ ប្រើសំឡេងចម្បង)
        by_gender = {"female": p.get("voice_female"), "male": p.get("voice_male")}
        cues = [dict(c, voice=by_gender.get(c.get("gender") or "") or None) for c in cues]

    return dict(
        cues=cues, engine=engine, opts=opts, out_dir=OUTPUT_DIR,
        fmt="mp3" if p.get("format") == "mp3" else "wav",
        fit=bool(p.get("fit", True)),
        max_speed=min(max(float(p.get("max_speed", 1.5)), 1.0), 3.0),
        # Edge ទ្រាំបាន 32–48 ស្របគ្នា (រង់ចាំបណ្តាញ) · Gemini មានកម្រិត rate limit → មិនលើស 16
        workers=min(max(int(p.get("workers", 32)), 1), 16 if engine == "gemini" else 64),
        edge_audio=edge_audio, gemini_call=_gemini_call,
    )


@app.route("/api/srt", methods=["POST"])
def srt_start():
    try:
        args = _dub_setup(request.get_json(force=True))
    except BadRequest as e:
        return jsonify({"error": str(e)}), e.code
    return jsonify({"job": srt_dub.start_job(**args)})


@app.route("/api/video-dub", methods=["POST"])
def video_dub_start():
    """SRT + វីដេអូ → សំឡេង TTS → ដាក់ចូលវីដេអូ → កាត់ជាផ្នែកៗ"""
    f = request.files.get("video")
    if not f or not f.filename:
        return jsonify({"error": "សូមជ្រើសរើសវីដេអូ"}), 400
    try:
        p = json.loads(request.form.get("payload") or "{}")
        args = _dub_setup(p)
    except (BadRequest, json.JSONDecodeError) as e:
        return jsonify({"error": str(e)}), getattr(e, "code", 400)

    base, ext = os.path.splitext(os.path.basename(f.filename))
    fd, video = tempfile.mkstemp(suffix=ext or ".mp4")
    os.close(fd)
    f.save(video)
    try:
        video_dub.probe(video)
    except Exception as e:
        os.remove(video)
        return jsonify({"error": f"មិនអាចអានវីដេអូនេះបានទេ: {e}"}), 400

    safe = safe_name(base) or "video"
    name_base = f"{safe}_dub_{time.strftime('%Y%m%d_%H%M%S')}"
    orig_volume = min(max(float(p.get("orig_volume", 0.15)), 0.0), 1.0)
    part_sec = max(float(p.get("part_minutes", 10)), 0) * 60

    def post(job, pcm):
        job["folder"], job["parts"] = video_dub.mix_and_split(
            video, pcm, args["cues"], OUTPUT_DIR, name_base, orig_volume, part_sec, job)

    def cleanup():
        try:
            os.remove(video)
        except OSError:
            pass

    return jsonify({"job": srt_dub.start_job(**args, post=post, on_finish=cleanup)})


@app.route("/api/transcribe", methods=["POST"])
def transcribe_start():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "សូមជ្រើសរើសឯកសារសំឡេង"}), 400
    api_key = (request.form.get("api_key") or "").strip() or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return jsonify({"error": "សូមបញ្ចូល Gemini API Key (ឬកំណត់ GEMINI_API_KEY)"}), 400
    if not shutil.which("ffmpeg"):
        return jsonify({"error": "រកមិនឃើញ ffmpeg — សូមដំឡើង ffmpeg ហើយដាក់ក្នុង PATH"}), 500

    base, ext = os.path.splitext(os.path.basename(f.filename))
    fd, src = tempfile.mkstemp(suffix=ext or ".bin")
    os.close(fd)
    f.save(src)
    base = safe_name(base) or "audio"
    model = request.form.get("model") or transcribe.MODELS[0]
    job_id = transcribe.start_job(
        src, api_key, model,
        with_gender=request.form.get("gender", "1") == "1",
        target=request.form.get("target", "") if request.form.get("target") in transcribe.LANGUAGES else "",
        out_dir=OUTPUT_DIR, base_name=base, gemini_post=gemini_post,
    )
    return jsonify({"job": job_id})


def _merge_base_name(filename):
    base = os.path.splitext(os.path.basename(filename))[0]
    base = re.sub(r"[_\-\s]*(part|ភាគ|ep)?[_\-\s]*\d+$", "", base, flags=re.I)  # លុប "_part01" ពីចុង
    if not base:  # ឈ្មោះគ្រាន់តែ "part01" → ប្រើឈ្មោះថត (ឧ. "រឿង_dub_..._parts")
        base = re.sub(r"_parts$", "", os.path.basename(os.path.dirname(filename)))
    return safe_name(base) or "video"


def _video_inputs(min_count):
    """វីដេអូពី upload (form "videos") ឬពីឯកសារក្នុង outputs/ (JSON {paths}).
    ត្រឡប់ (paths, names, temp_files, options)"""
    if not shutil.which("ffmpeg"):
        raise BadRequest("រកមិនឃើញ ffmpeg — សូមដំឡើង ffmpeg ហើយដាក់ក្នុង PATH", 500)
    temp_files = []
    if request.files:
        uploads = request.files.getlist("videos")
        for f in uploads:
            fd, path = tempfile.mkstemp(suffix=os.path.splitext(f.filename)[1] or ".mp4")
            os.close(fd)
            f.save(path)
            temp_files.append(path)
        paths, names, opts = temp_files, [f.filename for f in uploads], request.form
    else:
        opts = request.get_json(force=True)
        root = os.path.realpath(OUTPUT_DIR)
        paths = [os.path.realpath(os.path.join(OUTPUT_DIR, x)) for x in opts.get("paths") or []]
        if any(os.path.commonpath([root, x]) != root or not os.path.isfile(x) for x in paths):
            raise BadRequest("រកមិនឃើញឯកសារ")
        names = paths
    if len(paths) < min_count:
        for f in temp_files:
            os.remove(f)
        raise BadRequest("សូមជ្រើសរើសវីដេអូយ៉ាងតិច 2" if min_count > 1 else "សូមជ្រើសរើសវីដេអូ")
    return paths, names, temp_files, opts


@app.route("/api/merge", methods=["POST"])
def merge_start():
    """បញ្ចូលវីដេអូច្រើនទៅជាមួយ — ពីឯកសារដែល upload ឬពីឯកសារក្នុង outputs/ (JSON {paths})"""
    try:
        paths, names, temp_files, opts = _video_inputs(2)
    except BadRequest as e:
        return jsonify({"error": str(e)}), e.code
    force = str(opts.get("reencode")).lower() in ("1", "true")
    job_id = video_merge.start_job(paths, OUTPUT_DIR, _merge_base_name(names[0]), force, temp_files)
    return jsonify({"job": job_id})


@app.route("/api/mute", methods=["POST"])
def mute_start():
    """លុបសំឡេងចេញពីវីដេអូ (មួយ ឬច្រើន) — ចម្លងវីដេអូផ្ទាល់ មិន encode ឡើងវិញ"""
    try:
        paths, names, temp_files, _ = _video_inputs(1)
    except BadRequest as e:
        return jsonify({"error": str(e)}), e.code
    bases = [safe_name(os.path.splitext(os.path.basename(n))[0]) or "video" for n in names]
    return jsonify({"job": video_merge.start_mute_job(paths, bases, OUTPUT_DIR, temp_files)})


_MEDIA_KIND = {".mp4": "video", ".mkv": "video", ".mp3": "audio", ".wav": "audio", ".srt": "srt"}


@app.route("/api/history")
def history():
    """ឯកសារទាំងអស់ក្នុង outputs/ (ថ្មីបំផុតមុន) — ដើម្បីកុំឱ្យលទ្ធផលបាត់ពេល reload"""
    items = []
    for entry in os.scandir(OUTPUT_DIR):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():  # ថតវីដេអូដែលបានកាត់ជាផ្នែកៗ
            files = [f for f in os.scandir(entry.path) if f.is_file() and f.name.endswith(".mp4")]
            if not files:
                continue
            items.append({
                "kind": "parts", "name": entry.name, "mtime": max(f.stat().st_mtime for f in files),
                "parts": [{"name": f.name, "path": f"{entry.name}/{f.name}", "size": f.stat().st_size}
                          for f in sorted(files, key=lambda f: f.name)],
            })
        elif os.path.splitext(entry.name)[1].lower() in _MEDIA_KIND:
            st = entry.stat()
            items.append({"kind": _MEDIA_KIND[os.path.splitext(entry.name)[1].lower()], "name": entry.name,
                          "path": entry.name, "size": st.st_size, "mtime": st.st_mtime})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return jsonify(items[:int(request.args.get("limit", 100))])


@app.route("/api/jobs/<job_id>")
def job_status(job_id):
    job = srt_dub.jobs.get(job_id)
    if not job:
        return jsonify({"error": "រកមិនឃើញការងារ"}), 404
    data = dict(job)
    if job["file"]:
        data["url"] = f"/outputs/{job['file']}"
    if job.get("original_file"):
        data["original_url"] = f"/outputs/{job['original_file']}"
    return jsonify(data)


@app.route("/outputs/<path:name>")
def outputs(name):
    return send_from_directory(OUTPUT_DIR, name, as_attachment=request.args.get("dl") == "1")


if __name__ == "__main__":
    import ffmpeg_setup
    ffmpeg_setup.add_to_path()
    print("បើកកម្មវិធីនៅ http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
