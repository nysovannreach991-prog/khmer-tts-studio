"""បញ្ចូលវីដេអូច្រើនផ្នែកទៅជាមួយ — ចម្លងផ្ទាល់ (លឿនបំផុត) បើទម្រង់ដូចគ្នា, បើមិនដូចគ្នា encode ឡើងវិញ។"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from fractions import Fraction

from srt_dub import jobs

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _probe(path):
    proc = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                          capture_output=True, creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(f"មិនអាចអាន {os.path.basename(path)}")
    info = json.loads(proc.stdout.decode("utf-8", "replace"))
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    if not v:
        raise RuntimeError(f"{os.path.basename(path)} មិនមែនជាវីដេអូទេ")
    return {
        "duration": float(info["format"].get("duration") or 0),
        "video": (v.get("codec_name"), v.get("width"), v.get("height"), v.get("pix_fmt"),
                  v.get("r_frame_rate"), v.get("profile")),
        "audio": (a.get("codec_name"), a.get("sample_rate"), a.get("channels")) if a else None,
        "width": v.get("width"), "height": v.get("height"),
        "fps": float(Fraction(v.get("r_frame_rate") or "25/1")) or 25.0,
    }


def _run_ffmpeg(args, total_sec, job):
    """ដំណើរការ ffmpeg ហើយធ្វើបច្ចុប្បន្នភាព job['done'] (ភាគរយ) តាម -progress"""
    proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-progress", "pipe:1",
                             "-nostats", *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
    err = []
    t = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)
    t.start()
    for line in proc.stdout:
        key, _, val = line.decode("ascii", "replace").strip().partition("=")
        if key == "out_time_us" and val.isdigit() and total_sec > 0:
            job["done"] = min(99, int(int(val) / 1e6 / total_sec * 100))
    proc.wait()
    t.join()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg: " + b"".join(err).decode("utf-8", "replace")[-400:])


def _concat_copy(paths, out, total, job, tmp):
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for p in paths:
            f.write("file '" + os.path.abspath(p).replace("'", "'\\''") + "'\n")
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", lst, "-map", "0:v:0", "-map", "0:a:0?",
                 "-c", "copy", "-movflags", "+faststart", out], total, job)


def _concat_reencode(paths, infos, out, total, job, tmp):
    """encode ផ្នែកនីមួយៗដាច់ដោយឡែកឱ្យទម្រង់ដូចគ្នា (ទំហំ/fps/សំឡេង PCM) រួចភ្ជាប់ដោយចម្លងផ្ទាល់ —
    ពាក្យបញ្ជាខ្លីជានិច្ច ទោះមានផ្នែករាប់រយ (ពីមុន input ទាំងអស់ក្នុងពាក្យបញ្ជាមួយ → WinError 206)"""
    w, h = infos[0]["width"], infos[0]["height"]
    w, h = w - w % 2, h - h % 2
    fps = round(infos[0]["fps"], 3)
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
          f"setsar=1,fps={fps},format=yuv420p")
    parts, offset = [], 0.0
    for i, (p, info) in enumerate(zip(paths, infos)):
        dur = f"{info['duration']:.3f}"
        if info["audio"]:  # apad + -t → សំឡេងវែងស្មើវីដេអូ (កុំឱ្យលឿន/យឺតជាងរូបភាពពេលភ្ជាប់)
            src = ["-i", p, "-map", "0:v:0", "-map", "0:a:0", "-af", "aresample=48000,apad"]
        else:  # ផ្នែកគ្មានសំឡេង → បំពេញដោយភាពស្ងាត់
            src = ["-i", p, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0"]
        part = os.path.join(tmp, f"p{i:04d}.mkv")
        share = 95 * info["duration"] / (total or 1)
        _run_ffmpeg([*src, "-t", dur, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                     "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", part], info["duration"],
                    _Scaled(job, offset, share))
        parts.append(part)
        offset += share
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        f.writelines(f"file '{os.path.basename(p)}'\n" for p in parts)
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", lst, "-map", "0:v:0", "-map", "0:a:0",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out],
                total, _Scaled(job, offset, 100 - offset))


def start_mute_job(paths, bases, out_dir, temp_files=(), audio=None):
    """លុបសំឡេងដើមចេញពីវីដេអូនីមួយៗ — បើមាន audio ដាក់តែសំឡេងនោះចូល (ឮតែសំឡេងថ្មី)។
    job['files'] = ឈ្មោះឯកសារលទ្ធផល"""
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"status": "running", "done": 0, "total": 100, "error": None,
                    "file": None, "files": [], "warnings": []}
    threading.Thread(target=_run_mute, args=(job_id, paths, bases, out_dir, list(temp_files), audio),
                     daemon=True).start()
    return job_id


def _audio_duration(path):
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                          capture_output=True, creationflags=_NO_WINDOW)
    try:
        return float(proc.stdout.decode().strip())
    except ValueError:
        raise RuntimeError(f"មិនអាចអានសំឡេង {os.path.basename(path)}") from None


class _Scaled:
    """បំលែងវឌ្ឍនភាពនៃឯកសារមួយ ទៅជាភាគរយនៃការងារទាំងមូល"""
    def __init__(self, job, offset, share):
        self.job, self.offset, self.share = job, offset, share

    def __setitem__(self, key, value):
        if key == "done":
            self.job["done"] = min(99, int(self.offset + value * self.share / 100))


def _run_mute(job_id, paths, bases, out_dir, temp_files, audio=None):
    job = jobs[job_id]
    try:
        infos = [_probe(p) for p in paths]
        audio_sec = _audio_duration(audio) if audio else 0
        total = sum(i["duration"] for i in infos) or 1
        stamp, offset = time.strftime("%Y%m%d_%H%M%S"), 0.0
        suffix = "voice" if audio else "muted"
        for i, (path, base, info) in enumerate(zip(paths, bases, infos), 1):
            ext = os.path.splitext(path)[1].lower()
            ext = ext if ext in (".mp4", ".mkv", ".mov") else ".mp4"
            name = f"{base}_{suffix}_{stamp}{ext}"
            if os.path.exists(os.path.join(out_dir, name)):  # ឈ្មោះដូចគ្នា → បន្ថែមលេខ
                base = f"{base}_{i}"
                name = f"{base}_{suffix}_{stamp}{ext}"
            out = os.path.join(out_dir, name)
            share = 100 * info["duration"] / total
            progress = _Scaled(job, offset, share)
            faststart = ["-movflags", "+faststart"] if ext != ".mkv" else []
            if audio:
                # សំឡេងដើមត្រូវលុបចោល — ដាក់តែសំឡេងថ្មី, បំពេញភាពស្ងាត់ ឬកាត់ឱ្យស្មើរយៈពេលវីដេអូ
                if audio_sec > info["duration"] + 1:
                    job["warnings"].append(
                        f"{base}: សំឡេងវែងជាងវីដេអូ {audio_sec - info['duration']:.1f}s — ផ្នែកលើសត្រូវកាត់ចោល")
                inputs = ["-i", path, "-i", audio, "-map", "0:v:0", "-map", "1:a:0"]
                audio_args = ["-af", "apad", "-t", f"{info['duration']:.3f}", "-c:a", "aac", "-b:a", "192k"]
            else:
                if not info["audio"]:
                    job["warnings"].append(f"{base}: គ្មានសំឡេងស្រាប់ហើយ")
                inputs = ["-i", path, "-map", "0:v:0"]
                audio_args = ["-an"]
            try:
                # ចម្លងវីដេអូផ្ទាល់ (លឿនបំផុត)
                _run_ffmpeg([*inputs, "-c:v", "copy", *audio_args, *faststart, out], info["duration"], progress)
            except RuntimeError:
                job["warnings"].append(f"{base}: ត្រូវ encode ឡើងវិញជា H.264")
                name = f"{base}_{suffix}_{stamp}.mp4"
                out = os.path.join(out_dir, name)
                _run_ffmpeg([*inputs, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", *audio_args,
                             "-movflags", "+faststart", out], info["duration"], progress)
            job["files"].append({"name": name, "size": os.path.getsize(out), "duration": info["duration"]})
            offset += share
        job.update(file=job["files"][0]["name"], done=100, status="done")
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        for f in temp_files:
            try:
                os.remove(f)
            except OSError:
                pass


def start_job(paths, out_dir, name_base, force_reencode=False, temp_files=()):
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"status": "running", "done": 0, "total": 100, "error": None,
                    "file": None, "warnings": [], "mode": None}
    threading.Thread(target=_run_job,
                     args=(job_id, paths, out_dir, name_base, force_reencode, list(temp_files)),
                     daemon=True).start()
    return job_id


def _run_job(job_id, paths, out_dir, name_base, force_reencode, temp_files):
    job = jobs[job_id]
    tmp = tempfile.mkdtemp(prefix="merge_")
    try:
        infos = [_probe(p) for p in paths]
        total = sum(i["duration"] for i in infos)
        same = all(i["video"] == infos[0]["video"] and i["audio"] == infos[0]["audio"] for i in infos)
        name = f"{name_base}_merged_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
        out = os.path.join(out_dir, name)
        t0 = time.time()
        if same and not force_reencode:
            job["mode"] = "copy"
            try:
                _concat_copy(paths, out, total, job, tmp)
            except RuntimeError:
                job["warnings"].append("ការចម្លងផ្ទាល់បរាជ័យ — កំពុង encode ឡើងវិញ")
                job["mode"] = "reencode"
                _concat_reencode(paths, infos, out, total, job, tmp)
        else:
            job["mode"] = "reencode"
            if not same:
                job["warnings"].append("វីដេអូមានទម្រង់ខុសគ្នា (ទំហំ/codec/fps) — ត្រូវ encode ឡើងវិញ (យឺតជាង)")
            _concat_reencode(paths, infos, out, total, job, tmp)
        job.update(file=name, duration=total, size=os.path.getsize(out), parts=len(paths),
                   seconds=round(time.time() - t0, 1), done=100, status="done")
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for f in temp_files:
            try:
                os.remove(f)
            except OSError:
                pass
