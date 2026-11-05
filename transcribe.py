"""បម្លែងសំឡេង (mp3, wav, ...) ទៅជា .srt ដោយប្រើ Gemini — រួមទាំងចាប់ភេទអ្នកនិយាយ (ស្រី/ប្រុស)។"""
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from srt_dub import jobs

MODELS = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-2.5-flash"]
FALLBACK_MODEL = "gemini-3.8-flash"  # ពេល model ដែលជ្រើសបរាជ័យច្រើនដង → 3.8 Flash (មិនធ្លាក់ទៅ model ចាស់)
CHUNK_SEC = 300          # បំបែកសំឡេងវែងជាផ្នែក 5 នាទី — ផ្នែកខ្លី + ស្របគ្នាច្រើន = Gemini ឆ្លើយលឿនជាង
SNAP_SEC = 25            # រំកិលចំណុចកាត់ទៅចន្លោះស្ងាត់ក្បែរនោះ (± វិនាទី) — កុំកាត់ពាក់កណ្តាលប្រយោគ
WORKERS = 8              # ផ្នែកដែលផ្ញើទៅ Gemini ក្នុងពេលតែមួយ
GENDER_LABEL = {"female": "ស្រី", "male": "ប្រុស"}
# អក្សរនៃភាសាគោលដៅ — ចម្លើយ "បកប្រែ" ដែលគ្មានអក្សរនេះ (ឧ. server ខ្លះប្រគល់ភាសាដើមវិញ) = មិនបានបកប្រែ
_SCRIPT = {"km": re.compile(r"[ក-៿]"), "th": re.compile(r"[฀-๿]"),
           "zh": re.compile(r"[㐀-鿿]")}


def translated(text, target):
    """True = text ជាភាសា target ពិត (ឬភាសាដែលមិនអាចពិនិត្យតាមអក្សរ)"""
    rx = _SCRIPT.get(target)
    return bool(text) and (rx is None or bool(rx.search(text)))
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


class BadJSON(RuntimeError):
    """ចម្លើយមិនមែន JSON ត្រឹមត្រូវ ទោះកែរួច (ចម្លើយវែងពេកត្រូវកាត់ ឬ server មិនគោរពទម្រង់) — អ្នកហៅបំបែកសំណើឱ្យតូចជាង"""


def _brackets(t):
    """វង់ក្រចកដែលនៅបើក (តាមលំដាប់) និងថាតើចុងអត្ថបទនៅក្នុង string ឬអត់"""
    stack, in_str, esc = [], False, False
    for ch in t:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append("]" if ch == "[" else "}")
        elif ch in "]}" and stack:
            stack.pop()
    return stack, in_str


def _escape_inner_quotes(t):
    """" ក្នុង string ដែលមិនបានដាក់ \\ (ឧ. "he said "hi"") → បើក្រោយ " មិនមែន , } ] : = មិនមែនចុង string"""
    out, in_str, esc, n = [], False, False, len(t)
    for i, ch in enumerate(t):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                j = i + 1
                while j < n and t[j] in " \t\r\n":
                    j += 1
                if j >= n or t[j] in ",}]:":
                    in_str = False
                else:
                    out.append("\\")
        elif ch == '"':
            in_str = True
        out.append(ch)
    return "".join(out)


def repair_json(text):
    """JSON ដែលខូចបន្តិច → object · ValueError បើកែមិនបាន
    ដោះស្រាយ: ```json``` · អត្ថបទមុន/ក្រោយ · សញ្ញាក្បៀសចុង · " ក្នុង string · ចម្លើយកាត់ពាក់កណ្តាល (បិទវង់ក្រចក)"""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    starts = [i for i in (t.find("["), t.find("{")) if i >= 0]
    if not starts:
        raise ValueError("no JSON")
    t = t[min(starts):]
    cands = [re.sub(r",\s*([}\]])", r"\1", x) for x in (t, _escape_inner_quotes(t))]
    for c in cands:  # ទាំងមូលសិន (ក្បៀសចុង / " ក្នុង string / វង់ក្រចកមិនបិទ)
        stack, in_str = _brackets(c)
        if not in_str:
            try:
                return json.loads(c + "".join(reversed(stack)), strict=False)
            except ValueError:
                pass
    for c in cands:
        tried = 0  # កាត់ចោលពាក់កណ្តាល → ថយក្រោយដល់តម្លៃចុងក្រោយដែលពេញ រួចបិទវង់ក្រចក
        for i in range(len(c) - 1, 0, -1):
            if c[i] not in '}]"':
                continue
            tried += 1
            if tried > 400:
                break
            head = c[:i + 1]
            stack, in_str = _brackets(head)
            if in_str:
                continue
            head = re.sub(r",\s*$", "", head)
            try:
                return json.loads(head + "".join(reversed(stack)), strict=False)
            except ValueError:
                continue
    raise ValueError("cannot repair")


def _ask_json(parts, schema, api_key, model, gemini_post):
    """ផ្ញើទៅ Gemini/Bayon ហើយអានចម្លើយ JSON — ព្យាយាមម្តងទៀតពេល server រវល់ · JSON ខូច → កែ · នៅតែខូច 2 ដង → BadJSON"""
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema},
    }
    models = [model] + ([FALLBACK_MODEL] if model != FALLBACK_MODEL else [])
    delay, bad = 5, 0
    for attempt in range(6):
        # ព្យាយាមម្តងទៀតពេល server រវល់ ហើយប្តូរទៅ model បម្រុងបន្ទាប់ពីបរាជ័យ 2 ដង
        current = models[min(attempt // 2, len(models) - 1)]
        try:
            data = gemini_post(api_key, current, body)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (RuntimeError, OSError, KeyError, IndexError) as e:
            # OSError = timeout (ជាប់គ្មានចម្លើយ) / បណ្តាញដាច់ → ព្យាយាមម្តងទៀត
            msg = str(e) or type(e).__name__
            retryable = not isinstance(e, RuntimeError) or any(c in msg for c in ("429", "500", "502", "503", "504"))
            if not retryable or attempt == 5:
                raise RuntimeError(msg if isinstance(e, RuntimeError) else f"Gemini ឆ្លើយតបមិនត្រឹមត្រូវ: {e}") from None
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        try:
            return json.loads(text)
        except ValueError:
            pass
        try:
            return repair_json(str(text))
        except ValueError:
            bad += 1
            if bad >= 2 or attempt == 5:
                raise BadJSON("ចម្លើយ Gemini មិនមែន JSON ត្រឹមត្រូវ (ចម្លើយវែងពេក ឬទម្រង់ខុស)") from None
            time.sleep(delay)


def _chunks(src, duration, chunk_sec=CHUNK_SEC):
    """[(ចាប់ផ្តើម, ប្រវែង), ...] ប្រហែល chunk_sec ម្តងៗ — ចំណុចកាត់នៅចន្លោះស្ងាត់ (បើរកឃើញ)"""
    if duration <= chunk_sec * 1.2:
        return [(0.0, duration)]
    gaps = []
    try:
        from video_dub import speech_segments  # silencedetect លើសំឡេងដើម
        speech = speech_segments(src, duration)
        gaps = [(a["end"] + b["start"]) / 2000 for a, b in zip(speech, speech[1:]) if b["start"] - a["end"] >= 300]
    except Exception:  # noqa: BLE001 — រកមិនឃើញ → កាត់តាមពេលវេលាធម្មតា
        pass
    cuts, t = [], 0.0
    while duration - t > chunk_sec * 1.2:
        target = t + chunk_sec
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


# ---------------- Whisper SRT + Gemini (ស្តាប់សំឡេង) → ភេទ ស្រី/ប្រុស + កែពាក្យ + បកប្រែ ----------------
# សាកលើរឿងចិន: Gemini ស្តាប់តែឯង រំលងការសន្ទនាទាំងដុំ (30វិ) ហើយពេលវេលាលោត → Whisper ធ្វើ SRT (គ្រប់ + ពេលត្រូវ)
# ហើយ Gemini ស្តាប់សំឡេងដដែល + បញ្ជីបន្ទាត់ Whisper → ភេទអ្នកនិយាយ / កែពាក្យខុស / បកប្រែ តាមបន្ទាត់នីមួយៗ
VOICE_WINDOW_SEC = 60   # សំឡេងមួយសំណើ (ខ្លី = Gemini ស្តាប់ច្បាស់ មិនរំលង)
VOICE_WINDOW_LINES = 24  # ខ្លី → ចម្លើយ JSON មិនវែងពេក (មិនត្រូវកាត់)
_VOICE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"lines": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {"id": {"type": "INTEGER"},
                       "gender": {"type": "STRING", "enum": ["female", "male", "unknown"]},
                       "text": {"type": "STRING"},
                       "translation": {"type": "STRING"}},
        "required": ["id", "gender", "text", "translation"]}}},
    "required": ["lines"],
}


def _voice_windows(cues):
    windows, cur = [], []
    for c in cues:
        if cur and (c["end"] - cur[0]["start"] > VOICE_WINDOW_SEC * 1000 or len(cur) >= VOICE_WINDOW_LINES):
            windows.append(cur)
            cur = []
        cur.append(c)
    return windows + ([cur] if cur else [])


def gemini_voice_lines(src, cues, target, api_key, model, gemini_post, on_window=None, workers=WORKERS):
    """cues (ពី Whisper/SenseVoice: start/end ms, text) → cue ដដែល (ពេលវេលាដដែល ចំនួនដដែល) ជាមួយ
    gender ("female"/"male"/None), original (ពាក្យដែល Gemini កែតាមសំឡេង) និង text (បកប្រែ)
    បន្ទាត់ដែល Gemini មិនឆ្លើយ → បកប្រែតាមអត្ថបទធម្មតា (translate_lines) — មិនបាត់បន្ទាត់ណាទេ"""
    lang = LANGUAGES[target]
    windows = _voice_windows(cues)
    tmp_dir = tempfile.mkdtemp(prefix="gemvoice_")
    done, lock, seq = [0], threading.Lock(), [0]

    def ask(win, before):
        """{id: item} សម្រាប់ cue ក្នុង win · JSON ខូច (ចម្លើយវែងពេក) → បំបែកពាក់កណ្តាល ហើយសួរម្តងទៀត"""
        t0 = max(0.0, win[0]["start"] / 1000 - 0.5)
        length = win[-1]["end"] / 1000 + 0.5 - t0
        with lock:
            seq[0] += 1
            clip = os.path.join(tmp_dir, f"{seq[0]}.mp3")
        _extract_chunk(src, t0, length, clip)
        rows = [{"id": n, "start": round(c["start"] / 1000 - t0, 1), "end": round(c["end"] / 1000 - t0, 1),
                 "text": c["text"]} for n, c in enumerate(win)]
        prompt = (
            "This audio clip is from a drama. Below are its subtitle lines, numbered by id, with start/end in "
            "seconds from the beginning of this clip. The text comes from automatic speech recognition and may "
            "contain mistakes. For EVERY line, listen to the audio at that time and return:\n"
            "- gender: the gender of the person speaking that line, judged from the voice (female or male; "
            "unknown only if no voice is heard)\n"
            "- text: the line in its original language, with recognition mistakes corrected from the audio "
            "(same meaning and length; never merge or split lines)\n"
            f"- translation: the line translated into {lang}. It will be read aloud as a dub: natural, spoken "
            f"{lang} like real conversation (not word-for-word), names consistent, about as short as the original.\n"
            f"Return exactly {len(rows)} items with the same ids."
            + (f"\nPrevious lines (context only): {json.dumps(before, ensure_ascii=False)}" if before else "")
            + f"\nLines: {json.dumps(rows, ensure_ascii=False)}")
        with open(clip, "rb") as f:
            audio = base64.b64encode(f.read()).decode("ascii")
        try:
            data = _ask_json([{"inlineData": {"mimeType": "audio/mp3", "data": audio}}, {"text": prompt}],
                             _VOICE_SCHEMA, api_key, model, gemini_post)
            items = data.get("lines", []) if isinstance(data, dict) else data
        except BadJSON:
            if len(win) == 1:
                return {}
            h = len(win) // 2
            first = ask(win[:h], before)
            second = ask(win[h:], [c["text"] for c in win[:h][-3:]])
            return {**first, **{k + h: v for k, v in second.items()}}
        except RuntimeError as e:
            if not any(w in str(e) for w in ("audio", "Audio", "input_audio", "400")):
                raise
            items = []  # model/server មិនទទួលសំឡេង → បកប្រែតាមអត្ថបទ (ខាងក្រោម)
        got = {}
        for it in items if isinstance(items, list) else []:
            try:
                got[int(it["id"])] = it
            except (KeyError, TypeError, ValueError):
                pass
        return got

    def work(index):
        win = windows[index]
        got = ask(win, [c["text"] for c in windows[index - 1][-3:]] if index else [])
        out = []
        for n, c in enumerate(win):
            it = got.get(n) or {}
            original = " ".join(str(it.get("text") or "").split()) or c["text"]
            translation = " ".join(str(it.get("translation") or "").split())
            out.append(dict(c, original=original, text=translation if translated(translation, target) else "",
                            gender=it.get("gender") if it.get("gender") in GENDER_LABEL else None))
        with lock:
            done[0] += 1
            if on_window:
                on_window(done[0], len(windows))
        return out

    try:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(windows)))) as pool:
            result = [c for part in pool.map(work, range(len(windows))) for c in part]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    missing = [i for i, c in enumerate(result) if not c["text"]]
    if missing:  # Gemini រំលងបន្ទាត់ខ្លះ → បកប្រែតែបន្ទាត់ទាំងនោះ
        texts = translate_lines([result[i]["original"] for i in missing], target, api_key, model, gemini_post)
        for i, t in zip(missing, texts):
            result[i]["text"] = t
    return result


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

    def ask(batch, before):
        """→ បញ្ជីចំនួនដូច batch ("" = មិនបានបកប្រែ) · JSON ខូច → បំបែកពាក់កណ្តាល ហើយសួរម្តងទៀត"""
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
            try:
                data = _ask_json([{"text": prompt}], _LINES_SCHEMA, api_key, model, gemini_post)
            except BadJSON:
                if len(batch) == 1:
                    return [""]
                h = len(batch) // 2
                return ask(batch[:h], before) + ask(batch[h:], batch[:h][-3:])
            out = data.get("lines", []) if isinstance(data, dict) else data
            out = [" ".join(str(x).split()) for x in out] if isinstance(out, list) else []
            if len(out) == len(batch):
                break
        out = (out + [""] * len(batch))[:len(batch)]  # នៅតែខុសចំនួន → ផ្នែកដែលខ្វះទុកទទេ (បកប្រែម្តងទៀតខាងក្រោម)
        return [t if translated(t, target) else "" for t in out]  # ភាសាដើមត្រឡប់វិញ = មិនបានបកប្រែ

    def work(index):
        batch = batches[index]
        before = batches[index - 1][-3:] if index else []  # បរិបទ (មិនបកប្រែ) — ឱ្យសន្ទនាជាប់គ្នា
        out = ask(batch, before)
        left = [k for k, t in enumerate(out) if not t]
        if left and len(left) < len(batch):  # បន្ទាត់ខ្លះមិនបានបកប្រែ (ឧ. "嗯", "可是") → សាកតែបន្ទាត់ទាំងនោះម្តងទៀត
            for k, t in zip(left, ask([batch[k] for k in left], [])):
                out[k] = t
        if on_batch:
            on_batch()
        return [t or o for t, o in zip(out, batch)]  # មិនបានសោះ → ទុកភាសាដើម (កុំបាត់បន្ទាត់)

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
