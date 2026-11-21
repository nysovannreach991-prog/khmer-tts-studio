"""បម្លែងឯកសារ .srt ទៅជាសំឡេងមួយ ដែលតម្រឹមតាមពេលវេលានៃ subtitle នីមួយៗ។"""
import os
import re
import subprocess
import threading
import time
import uuid
import wave
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

import numpy as np

SAMPLE_RATE = 24000
CATCHUP_MAX = 2.0  # បន្ទាត់វែងពេក (ក្រោយ max_speed) នឹងរុញបន្ទាត់បន្ទាប់ឱ្យយឺត → ពន្លឿនបន្ថែមរហូតដល់ 2x
STALL_SECONDS = 240  # គ្មានបន្ទាត់ណាចប់ក្នុងរយៈពេលនេះ = ជាប់ → ធ្វើបន្ទាត់នោះម្តងទៀត
_TIME_RE = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")

# សញ្ញាភេទនៅដើមបន្ទាត់ ឧ. "(ស្រី) ..." ឬ "(ប្រុស) ..." — បង្កើតដោយមុខងារ សំឡេង → SRT
_GENDER_RE = re.compile(r"^[\(（\[]\s*(ស្រី|ប្រុស|female|male|f|m|女|男)\s*[\)）\]]\s*[:：៖\-]?\s*", re.I)
_FEMALE = {"ស្រី", "female", "f", "女"}

jobs = {}


# ---------------- SRT parsing ----------------

def _ms(h, m, s, ms):
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms.ljust(3, "0")[:3])


def parse_srt(content):
    """ត្រឡប់បញ្ជី cue: {index, start, end (ms), text}"""
    content = content.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = block.strip().split("\n")
        for i, line in enumerate(lines):
            m = _TIME_RE.search(line)
            if m:
                text = " ".join(lines[i + 1:])
                text = re.sub(r"<[^>]+>|\{[^}]+\}", "", text).strip()
                gender = None
                g = _GENDER_RE.match(text)
                if g:
                    gender = "female" if g.group(1).lower() in _FEMALE else "male"
                    text = text[g.end():].strip()
                if text:
                    cues.append({
                        "index": len(cues) + 1,
                        "start": _ms(*m.groups()[:4]),
                        "end": _ms(*m.groups()[4:]),
                        "text": text,
                        "gender": gender,
                    })
                break
    cues.sort(key=lambda c: c["start"])
    return cues


# ---------------- Audio helpers ----------------

def _ffmpeg(args, data=None):
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *args],
        input=data, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg: {proc.stderr.decode('utf-8', 'replace')[:300]}")
    return proc.stdout


def decode_to_pcm(data):
    """បម្លែង MP3 bytes ទៅជា PCM ដោយផ្ទាល់ក្នុង memory (មិនប្រើឯកសារបណ្តោះអាសន្ន)"""
    raw = _ffmpeg(["-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"], data)
    return np.frombuffer(raw, dtype=np.int16)


def resample_pcm(pcm, rate):
    if rate == SAMPLE_RATE:
        return pcm
    raw = _ffmpeg(["-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
                   "-f", "s16le", "-ar", str(SAMPLE_RATE), "pipe:1"], pcm.tobytes())
    return np.frombuffer(raw, dtype=np.int16)


def speed_up(pcm, factor):
    """ពន្លឿនសំឡេងដោយមិនប្តូរកម្រិតសំឡេង (atempo)"""
    filters, f = [], factor
    while f > 2.0:
        filters.append("atempo=2.0")
        f /= 2.0
    filters.append(f"atempo={f:.4f}")
    raw = _ffmpeg(["-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "pipe:0",
                   "-filter:a", ",".join(filters), "-f", "s16le", "pipe:1"], pcm.tobytes())
    return np.frombuffer(raw, dtype=np.int16)


def trim_silence(pcm, threshold=300, pad_ms=40):
    loud = np.nonzero(np.abs(pcm.astype(np.int32)) > threshold)[0]
    if len(loud) == 0:
        return pcm[:0]
    pad = SAMPLE_RATE * pad_ms // 1000
    return pcm[max(0, loud[0] - pad): loud[-1] + pad]


def write_output(pcm, out_dir, name_base, fmt):
    wav_path = os.path.join(out_dir, name_base + ".wav")
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
    if fmt == "mp3":
        mp3_path = os.path.join(out_dir, name_base + ".mp3")
        _ffmpeg(["-y", "-i", wav_path, "-b:a", "192k", mp3_path])
        os.remove(wav_path)
        return name_base + ".mp3"
    return name_base + ".wav"


# ---------------- Per-cue synthesis ----------------

# អក្សរដែលសំឡេងខ្មែរអានមិនបាន (ចិន/ជប៉ុន/កូរ៉េ ដែលបកប្រែមិនដាច់ ឧ. "谢។")
_UNSPEAKABLE = re.compile(r"[　-ヿ㐀-鿿가-힯豈-﫿＀-￯]+")
_HAS_WORD = re.compile(r"\w")


class Skipped(Exception):
    """បន្ទាត់គ្មានអ្វីអានបាន — ទុកស្ងាត់ជំនួសការបញ្ឈប់ការងារទាំងមូល"""


def _synth_edge(text, voice, opts, edge_audio):
    for attempt in range(3):
        try:
            return decode_to_pcm(edge_audio(text, voice, opts["rate"], opts["pitch"], opts["volume"]))
        except RuntimeError as e:
            if "No audio" not in str(e) and "មិនបានផ្ញើសំឡេង" not in str(e):
                raise
            # ពេលស្របគ្នាច្រើន Edge ជួនកាលមិនផ្ញើសំឡេងមួយភ្លែត (អត្ថបទដដែលអានបានពេលសាកម្តងទៀត)
            time.sleep(1 + attempt)
    # នៅតែគ្មានសំឡេង = អត្ថបទមានតែអក្សរដែលសំឡេងនេះអានមិនបាន ឬតែវណ្ណយុត្តិ
    if voice.startswith(("zh-", "ja-", "ko-")):
        raise Skipped()
    clean = _UNSPEAKABLE.sub(" ", text).strip()
    if clean == text or not _HAS_WORD.search(clean):
        raise Skipped()
    try:
        return decode_to_pcm(edge_audio(clean, voice, opts["rate"], opts["pitch"], opts["volume"]))
    except RuntimeError:
        raise Skipped() from None


def _synth_gemini(text, voice, opts, gemini_call):
    delay = 5
    for attempt in range(6):
        try:
            audio, rate = gemini_call(opts["api_key"], opts["model"], voice, text, opts["style"])
            return resample_pcm(np.frombuffer(audio, dtype=np.int16), rate)
        except RuntimeError as e:
            msg = str(e)
            retryable = any(code in msg for code in ("429", "500", "503", "មិនបានផ្ញើសំឡេង"))
            if not retryable or attempt == 5:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 60)


# ---------------- Job ----------------

def start_job(cues, engine, opts, out_dir, fmt, fit, max_speed, workers, edge_audio, gemini_call,
              post=None, on_finish=None):
    """post(job, pcm) — ជំហានបន្ទាប់ជាជម្រើស (ឧ. ដាក់សំឡេងចូលវីដេអូ) ជំនួសការរក្សាទុកជាឯកសារសំឡេង
    on_finish() — ហៅជានិច្ចនៅចុងបញ្ចប់ (ទោះជោគជ័យ ឬបរាជ័យ) ឧ. លុបឯកសារបណ្តោះអាសន្ន"""
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"status": "running", "done": 0, "total": len(cues), "error": None,
                    "file": None, "warnings": []}
    threading.Thread(
        target=_run_job,
        args=(job_id, cues, engine, opts, out_dir, fmt, fit, max_speed, workers, edge_audio, gemini_call,
              post, on_finish),
        daemon=True,
    ).start()
    return job_id


def render_dub(job, cues, engine, opts, fit, max_speed, workers, edge_audio, gemini_call):
    """បង្កើតសំឡេងគ្រប់បន្ទាត់ ហើយដាក់តាមពេលវេលា — ត្រឡប់ PCM int16 (SAMPLE_RATE, mono)"""
    synth_fn = _synth_gemini if engine == "gemini" else _synth_edge
    backend = gemini_call if engine == "gemini" else edge_audio
    factors = [1.0] * len(cues)  # ល្បឿនដែលបានពន្លឿនរួច (ក្នុង synth)

    def synth(i):
        # cue["voice"] = សំឡេងស្រី/ប្រុសដែលជ្រើសដោយស្វ័យប្រវត្តិ
        # decode + កាត់ស្ងាត់ + ពន្លឿន ធ្វើក្នុង thread ដដែល (ស្របគ្នា) — មិនរង់ចាំដល់ចុងបញ្ចប់
        cue = cues[i]
        try:
            clip = trim_silence(synth_fn(cue["text"], cue.get("voice") or opts["voice"], opts, backend))
        except Skipped:
            job["warnings"].append(f"បន្ទាត់ទី {cue['index']} អានមិនបាន (\"{cue['text'][:30]}\") — ទុកស្ងាត់")
            return np.zeros(0, np.int16)
        next_start = cues[i + 1]["start"] if i + 1 < len(cues) else cue["end"]
        slot = (max(cue["end"], next_start) - cue["start"]) * SAMPLE_RATE // 1000
        if fit and slot > 0 and len(clip) > slot:
            factors[i] = min(len(clip) / slot, max_speed)
            clip = speed_up(clip, factors[i])
        return clip

    def respawn(i):
        # thread daemon ថ្មី — បើ thread ចាស់នៅជាប់ វាមិនរារាំងការបិទកម្មវិធី
        fut = Future()

        def run():
            try:
                fut.set_result(synth(i))
            except BaseException as e:
                fut.set_exception(e)
        threading.Thread(target=run, daemon=True).start()
        return fut

    clips = [None] * len(cues)
    failed = []
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(synth, i): i for i in range(len(cues))}
        pending, retried = set(futures), set()
        while pending:
            finished, pending = wait(pending, timeout=STALL_SECONDS, return_when=FIRST_COMPLETED)
            if not finished:
                # គ្មានបន្ទាត់ណាចប់យូរពេក → ធ្វើបន្ទាត់ដែលកំពុងជាប់ម្តងទៀត (យកមួយណាដែលចប់មុន)
                stuck = [f for f in pending if f.running() and futures[f] not in retried]
                if not stuck:
                    raise RuntimeError(f"ជាប់គាំងលើសពី {STALL_SECONDS * 2 // 60} នាទី — សូមចុចចាប់ផ្តើមម្តងទៀត")
                for f in stuck:
                    retried.add(futures[f])
                    nf = respawn(futures[f])
                    futures[nf] = futures[f]
                    pending.add(nf)
                continue
            for fut in finished:
                i = futures[fut]
                if clips[i] is not None:
                    continue  # ច្បាប់ចម្លងមួយទៀតចប់មុនរួចហើយ
                try:
                    clips[i] = fut.result()
                except Exception as e:
                    # បន្ទាត់ខ្លះបរាជ័យ (បណ្តាញដាច់មួយភ្លែត, Edge បដិសេធអត្ថបទ) → ទុកស្ងាត់ មិនបញ្ឈប់រឿងទាំងមូល
                    # តែបរាជ័យច្រើន = បញ្ហាពិត (គ្មានអ៊ីនធឺណិត, Edge TTS បិទ) → បញ្ឈប់ ហើយប្រាប់មូលហេតុ
                    failed.append(f"បន្ទាត់ទី {cues[i]['index']}: {e}")
                    if len(failed) > max(3, len(cues) // 50):
                        raise RuntimeError(f"បង្កើតសំឡេងមិនបាន {len(failed)} បន្ទាត់ — {failed[0]}") from None
                    job["warnings"].append(f"{failed[-1][:160]} — ទុកស្ងាត់")
                    clips[i] = np.zeros(0, np.int16)
                job["done"] += 1
            pending = {f for f in pending if clips[futures[f]] is None}
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # បរាជ័យ → កុំរង់ចាំបន្ទាត់ដែលនៅសល់

    job["status"] = "mixing"
    pieces, cursor = [], 0  # cursor = ចុងបញ្ចប់នៃ clip មុន (គិតជា sample)
    limit = max(CATCHUP_MAX, max_speed)
    for n, (cue, clip) in enumerate(zip(cues, clips)):
        start = max(cue["start"] * SAMPLE_RATE // 1000, cursor)
        # វែងជាងពេលវេលា (រហូតដល់បន្ទាត់បន្ទាប់) → ពន្លឿនបន្ថែម កុំឱ្យបន្ទាត់បន្ទាប់ៗយឺតជាប់ៗគ្នា
        nxt = cues[n + 1]["start"] * SAMPLE_RATE // 1000 if n + 1 < len(cues) else None
        if fit and nxt and len(clip) and start + len(clip) > nxt and factors[n] < limit:
            room = nxt - start
            extra = min(len(clip) / room, limit / factors[n]) if room > SAMPLE_RATE // 10 else limit / factors[n]
            if extra > 1.02:
                clip = speed_up(clip, extra)
        start = cue["start"] * SAMPLE_RATE // 1000
        if cursor > start:
            job["warnings"].append(
                f"បន្ទាត់ទី {cue['index']} ត្រូវបានពន្យារ {(cursor - start) / SAMPLE_RATE:.1f}s")
            start = cursor
        pieces.append((start, clip))
        cursor = start + len(clip)

    total = max(cursor, cues[-1]["end"] * SAMPLE_RATE // 1000)
    out = np.zeros(total, dtype=np.int16)
    for start, clip in pieces:
        out[start:start + len(clip)] = clip
    return out


def _run_job(job_id, cues, engine, opts, out_dir, fmt, fit, max_speed, workers, edge_audio, gemini_call,
             post, on_finish):
    job = jobs[job_id]
    try:
        _run_job_inner(job, cues, engine, opts, out_dir, fmt, fit, max_speed, workers, edge_audio,
                       gemini_call, post)
    finally:
        if on_finish:
            on_finish()


def _run_job_inner(job, cues, engine, opts, out_dir, fmt, fit, max_speed, workers, edge_audio, gemini_call, post):
    try:
        out = render_dub(job, cues, engine, opts, fit, max_speed, workers, edge_audio, gemini_call)
        if post:
            post(job, out)
        else:
            name_base = f"srt_{engine}_{time.strftime('%Y%m%d_%H%M%S')}"
            job["file"] = write_output(out, out_dir, name_base, fmt)
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
