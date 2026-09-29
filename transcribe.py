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
CHUNK_SEC = 600          # បំបែកសំឡេងវែងជាផ្នែក 10 នាទី
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
    body = {
        "contents": [{"parts": [{"inlineData": {"mimeType": "audio/mp3", "data": audio}},
                                {"text": prompt}]}],
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
        except (RuntimeError, KeyError, IndexError, json.JSONDecodeError) as e:
            msg = str(e)
            retryable = not isinstance(e, RuntimeError) or any(c in msg for c in ("429", "500", "503"))
            if not retryable or attempt == 5:
                raise RuntimeError(msg if isinstance(e, RuntimeError) else f"Gemini ឆ្លើយតបមិនត្រឹមត្រូវ: {e}") from None
            time.sleep(delay)
            delay = min(delay * 2, 60)


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


def start_job(src_path, api_key, model, with_gender, target, out_dir, base_name, gemini_post, workers=3):
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
        starts = [float(s) for s in range(0, int(duration) + 1, CHUNK_SEC) if s < duration]
        job["total"] = len(starts)

        def work(offset):
            chunk = os.path.join(tmp_dir, f"{int(offset)}.mp3")
            _extract_chunk(src_path, offset, CHUNK_SEC, chunk)
            segs = _clean(_transcribe_chunk(chunk, api_key, model, gemini_post, target),
                          min(CHUNK_SEC, duration - offset))
            job["done"] += 1
            return [dict(s, start=s["start"] + offset, end=s["end"] + offset) for s in segs]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            segments = [s for part in pool.map(work, starts) for s in part]
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
