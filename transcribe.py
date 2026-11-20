"""បម្លែងសំឡេង (mp3, wav, ...) ទៅជា .srt ដោយប្រើ Gemini — រួមទាំងចាប់ភេទអ្នកនិយាយ (ស្រី/ប្រុស)។"""
import base64
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from srt_dub import jobs

MODELS = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-2.5-flash"]
FALLBACK_MODEL = "gemini-3.5-flash"
CHUNK_SEC = 300          # បំបែកសំឡេងវែងជាផ្នែក 5 នាទី — ផ្នែកខ្លី + ស្របគ្នាច្រើន = Gemini ឆ្លើយលឿនជាង
SNAP_SEC = 25            # រំកិលចំណុចកាត់ទៅចន្លោះស្ងាត់ក្បែរនោះ (± វិនាទី) — កុំកាត់ពាក់កណ្តាលប្រយោគ
WORKERS = 8              # ផ្នែកដែលផ្ញើទៅ Gemini ក្នុងពេលតែមួយ
GENDER_LABEL = {"female": "ស្រី", "male": "ប្រុស"}
# ភាសាដែលអាចបកប្រែទៅ ("" = រក្សាភាសាដើម)
LANGUAGES = {"km": "Khmer", "en": "English", "th": "Thai", "vi": "Vietnamese", "zh": "Simplified Chinese"}

_PROMPT = (
    "Transcribe this audio into subtitle segments. Each segment is one sentence or short phrase "
    "spoken by one person (split when the speaker changes, and keep segments under about 7 seconds). "
    "Give start and end time in seconds (decimals) measured from the beginning of this audio clip, "
    "the exact spoken text in its original language (do not translate), and the speaker's gender "
    "judged from the voice: female or male. Skip music and silence."
)
_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "start": {"type": "NUMBER"},
            "end": {"type": "NUMBER"},
            "text": {"type": "STRING"},
            "gender": {"type": "STRING", "enum": ["female", "male", "unknown"]},
        },
        "required": ["start", "end", "text", "gender"],
    },
}


def _run(args):
    proc = subprocess.run(args, capture_output=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        raise RuntimeError(f"{args[0]}: {proc.stderr.decode('utf-8', 'replace')[:300]}")
    return proc.stdout.decode("utf-8", "replace")


def _duration(path):
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    try:
        return float(out.strip())
    except ValueError:
        raise RuntimeError("មិនអាចអានឯកសារសំឡេងនេះបានទេ") from None


def _extract_chunk(src, start, length, dst):
    """កាត់ ហើយបង្រួមជា mono 16kHz mp3 — តូច ហើយគ្រប់គ្រាន់សម្រាប់ស្គាល់សំឡេង"""
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-t", str(length),
          "-i", src, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", dst])


def _prompt_and_schema(target):
    if not target:
        return _PROMPT, _SCHEMA
    lang = LANGUAGES[target]
    prompt = _PROMPT + (
        f" Also translate each segment into {lang} in the 'translation' field. The translation will be "
        f"read aloud as a dub, so write natural, spoken {lang} that sounds like real conversation "
        "(not word-for-word), keep names consistent, and keep it about as short as the original so it "
        "fits the same time. If the speech is already in that language, copy it as is."
    )
    schema = json.loads(json.dumps(_SCHEMA))
    schema["items"]["properties"]["translation"] = {"type": "STRING"}
    schema["items"]["required"].append("translation")
    return prompt, schema


def _transcribe_chunk(path, api_key, model, gemini_post, target):
    with open(path, "rb") as f:
        audio = base64.b64encode(f.read()).decode("ascii")
    prompt, schema = _prompt_and_schema(target)
    return _ask_json([{"inlineData": {"mimeType": "audio/mp3", "data": audio}}, {"text": prompt}],
                     schema, api_key, model, gemini_post)


def _ask_json(parts, schema, api_key, model, gemini_post):
    """ផ្ញើទៅ Gemini/Bayon ហើយអានចម្លើយ JSON — ព្យាយាមម្តងទៀតពេល server រវល់"""
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema},
    }
    models = [model] + ([FALLBACK_MODEL] if model != FALLBACK_MODEL else [])
    delay = 5
    for attempt in range(6):
        # ព្យាយាមម្តងទៀតពេល server រវល់ ហើយប្តូរទៅ model បម្រុងបន្ទាប់ពីបរាជ័យ 2 ដង
        current = models[min(attempt // 2, len(models) - 1)]
        try:
            data = gemini_post(api_key, current, body)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except (RuntimeError, OSError, KeyError, IndexError, json.JSONDecodeError) as e:
            # OSError = timeout (ជាប់គ្មានចម្លើយ) / បណ្តាញដាច់ → ព្យាយាមម្តងទៀត
            msg = str(e) or type(e).__name__
            retryable = not isinstance(e, RuntimeError) or any(c in msg for c in ("429", "500", "502", "503", "504"))
            if not retryable or attempt == 5:
                raise RuntimeError(msg if isinstance(e, RuntimeError) else f"Gemini ឆ្លើយតបមិនត្រឹមត្រូវ: {e}") from None
            time.sleep(delay)
            delay = min(delay * 2, 60)


def _chunks(src, duration):
    """[(ចាប់ផ្តើម, ប្រវែង), ...] ប្រហែល CHUNK_SEC ម្តងៗ — ចំណុចកាត់នៅចន្លោះស្ងាត់ (បើរកឃើញ)"""
    if duration <= CHUNK_SEC * 1.2:
        return [(0.0, duration)]
    gaps = []
    try:
        from video_dub import speech_segments  # silencedetect លើសំឡេងដើម
        speech = speech_segments(src, duration)
        gaps = [(a["end"] + b["start"]) / 2000 for a, b in zip(speech, speech[1:]) if b["start"] - a["end"] >= 300]
    except Exception:  # noqa: BLE001 — រកមិនឃើញ → កាត់តាមពេលវេលាធម្មតា
        pass
    cuts, t = [], 0.0
    while duration - t > CHUNK_SEC * 1.2:
        target = t + CHUNK_SEC
        near = [g for g in gaps if abs(g - target) <= SNAP_SEC]
        t = min(near, key=lambda g: abs(g - target)) if near else target
        cuts.append(t)
    bounds = [0.0] + cuts + [duration]
    return [(a, b - a) for a, b in zip(bounds, bounds[1:])]


def _fmt(sec):
    ms = int(round(max(sec, 0) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(segments, with_gender, field="text"):
    blocks = []
    for i, seg in enumerate(segments, 1):
        label = GENDER_LABEL.get(seg["gender"]) if with_gender else None
        text = f"({label}) {seg[field]}" if label else seg[field]
        blocks.append(f"{i}\n{_fmt(seg['start'])} --> {_fmt(seg['end'])}\n{text}\n")
    return "\n".join(blocks)


def _clean(segments, duration):
    segs = []
    for s in segments:
        text = " ".join(str(s.get("text", "")).split())
        translation = " ".join(str(s.get("translation", "")).split()) or text
        try:
            start, end = float(s["start"]), float(s["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if text and start < duration:
            segs.append({"start": max(start, 0.0), "end": min(max(end, start + 0.3), duration),
                         "text": text, "translation": translation, "gender": s.get("gender", "unknown")})
    segs.sort(key=lambda s: s["start"])
    for a, b in zip(segs, segs[1:]):  # កុំឱ្យជាន់គ្នា
        if a["end"] > b["start"]:
            a["end"] = max(b["start"], a["start"] + 0.1)
    return segs


def start_job(src_path, api_key, model, with_gender, target, out_dir, base_name, gemini_post, workers=WORKERS):
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"status": "running", "done": 0, "total": 1, "error": None,
                    "file": None, "original_file": None, "warnings": [], "cues": None}
    threading.Thread(
        target=_run_job,
        args=(job_id, src_path, api_key, model, with_gender, target, out_dir, base_name, gemini_post, workers),
        daemon=True,
    ).start()
    return job_id


def _run_job(job_id, src_path, api_key, model, with_gender, target, out_dir, base_name, gemini_post, workers):
    job = jobs[job_id]
    tmp_dir = tempfile.mkdtemp(prefix="stt_")
    try:
        duration = _duration(src_path)
        chunks = _chunks(src_path, duration)
        job["total"] = len(chunks)

        def work(chunk_spec):
            offset, length = chunk_spec
            chunk = os.path.join(tmp_dir, f"{int(offset * 1000)}.mp3")
            _extract_chunk(src_path, offset, length, chunk)
            segs = _clean(_transcribe_chunk(chunk, api_key, model, gemini_post, target), length)
            job["done"] += 1
            return [dict(s, start=s["start"] + offset, end=s["end"] + offset) for s in segs]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            segments = [s for part in pool.map(work, chunks) for s in part]
        if not segments:
            raise RuntimeError("រកមិនឃើញការនិយាយក្នុងសំឡេងនេះទេ")

        stamp = time.strftime('%Y%m%d_%H%M%S')
        name = f"{base_name}_{target}_{stamp}.srt" if target else f"{base_name}_{stamp}.srt"
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(build_srt(segments, with_gender, "translation" if target else "text"))
        if target:  # រក្សាទុក SRT ភាសាដើមផងដែរ
            job["original_file"] = f"{base_name}_orig_{stamp}.srt"
            with open(os.path.join(out_dir, job["original_file"]), "w", encoding="utf-8") as f:
                f.write(build_srt(segments, with_gender))

        job["cues"] = [{"index": i, "start": int(s["start"] * 1000), "end": int(s["end"] * 1000),
                        "text": s["translation"] if target else s["text"], "original": s["text"],
                        "gender": s["gender"] if with_gender else None}
                       for i, s in enumerate(segments, 1)]
        job["file"] = name
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        try:
            os.remove(src_path)
        except OSError:
            pass


# ---------------- បកប្រែ SRT (អត្ថបទ) — សម្រាប់ SenseVoice / Paraformer / Whisper ----------------
TRANSLATE_BATCH = 50   # បន្ទាត់ក្នុងមួយសំណើ — តិច = ឆ្លើយលឿន ហើយមិនរំលងបន្ទាត់
_LINES_SCHEMA = {"type": "OBJECT", "properties": {"lines": {"type": "ARRAY", "items": {"type": "STRING"}}},
                 "required": ["lines"]}


def translate_lines(lines, target, api_key, model, gemini_post, on_batch=None, workers=WORKERS):
    """បកប្រែបន្ទាត់ subtitle (ស្របគ្នាច្រើនដុំ) → បញ្ជីដែលមានចំនួនដូចគ្នា (ដុំដែលខុសចំនួន → ព្យាយាមម្តងទៀត)"""
    lang = LANGUAGES[target]
    batches = [lines[i:i + TRANSLATE_BATCH] for i in range(0, len(lines), TRANSLATE_BATCH)]

    def work(index):
        batch = batches[index]
        before = batches[index - 1][-3:] if index else []  # បរិបទ (មិនបកប្រែ) — ឱ្យសន្ទនាជាប់គ្នា
        prompt = (
            f"Translate these {len(batch)} subtitle lines from a drama into {lang}. The translation will be read "
            f"aloud as a dub: write natural, spoken {lang} like real conversation (not word-for-word), keep names "
            "consistent, and keep each line about as short as the original so it fits the same time. "
            f"Return exactly {len(batch)} strings in 'lines', in the same order, one per input line — never merge, "
            "split, skip or number lines."
            + (f"\nPrevious lines (context only, do not translate): {json.dumps(before, ensure_ascii=False)}"
               if before else "")
            + f"\nLines: {json.dumps(batch, ensure_ascii=False)}")
        out = []
        for _ in range(3):
            data = _ask_json([{"text": prompt}], _LINES_SCHEMA, api_key, model, gemini_post)
            out = data.get("lines", []) if isinstance(data, dict) else data
            out = [" ".join(str(x).split()) for x in out] if isinstance(out, list) else []
            if len(out) == len(batch):
                break
        if len(out) != len(batch):  # នៅតែខុស → ប្រើបន្ទាត់ដើមសម្រាប់ផ្នែកដែលខ្វះ
            out = (out + batch[len(out):])[:len(batch)]
        if on_batch:
            on_batch()
        return [t or o for t, o in zip(out, batch)]

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(batches)))) as pool:
        return [t for part in pool.map(work, range(len(batches))) for t in part]


def start_translate_job(src_srt, out_dir, base_name, target, api_key, model, gemini_post):
    """SRT ភាសាដើម → <base>.srt (បកប្រែ) + <base>_orig.srt · job ដូច start_job (file / original_file / lines)"""
    from srt_dub import parse_srt
    job_id = uuid.uuid4().hex[:12]
    job = jobs[job_id] = {"status": "running", "done": 0, "total": 1, "error": None, "warnings": [],
                          "file": None, "original_file": None, "lines": 0}

    def run():
        try:
            with open(src_srt, encoding="utf-8-sig", errors="replace") as f:
                cues = parse_srt(f.read())
            if not cues:
                raise RuntimeError("SRT គ្មាន subtitle")
            job["total"] = -(-len(cues) // TRANSLATE_BATCH)
            texts = translate_lines([c["text"] for c in cues], target, api_key, model, gemini_post,
                                    on_batch=lambda: job.__setitem__("done", job["done"] + 1))
            segs = [{"start": c["start"] / 1000, "end": c["end"] / 1000, "text": c["text"], "translation": t,
                     "gender": "unknown"} for c, t in zip(cues, texts)]
            job["file"], job["original_file"] = f"{base_name}.srt", f"{base_name}_orig.srt"
            with open(os.path.join(out_dir, job["file"]), "w", encoding="utf-8") as f:
                f.write(build_srt(segs, False, "translation"))
            with open(os.path.join(out_dir, job["original_file"]), "w", encoding="utf-8") as f:
                f.write(build_srt(segs, False))
            job.update(lines=len(segs), status="done")
        except Exception as e:  # noqa: BLE001
            job.update(status="error", error=f"បកប្រែ: {e}")

    threading.Thread(target=run, daemon=True).start()
    return job_id
