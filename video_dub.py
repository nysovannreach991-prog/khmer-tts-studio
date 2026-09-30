"""ដាក់សំឡេង TTS ចូលក្នុងវីដេអូ ហើយកាត់ជាផ្នែកៗ (ប្រហែល 10 នាទី) នៅចន្លោះស្ងាត់រវាងប្រយោគ។"""
import json
import os
import shutil
import subprocess
import tempfile

from srt_dub import SAMPLE_RATE, write_output
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


def mix_and_split(video, dub_pcm, cues, out_dir, name_base, orig_volume, part_sec, job, overlays=None, ads=None):
    """ដាក់ dub_pcm ចូលវីដេអូ (រក្សាសំឡេងដើមតាម orig_volume) ហើយកាត់ជាផ្នែកៗ។
    overlays = Logo / Lower third (មើល overlay.py) — ត្រូវ encode វីដេអូឡើងវិញ
    ads = វីដេអូ Ads នៅកណ្តាល/ចុង (មើល ads.py) — ដាក់បន្ទាប់ពីកាត់ជាផ្នែករួច"""
    duration, has_audio = probe(video)
    overlays = [o for o in (overlays or []) if o.get("path") and os.path.isfile(o["path"])]
    tmp = tempfile.mkdtemp(prefix="vdub_")
    try:
        dub_wav = os.path.join(tmp, write_output(dub_pcm, tmp, "dub", "wav"))
        dub_sec = len(dub_pcm) / SAMPLE_RATE
        if dub_sec > duration + 1:
            job["warnings"].append(f"សំឡេង TTS វែងជាងវីដេអូ {dub_sec - duration:.1f}s — ផ្នែកលើសត្រូវកាត់ចោល")

        dub = f"[1:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{duration:.3f}"
        if has_audio and orig_volume > 0:
            graph = (f"[0:a]aresample=48000,aformat=channel_layouts=stereo,volume={orig_volume:.2f}[bg];"
                     f"{dub}[d];[bg][d]amix=inputs=2:duration=first:normalize=0[a]")
        else:
            graph = f"{dub}[a]"

        # encode ឡើងវិញ (Logo) → កាត់បានគ្រប់ទីកន្លែង មិនចាំបាច់រក keyframe
        points = cut_points(cues, duration, part_sec, keyframes(video) if part_sec > 0 and not overlays else None)
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
        with_ads = ads_mod.active(ads)
        progress = _StagePct(job, 0, 60 if with_ads else 100)
        force = ["-force_key_frames", ",".join(map(str, points))] if points else []
        if overlays:
            info = _probe(video)
            bounds = [0.0] + points + [duration]
            inputs, chains, vlabel = ov.build(overlays, info["width"], info["fps"], duration,
                                              list(zip(bounds, bounds[1:])), 2)
            enc = ov.pick_encoder()
            job["encoder"] = enc
            args = ["-i", video, "-i", dub_wav, *inputs, "-filter_complex", ";".join(chains + [graph]),
                    "-map", vlabel, "-map", "[a]", "-t", f"{duration:.3f}",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
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
            return folder, _collect(dest, folder, job, cues, ads if with_ads else None)

        args = ["-i", video, "-i", dub_wav, "-filter_complex", graph, "-map", "0:v:0", "-map", "[a]",
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
        return folder, _collect(dest, folder, job, cues, ads if with_ads else None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
