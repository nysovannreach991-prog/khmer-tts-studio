"""ដាក់វីដេអូ Ads ខ្លីនៅកណ្តាល និង/ឬចុងវីដេអូ (ឬផ្នែកនីមួយៗ)។

ads spec (dict):
    path      វីដេអូ Ads
    mid       True → ដាក់នៅកណ្តាល (នៅចន្លោះស្ងាត់រវាង subtitle — មិនកាត់ពាក់កណ្តាលប្រយោគ)
    end       True → ដាក់នៅចុង
    per_part  True → ផ្នែកនីមួយៗមាន Ads ផ្ទាល់ខ្លួន, False → កណ្តាល/ចុងនៃវីដេអូទាំងមូល
    volume    0–200 (%) កម្រិតសំឡេង Ads
Ads ត្រូវបានប្តូរទំហំឱ្យត្រូវនឹងវីដេអូ (បន្ថែមរបារខ្មៅបើរាងខុសគ្នា), fps និងសំឡេងដូចគ្នា — ត្រូវ encode ឡើងវិញ។
"""
import os

import overlay as ov
from video_merge import _probe, _run_ffmpeg

MIN_PART_FOR_MID = 60   # វិនាទី — ផ្នែកខ្លីជាងនេះមិនដាក់ Ads កណ្តាល
MIN_GAP_MS = 250        # ចន្លោះស្ងាត់តិចបំផុតរវាង subtitle


def active(spec):
    return bool(spec and spec.get("path") and os.path.isfile(spec["path"]) and (spec.get("mid") or spec.get("end")))


def quiet_point(cues, start, end):
    """ចំណុចជិតកណ្តាល [start, end] (វិនាទី) ដែលនៅចន្លោះស្ងាត់រវាង subtitle"""
    mid = (start + end) / 2
    reach = (end - start) * 0.25
    best = None
    for a, b in zip(cues, cues[1:]):
        if b["start"] - a["end"] < MIN_GAP_MS:
            continue
        t = (a["end"] + b["start"]) / 2000
        if abs(t - mid) <= reach and start + 10 < t < end - 10 and (best is None or abs(t - mid) < abs(best - mid)):
            best = t
    return best if best is not None else mid


def plan(parts, cues, spec):
    """parts = [(path, duration), ...] តាមលំដាប់ → {index: {"mid": វិនាទីក្នុងផ្នែក ឬ None, "end": bool}}"""
    bounds, t = [], 0.0
    for _, dur in parts:
        bounds.append((t, t + dur))
        t += dur
    jobs = {}
    if spec.get("per_part"):
        for i, (a, b) in enumerate(bounds):
            mid = quiet_point(cues, a, b) - a if spec.get("mid") and b - a >= MIN_PART_FOR_MID else None
            if mid is not None or spec.get("end"):
                jobs[i] = {"mid": mid, "end": bool(spec.get("end"))}
    else:
        if spec.get("mid") and t >= MIN_PART_FOR_MID:
            g = quiet_point(cues, 0.0, t)
            i = next(k for k, (a, b) in enumerate(bounds) if g < b or k == len(bounds) - 1)
            jobs[i] = {"mid": g - bounds[i][0], "end": False}
        if spec.get("end"):
            last = len(parts) - 1
            jobs.setdefault(last, {"mid": None, "end": False})["end"] = True
    return jobs


class _Scaled:
    """វឌ្ឍនភាព ffmpeg មួយ → ភាគរយនៃការងារ Ads ទាំងមូល"""
    def __init__(self, progress, offset, share):
        self.progress, self.offset, self.share = progress, offset, share

    def __setitem__(self, key, value):
        if key == "done":
            self.progress["done"] = min(99, int(self.offset + value * self.share / 100))


def insert(part, ad, mid, end, volume, progress, job):
    """ដាក់ Ads ចូលក្នុង part (ជំនួសឯកសារដើម)"""
    info, ad_info = _probe(part), _probe(ad)
    w, h = info["width"] - info["width"] % 2, info["height"] - info["height"] % 2
    fps = round(info["fps"], 3)
    segments = []  # (input args, is_ad)
    if mid is not None:
        segments += [(["-t", f"{mid:.3f}", "-i", part], False), (["-i", ad], True),
                     (["-ss", f"{mid:.3f}", "-i", part], False)]
    else:
        segments.append((["-i", part], False))
    if end:
        segments.append((["-i", ad], True))

    inputs, chains, labels = [], [], []
    for i, (args, is_ad) in enumerate(segments):
        inputs += args
        chains.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                      f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p,"
                      f"setpts=PTS-STARTPTS[v{i}]")
        if is_ad and not ad_info["audio"]:
            chains.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{ad_info['duration']:.3f}[a{i}]")
        else:
            vol = f",volume={volume / 100:.2f}" if is_ad and volume != 100 else ""
            chains.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo{vol},asetpts=PTS-STARTPTS[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    chains.append(f"{''.join(labels)}concat=n={len(segments)}:v=1:a=1[v][a]")
    total = info["duration"] + ad_info["duration"] * sum(is_ad for _, is_ad in segments)

    tmp = part[:-4] + ".ads.mp4"
    for enc in dict.fromkeys([ov.pick_encoder(), "libx264"]):  # GPU បរាជ័យ → libx264
        try:
            _run_ffmpeg([*inputs, "-filter_complex", ";".join(chains), "-map", "[v]", "-map", "[a]",
                         *ov.encoder_args(enc), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", tmp],
                        total, progress)
            break
        except RuntimeError:
            if enc == "libx264":
                raise
            job["warnings"].append(f"Ads: {enc} បរាជ័យ — ប្រើ libx264 ជំនួស")
    os.replace(tmp, part)


def apply(dest, cues, spec, job, progress):
    """ដាក់ Ads ចូលក្នុងវីដេអូទាំងអស់ក្នុង dest (តាមលំដាប់ឈ្មោះ)"""
    names = sorted(f for f in os.listdir(dest) if f.endswith(".mp4") and not f.endswith(".ads.mp4"))
    parts = [(os.path.join(dest, f), _probe(os.path.join(dest, f))["duration"]) for f in names]
    todo = plan(parts, cues, spec)
    if not todo:
        return 0
    ad_len = _probe(spec["path"])["duration"]
    weights = {i: parts[i][1] + ad_len * ((j["mid"] is not None) + j["end"]) for i, j in todo.items()}
    total, offset, count = sum(weights.values()) or 1, 0.0, 0
    for i, j in sorted(todo.items()):
        share = 100 * weights[i] / total
        insert(parts[i][0], spec["path"], j["mid"], j["end"], float(spec.get("volume", 100)),
               _Scaled(progress, offset, share), job)
        offset += share
        count += (j["mid"] is not None) + j["end"]
    return count
