"""ដាក់សំឡេង TTS ចូលក្នុងវីដេអូ ហើយកាត់ជាផ្នែកៗ (ប្រហែល 10 នាទី) នៅចន្លោះស្ងាត់រវាងប្រយោគ។"""
import json
import re
import threading
import uuid
import os
import shutil
import subprocess
import tempfile

from srt_dub import SAMPLE_RATE, jobs, write_output
import ads as ads_mod
import overlay as ov
from video_merge import _probe, _run_ffmpeg


class _StagePct:
    """_run_ffmpeg សរសេរ ["done"] → រក្សាទុកជា job["stage_pct"] (វឌ្ឍនភាពនៃដំណាក់កាលវីដេអូ)។
    lo–hi: ផ្នែកមួយនៃដំណាក់កាល (ឧ. ដាក់សំឡេង 0–60%, Ads 60–100%)"""
    def __init__(self, job, lo=0, hi=100):
        self.job, self.lo, self.hi = job, lo, hi

    def __setitem__(self, key, value):
        if key == "done":
            self.job["stage_pct"] = int(self.lo + value * (self.hi - self.lo) / 100)

SEARCH_BEFORE = 90   # វិនាទី — រកចន្លោះស្ងាត់មុនចំណុចកាត់
SEARCH_AFTER = 30    # វិនាទី — ... និងក្រោយចំណុចកាត់


def _run(args):
    proc = subprocess.run(args, capture_output=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        raise RuntimeError(f"{os.path.basename(args[0])}: {proc.stderr.decode('utf-8', 'replace')[-400:]}")
    return proc.stdout.decode("utf-8", "replace")


def probe(path):
    """ត្រឡប់ (រយៈពេល វិនាទី, មានសំឡេងឬទេ)"""
    info = json.loads(_run(["ffprobe", "-v", "error", "-print_format", "json",
                            "-show_format", "-show_streams", path]))
    streams = info.get("streams", [])
    if not any(s.get("codec_type") == "video" for s in streams):
        raise RuntimeError("ឯកសារនេះមិនមែនជាវីដេអូទេ")
    return float(info["format"]["duration"]), any(s.get("codec_type") == "audio" for s in streams)


def keyframes(path):
    """ពេលវេលានៃ keyframe របស់វីដេអូ (អានពី packet — មិនចាំបាច់ decode)"""
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", path])
    times = []
    for line in out.splitlines():
        t, _, flags = line.partition(",")
        if "K" in flags:
            try:
                times.append(float(t))
            except ValueError:
                pass
    return sorted(times)


def cut_points(cues, duration, part_sec, kf=None):
    """ជ្រើសចំណុចកាត់ជិត part_sec ម្តងៗ នៅចន្លោះស្ងាត់រវាង subtitle (កុំឱ្យកាត់ពាក់កណ្តាលប្រយោគ)។
    បើមាន kf (keyframes) ចំណុចកាត់ត្រូវជា keyframe ដែលនៅក្នុងចន្លោះស្ងាត់ — ព្រោះការចម្លងវីដេអូ
    ដោយមិន encode អាចកាត់បានតែនៅ keyframe ប៉ុណ្ណោះ។"""
    if part_sec <= 0 or duration <= part_sec * 1.2:
        return []
    kf = kf or []
    gaps = []  # (ចំណុចកាត់, ប្រវែងចន្លោះ) គិតជាវិនាទី
    for a, b in zip(cues, cues[1:]):
        g0, g1 = a["end"] / 1000, b["start"] / 1000
        if g1 <= g0:
            continue
        # keyframe ដែលនៅក្នុងចន្លោះ ហើយមិនជិតដើមប្រយោគបន្ទាប់ពេក — យកមួយដែលជិតកណ្តាលចន្លោះបំផុត
        inside = [t for t in kf if g0 + 0.05 <= t <= g1 - 0.25]
        if inside:
            mid = (g0 + g1) / 2
            gaps.append((min(inside, key=lambda t: abs(t - mid)), g1 - g0, True))
        else:
            gaps.append((g0 + min(0.2, (g1 - g0) / 2), g1 - g0, False))
    points, last = [], 0.0
    while True:
        target = last + part_sec
        if duration - target < part_sec * 0.2:  # ផ្នែកចុងក្រោយខ្លីពេក → បញ្ចូលទៅផ្នែកមុន
            break
        near = [g for g in gaps if target - SEARCH_BEFORE <= g[0] <= target + SEARCH_AFTER and g[0] > last + part_sec / 2]
        # ចន្លោះដែលមាន keyframe ល្អបំផុត, បន្ទាប់មកចន្លោះធំ, ហើយកុំឱ្យឆ្ងាយពី target ពេក
        best = max(near, key=lambda g: g[2] * 10 + min(g[1], 3) - abs(g[0] - target) / 60, default=None)
        point = best[0] if best else target
        points.append(round(point, 3))
        last = point
    return points


def mix_and_split(video, dub_pcm, cues, out_dir, name_base, orig_volume, part_sec, job, overlays=None, ads=None,
                  keep_full=False):
    """ដាក់ dub_pcm ចូលវីដេអូ (រក្សាសំឡេងដើមតាម orig_volume) ហើយកាត់ជាផ្នែកៗ។
    overlays = Logo / Lower third (មើល overlay.py) — ត្រូវ encode វីដេអូឡើងវិញ
    ads = វីដេអូ Ads នៅកណ្តាល/ចុង (មើល ads.py) — ដាក់បន្ទាប់ពីកាត់ជាផ្នែករួច
    dub_pcm = None → រក្សាសំឡេងដើម (ដាក់តែ Logo / Ads លើវីដេអូដែលមានស្រាប់)
    keep_full = True → រក្សាទុកវីដេអូពេញ (មិនកាត់, គ្មាន Ads) ផង: out_dir/<name_base>_full.mp4 → job["full"]"""
    duration, has_audio = probe(video)
    overlays = [o for o in (overlays or []) if o.get("path") and os.path.isfile(o["path"])]
    tmp = tempfile.mkdtemp(prefix="vdub_")
    try:
        if dub_pcm is None:  # រក្សាសំឡេងដើម (បើគ្មាន → ភាពស្ងាត់ ដើម្បីឱ្យ Ads/concat ដំណើរការ)
            base_inputs = ["-i", video]
            graph = ("[0:a]aresample=48000,aformat=channel_layouts=stereo[a]" if has_audio
                     else f"anullsrc=r=48000:cl=stereo,atrim=0:{duration:.3f}[a]")
        else:
            dub_wav = os.path.join(tmp, write_output(dub_pcm, tmp, "dub", "wav"))
            base_inputs = ["-i", video, "-i", dub_wav]
            dub_sec = len(dub_pcm) / SAMPLE_RATE
            if dub_sec > duration + 1:
                job["warnings"].append(f"សំឡេង TTS វែងជាងវីដេអូ {dub_sec - duration:.1f}s — ផ្នែកលើសត្រូវកាត់ចោល")
            dub = f"[1:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{duration:.3f}"
            if has_audio and orig_volume > 0:
                graph = (f"[0:a]aresample=48000,aformat=channel_layouts=stereo,volume={orig_volume:.2f}[bg];"
                         f"{dub}[d];[bg][d]amix=inputs=2:duration=first:normalize=0[a]")
            else:
                graph = f"{dub}[a]"

        with_ads = ads_mod.active(ads)
        # Ads → encode តែម្តង (វីដេអូ + Ads ក្នុង ffmpeg តែមួយ) ជំនួស copy/encode ហើយ encode ផ្នែកនីមួយៗម្តងទៀត។
        # លើកលែង: រក្សាទុកវីដេអូពេញ (គ្មាន Ads) → ប្រើវិធីចាស់ 2 ជំហាន
        single_pass = with_ads and not keep_full
        # encode ឡើងវិញ (Logo / Ads) → កាត់បានគ្រប់ទីកន្លែង មិនចាំបាច់រក keyframe
        encode = bool(overlays) or single_pass
        points = cut_points(cues, duration, part_sec, keyframes(video) if part_sec > 0 and not encode else None)
        folder = f"{name_base}_parts" if points else name_base
        dest = os.path.join(out_dir, folder)
        os.makedirs(dest, exist_ok=True)
        if points:
            output = ["-f", "segment", "-segment_times", ",".join(map(str, points)),
                      "-reset_timestamps", "1", "-segment_start_number", "1",
                      os.path.join(dest, "part%02d.mp4")]
        else:
            output = [os.path.join(dest, f"{name_base}.mp4")]

        job["status"] = "video"
        job["stage_pct"] = 0
        if single_pass:
            _encode_with_ads(video, base_inputs, graph, overlays, duration, points, cues, ads, dest, name_base,
                             job, tmp)
            return folder, _collect(dest, folder, job, cues)
        progress = _StagePct(job, 0, 60 if with_ads else 100)
        force = ["-force_key_frames", ",".join(map(str, points))] if points else []
        if overlays:
            info = _probe(video)
            bounds = [0.0] + points + [duration]
            inputs, chains, vlabel = ov.build(overlays, info["width"], info["fps"], duration,
                                              list(zip(bounds, bounds[1:])), len(base_inputs) // 2)
            enc = ov.pick_encoder()
            job["encoder"] = enc
            args = ov.compact_args([*base_inputs, *inputs, "-filter_complex", ";".join(chains + [graph]),
                                    "-map", vlabel, "-map", "[a]", "-t", f"{duration:.3f}",
                                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"], tmp)
            try:
                _run_ffmpeg(args + ov.encoder_args(enc) + force + output, duration, progress)
            except RuntimeError:
                if enc == "libx264":
                    raise
                job["warnings"].append(f"{enc} បរាជ័យ — ប្រើ libx264 ជំនួស (យឺតជាង)")
                job["encoder"] = "libx264"
                for f in os.listdir(dest):
                    os.remove(os.path.join(dest, f))
                _run_ffmpeg(args + ov.encoder_args("libx264") + force + output, duration, progress)
            if keep_full and points:
                _join_parts(dest, out_dir, name_base, job)
            return folder, _collect(dest, folder, job, cues, ads if with_ads else None)

        args = [*base_inputs, "-filter_complex", graph, "-map", "0:v:0", "-map", "[a]",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
        try:
            # ចម្លងវីដេអូដោយមិន encode ឡើងវិញ — លឿនបំផុត
            _run_ffmpeg(args + ["-c:v", "copy"] + output, duration, progress)
        except RuntimeError:
            # codec មិនត្រូវនឹង mp4 → encode ឡើងវិញជា H.264
            job["warnings"].append("វីដេអូត្រូវ encode ឡើងវិញជា H.264 (យឺតជាងបន្តិច)")
            job["video_reencode"] = True
            for f in os.listdir(dest):
                os.remove(os.path.join(dest, f))
            _run_ffmpeg(args + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"] + force + output,
                        duration, progress)
        if keep_full and points:
            _join_parts(dest, out_dir, name_base, job)
        return folder, _collect(dest, folder, job, cues, ads if with_ads else None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _encode_with_ads(video, base_inputs, graph, overlays, duration, points, cues, ads, dest, name_base, job, tmp):
    """វីដេអូ (+ Logo) + សំឡេង + Ads → encode តែម្តង ហើយកាត់ជាផ្នែក។
    វីដេអូមេត្រូវកាត់ជាដុំៗ (trim) នៅចំណុច Ads ហើយភ្ជាប់ជាមួយ Ads ដោយ concat"""
    info = _probe(video)
    w, h = info["width"] - info["width"] % 2, info["height"] - info["height"] % 2
    fps = round(info["fps"], 3)
    bounds = list(zip([0.0] + points, points + [duration]))
    inputs, chains, vlabel = [], [], "[0:v]"
    if overlays:
        inputs, chains, vlabel = ov.build(overlays, info["width"], info["fps"], duration, bounds,
                                          len(base_inputs) // 2)
    chains = chains + [graph]
    parts, count = ads_mod.layout(bounds, cues, ads)
    segs = [s for part in parts for s in part]
    k = sum(s[0] == "main" for s in segs)
    fix = f"scale={w}:{h}," if (w, h) != (info["width"], info["height"]) else ""
    chains.append(f"{vlabel}{fix}setsar=1,format=yuv420p,split={k}" + "".join(f"[m{i}]" for i in range(k)))
    chains.append(f"[a]asplit={k}" + "".join(f"[n{i}]" for i in range(k)))
    idx = (base_inputs + inputs).count("-i")
    volume = float(ads.get("volume", 100))
    labels, lengths, ad_info, mi, ai = [], [], {}, 0, 0
    for seg in segs:
        if seg[0] == "main":
            t0, t1 = seg[1], seg[2]
            end = f":end={t1:.3f}" if t1 < duration - 0.001 else ""
            chains.append(f"[m{mi}]trim=start={t0:.3f}{end},setpts=PTS-STARTPTS[mv{mi}]")
            chains.append(f"[n{mi}]atrim=start={t0:.3f}{end},asetpts=PTS-STARTPTS[ma{mi}]")
            labels.append(f"[mv{mi}][ma{mi}]")
            lengths.append(t1 - t0)
            mi += 1
        else:
            a_info = ad_info.setdefault(seg[1], _probe(seg[1]))
            inputs += ["-i", seg[1]]
            chains += ads_mod.ad_chains(idx, f"ad{ai}", a_info, w, h, fps, volume)
            labels.append(f"[vad{ai}][aad{ai}]")
            lengths.append(a_info["duration"])
            idx += 1
            ai += 1
    chains.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=1[vc][ac]")

    # ចំណុចកាត់ថ្មី (បន្ទាប់ពីបញ្ចូល Ads) = ចុងនៃផ្នែកនីមួយៗ
    new_points, t, pos = [], 0.0, 0
    for part in parts[:-1]:
        t += sum(lengths[pos:pos + len(part)])
        pos += len(part)
        new_points.append(round(t, 3))
    total = sum(lengths)
    if new_points:
        output = ["-force_key_frames", ",".join(map(str, new_points)),
                  "-f", "segment", "-segment_times", ",".join(map(str, new_points)),
                  "-reset_timestamps", "1", "-segment_start_number", "1", os.path.join(dest, "part%02d.mp4")]
    else:
        output = [os.path.join(dest, f"{name_base}.mp4")]
    args = ov.compact_args([*base_inputs, *inputs, "-filter_complex", ";".join(chains),
                            "-map", "[vc]", "-map", "[ac]", "-t", f"{total:.3f}",
                            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"], tmp)
    progress = _StagePct(job, 0, 100)
    for enc in dict.fromkeys([ov.pick_encoder(), "libx264"]):
        job["encoder"] = enc
        try:
            _run_ffmpeg(args + ov.encoder_args(enc) + output, total, progress)
            break
        except RuntimeError:
            if enc == "libx264":
                raise
            job["warnings"].append(f"{enc} បរាជ័យ — ប្រើ libx264 ជំនួស (យឺតជាង)")
            for f in os.listdir(dest):
                os.remove(os.path.join(dest, f))
    job["ads_inserted"] = count


def _join_parts(dest, out_dir, name_base, job):
    """ភ្ជាប់ផ្នែកទាំងអស់វិញជាវីដេអូពេញមួយ (ចម្លងផ្ទាល់ មិន encode) — មុនដាក់ Ads"""
    names = sorted(f for f in os.listdir(dest) if f.endswith(".mp4"))
    listing = os.path.join(dest, "_full.txt")
    with open(listing, "w", encoding="utf-8") as f:
        for name in names:
            path = os.path.join(dest, name).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{path}'\n")
    full = f"{name_base}_full.mp4"
    try:
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing,
              "-map", "0", "-c", "copy", "-movflags", "+faststart", os.path.join(out_dir, full)])
        job["full"] = full
    except RuntimeError as e:
        job["warnings"].append(f"រក្សាទុកវីដេអូពេញមិនបាន: {str(e)[-200:]}")
    finally:
        os.remove(listing)


def _collect(dest, folder, job, cues, ads=None):
    if ads:
        job["stage_pct"] = 60
        job["ads_inserted"] = ads_mod.apply(dest, cues, ads, job, _StagePct(job, 60, 100))
    job["stage_pct"] = 100
    parts = []
    for f in sorted(os.listdir(dest)):
        if f.endswith(".mp4"):
            path = os.path.join(dest, f)
            parts.append({"name": f, "path": f"{folder}/{f}",
                          "duration": probe(path)[0], "size": os.path.getsize(path)})
    return parts


def speech_segments(video, duration, noise_db=-35, min_silence=0.4):
    """ផ្នែកដែលមានសំឡេងនិយាយ (ពីចន្លោះស្ងាត់ក្នុងសំឡេងដើម) — ជំនួស subtitle ពេលមិនមាន SRT,
    ដើម្បីឱ្យការកាត់ជាផ្នែក និង Ads កណ្តាលនៅចន្លោះស្ងាត់។ ទម្រង់ដូច cue: {"start", "end"} (ms)"""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-vn", "-af",
                           f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
                          capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log = proc.stderr.decode("utf-8", "replace")
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    segments, t = [], 0.0
    for a, b in zip(starts, ends + [duration] * (len(starts) - len(ends))):
        if a > t:
            segments.append({"start": int(t * 1000), "end": int(a * 1000)})
        t = b
    if t < duration:
        segments.append({"start": int(t * 1000), "end": int(duration * 1000)})
    return segments


SNAP_BACK = 30  # វិនាទី — ពេលកាត់យកតែផ្នែកខ្លះ បញ្ចប់នៅចន្លោះស្ងាត់មុនចំណុចបញ្ចប់ (មិនលើស)


def snap_end(cues, start, end):
    """ចន្លោះស្ងាត់ចុងក្រោយក្នុង [end − SNAP_BACK, end] — មិនកាត់ពាក់កណ្តាលប្រយោគ"""
    best = None
    for a, b in zip(cues, cues[1:]):
        t = (a["end"] + b["start"]) / 2000
        if max(start, end - SNAP_BACK) <= t <= end:
            best = t
    return best or end


def trim_video(video, start, end, out, reencode):
    """កាត់យក [start, end] វិនាទី → out (.mp4)។
    start = 0 → ចម្លងផ្ទាល់ (លឿន ហើយត្រឹមត្រូវ), start > 0 → encode ឡើងវិញ ដើម្បីឱ្យចាប់ផ្តើមត្រឹមត្រូវ"""
    length = f"{end - start:.3f}"
    copy = ["-ss", f"{start:.3f}", "-i", video, "-t", length, "-map", "0:v:0", "-map", "0:a:0?",
            "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", out]
    if not reencode:
        try:
            return _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *copy])
        except RuntimeError:
            pass  # codec មិនត្រូវនឹង mp4 → encode ឡើងវិញ
    for enc in dict.fromkeys([ov.pick_encoder(), "libx264"]):
        try:
            return _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", video,
                         "-t", length, "-map", "0:v:0", "-map", "0:a:0?", *ov.encoder_args(enc),
                         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])
        except RuntimeError:
            if enc == "libx264":
                raise


def start_brand_job(video, out_dir, name_base, overlays, ads, part_sec, trim=None):
    """ដាក់តែ Logo / Lower third / Watermark / Ads លើវីដេអូដែលមានស្រាប់ (រក្សាសំឡេងដើម, មិនបកប្រែ)។
    trim = {"start": វិនាទី, "length": វិនាទី, "snap": True} → កាត់យកតែផ្នែកនោះជាមុនសិន"""
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"status": "analyzing", "done": 0, "total": 1, "error": None, "warnings": [],
                    "folder": None, "parts": [], "trimmed": None}

    def run():
        job = jobs[job_id]
        tmp = tempfile.mkdtemp(prefix="brand_")
        try:
            duration, has_audio = probe(video)
            need_gaps = has_audio and (part_sec > 0 or (ads or {}).get("mid") or (trim or {}).get("snap"))
            cues = speech_segments(video, duration) if need_gaps else []
            src = video
            if trim:
                t0 = min(max(float(trim.get("start", 0)), 0.0), max(duration - 1, 0.0))
                t1 = min(t0 + float(trim["length"]), duration)
                if trim.get("snap") and t1 < duration:
                    t1 = snap_end(cues, t0, t1)
                if t0 > 0 or t1 < duration - 0.05:
                    src = os.path.join(tmp, "trimmed.mp4")
                    job["status"] = "trimming"
                    trim_video(video, t0, t1, src, reencode=t0 > 0)
                    ms0, ms1 = t0 * 1000, t1 * 1000
                    cues = [{"start": max(c["start"], ms0) - ms0, "end": min(c["end"], ms1) - ms0}
                            for c in cues if c["end"] > ms0 and c["start"] < ms1]
                    job["trimmed"] = (round(t0, 2), round(t1, 2))
            job["done"] = 1
            job["folder"], job["parts"] = mix_and_split(src, None, cues, out_dir, name_base, 0, part_sec, job,
                                                        overlays, ads)
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001
            job.update(status="error", error=str(e))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    threading.Thread(target=run, daemon=True).start()
    return job_id
