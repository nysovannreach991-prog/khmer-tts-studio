"""ដាក់ Logo និង Lower third លើវីដេអូ — PNG/JPG, វីដេអូ Greenscreen (.mp4) ឬវីដេអូថ្លា (.mov/.webm)។

overlay spec (dict):
    path      ឯកសារ
    key       "auto" | "green" | "blue" | "none"   (auto: ថ្លាស្រាប់ → ប្រើ alpha, វីដេអូ → green, រូបភាព → none)
    strength  0.05–0.5  ភាពខ្លាំងនៃការលុបពណ៌ greenscreen (chromakey similarity)
    pos       "tl" "tc" "tr" "bl" "bc" "br"
    size      % នៃទទឹងវីដេអូ
    margin    % នៃទទឹងវីដេអូ (គម្លាតពីគែម)
    opacity   0–100
    mode      "always" (Logo — វីដេអូ loop ជានិច្ច) | "timed" (Lower third)
    start     វិនាទី — លេចឡើងលើកដំបូង
    every     វិនាទី — លេចម្តងទៀតរៀងរាល់ (0 = តែម្តង)
    show      វិនាទី — រយៈពេលបង្ហាញរូបភាព (វីដេអូ = ប្រវែងវីដេអូខ្លួនឯង)
    per_part  True → រាប់ពេលពីដើមផ្នែកនីមួយៗ (ពេលកាត់វីដេអូជាផ្នែក)
"""
import functools
import json
import os
import subprocess

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
OVERLAY_FILTER = ("Logo / Lower third (*.png *.jpg *.jpeg *.webp *.mp4 *.mov *.webm *.mkv *.avi);;"
                  "រូបភាព (*.png *.jpg *.jpeg *.webp);;វីដេអូ (*.mp4 *.mov *.webm *.mkv *.avi);;ឯកសារទាំងអស់ (*)")
KEY_COLOR = {"green": "0x00FF00", "blue": "0x0047BB"}
MAX_SHOWS = 200  # ការពារកុំឱ្យមាន input ច្រើនពេក
FADE = 0.4       # វិនាទី — fade in/out សម្រាប់ Lower third ជារូបភាព


@functools.lru_cache(maxsize=1)
def _script_option():
    """ffmpeg 7+: "-/filter_complex <file>" · ចាស់ជាងនេះ: "-filter_complex_script <file>" """
    import tempfile
    probe = os.path.join(tempfile.gettempdir(), "aiteam1_graph_probe.txt")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("null")
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "nullsrc=d=0.04",
                               "-/vf", probe, "-f", "null", "-"], capture_output=True, creationflags=_NO_WINDOW)
        return "-/filter_complex" if proc.returncode == 0 else "-filter_complex_script"
    except OSError:
        return "-filter_complex_script"


def compact_args(args, workdir):
    """Windows កំណត់បន្ទាត់ពាក្យបញ្ជាត្រឹម 32767 តួអក្សរ (WinError 206) — Lower third ច្រើនដង = input និង filter វែង។
    filter_complex → ឯកសារ script, ឯកសារដែលប្រើច្រើនដង → ឈ្មោះខ្លីក្នុង workdir"""
    args = list(args)
    if "-filter_complex" in args:
        i = args.index("-filter_complex")
        script = os.path.join(workdir, "graph.txt")
        with open(script, "w", encoding="utf-8") as f:
            f.write(args[i + 1])
        args[i:i + 2] = [_script_option(), script]
    inputs = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-i"]
    too_long = sum(len(a) + 3 for a in args) > 24000  # នៅតែវែង (ឧ. Merge ផ្នែកច្រើន) → ខ្លីទាំងអស់
    short = {}
    for path in dict.fromkeys(p for p in inputs if (too_long or inputs.count(p) > 1) and os.path.isfile(p)):
        link = os.path.join(workdir, f"in{len(short)}{os.path.splitext(path)[1].lower()}")
        try:
            os.link(path, link)
        except OSError:  # ដ្រាយផ្សេងគ្នា → ចម្លង
            import shutil
            shutil.copyfile(path, link)
        short[path] = link
    return [short.get(a, a) if i and args[i - 1] == "-i" else a for i, a in enumerate(args)]


@functools.lru_cache(maxsize=32)
def _media_info(path, mtime):
    proc = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                           path], capture_output=True, creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(f"មិនអាចអាន {os.path.basename(path)}")
    info = json.loads(proc.stdout.decode("utf-8", "replace"))
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        raise RuntimeError(f"{os.path.basename(path)} មិនមែនជារូបភាព ឬវីដេអូទេ")
    is_image = os.path.splitext(path)[1].lower() in IMAGE_EXT
    tags = {k.lower(): str(val) for k, val in (v.get("tags") or {}).items()}
    pix = v.get("pix_fmt") or ""
    vpx_alpha = v.get("codec_name") in ("vp8", "vp9") and tags.get("alpha_mode") == "1"
    alpha = vpx_alpha or pix.startswith(("yuva", "rgba", "bgra", "argb", "abgr", "gbrap", "ya", "pal8"))
    dur = 0.0 if is_image else float(v.get("duration") or info["format"].get("duration") or 0)
    try:
        num, _, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "25/1").partition("/")
        fps = float(num) / float(den or 1) or 25.0
    except (ValueError, ZeroDivisionError):
        fps = 25.0
    return {"image": is_image, "alpha": alpha, "duration": dur,
            "decoder": {"vp9": "libvpx-vp9", "vp8": "libvpx"}.get(v.get("codec_name")) if vpx_alpha else None,
            "width": v.get("width"), "height": v.get("height"), "fps": min(max(fps, 1.0), 120.0)}


def media_info(path):
    return _media_info(path, os.path.getmtime(path))


def describe(path):
    """អត្ថបទខ្លីសម្រាប់ GUI — ប្រភេទឯកសារដែលបានរកឃើញ"""
    info = media_info(path)
    size = f"{info['width']}×{info['height']}"
    if info["image"]:
        return f"🖼 រូបភាព {size}" + (" · ថ្លា" if info["alpha"] else "")
    kind = "វីដេអូថ្លា (alpha)" if info["alpha"] else "វីដេអូ Greenscreen"
    return f"🎞 {kind} {size} · {info['duration']:.1f}s"


def _key_mode(spec, info):
    key = spec.get("key", "auto")
    if key == "auto":
        return "none" if info["alpha"] or info["image"] else "green"
    return key


def _clip_chain(spec, info, video_w):
    """filter សម្រាប់ Logo/Lower third មួយ: key → rgba → ទំហំ → ភាពថ្លា"""
    chain = []
    key = _key_mode(spec, info)
    if key in KEY_COLOR:
        sim = min(max(float(spec.get("strength", 0.15)), 0.01), 0.6)
        chain.append(f"chromakey={KEY_COLOR[key]}:{sim:.3f}:0.08")
        chain.append(f"despill=type={key}")
    chain.append("format=rgba")
    width = max(8, int(round(video_w * float(spec.get("size", 15)) / 100 / 2)) * 2)
    chain.append(f"scale={width}:-1")
    opacity = float(spec.get("opacity", 100)) / 100
    if opacity < 0.999:
        chain.append(f"colorchannelmixer=aa={max(opacity, 0):.3f}")
    return chain


# តំបន់ដែល Watermark រំកិល (ផ្នែកនៃកម្ពស់វីដេអូ: ពី, ដល់)
REGIONS = {"full": (0.0, 1.0), "top": (0.0, 0.5), "bottom": (0.5, 1.0), "middle": (0.25, 0.75)}


def _motion_xy(spec):
    """Watermark រំកិល — expression វាយតម្លៃរាល់ស៊ុម (t = វិនាទី)។
    "\\," = សញ្ញាក្បៀសក្នុង filtergraph (បើមិន escape ffmpeg យល់ថាជាការបំបែក filter)"""
    y0, y1 = REGIONS.get(spec.get("region", "full"), (0.0, 1.0))
    top = f"H*{y0:.2f}+" if y0 else ""
    room = f"max(H*{y1 - y0:.2f}-h\\,1)"  # កម្ពស់ដែលអាចរំកិលបានក្នុងតំបន់
    if spec["motion"] == "jump":  # លោតទៅទីតាំងថ្មីរៀងរាល់ N វិនាទី (លំដាប់ golden ratio → រាយប៉ាយស្មើ)
        n = f"floor(t/{max(float(spec.get('interval', 5)), 0.5):.2f})"
        return f"(W-w)*mod({n}*0.618034+0.13\\,1)", f"{top}{room}*mod({n}*0.414214+0.71\\,1)"
    # bounce: រំកិលត្រង់ ហើយលោតត្រឡប់ពីគែម (ដូច DVD logo) — ល្បឿន = % នៃទទឹងវីដេអូ / វិនាទី
    v = max(float(spec.get("speed", 6)), 0.5) / 100
    return (f"abs(mod(t*W*{v:.4f}\\,2*(W-w))-(W-w))",
            f"{top}abs(mod(t*W*{v * 0.73:.4f}+{room}/2\\,2*{room})-{room})")


def _xy(spec, video_w):
    if spec.get("motion"):
        return _motion_xy(spec)
    m = int(round(video_w * float(spec.get("margin", 3)) / 100))
    pos = spec.get("pos", "tr")
    x = {"l": f"{m}", "c": "(W-w)/2", "r": f"W-w-{m}"}[pos[1]]
    y = {"t": f"{m}", "b": f"H-h-{m}"}[pos[0]]
    # សារ៉េ X / Y — % នៃទទឹង/កម្ពស់វីដេអូ (X+ = ស្តាំ, Y+ = ចុះក្រោម)
    ox, oy = float(spec.get("x", 0)), float(spec.get("y", 0))
    if ox:
        x = f"{x}+W*{ox / 100:.4f}"
    if oy:
        y = f"{y}+H*{oy / 100:.4f}"
    return x, y


def show_times(spec, duration, parts):
    """ពេលវេលាដែល Lower third លេចឡើង — parts = [(ចាប់ផ្តើម, បញ្ចប់), ...] នៃផ្នែកនីមួយៗ"""
    info = media_info(spec["path"])
    length = float(spec.get("show", 8)) if info["image"] else info["duration"]
    start, every = max(0.0, float(spec.get("start", 5))), max(0.0, float(spec.get("every", 0)))
    ranges = parts if spec.get("per_part") and len(parts) > 1 else [(0.0, duration)]
    times = []
    for a, b in ranges:
        t = a + start
        # កុំឱ្យកាត់ Lower third ពាក់កណ្តាល (លើកលែងតែវាវែងជាងផ្នែកខ្លួនឯង)
        while t < b - min(length, (b - a) / 2) and len(times) < MAX_SHOWS:
            times.append(round(t, 3))
            if every <= 0:
                break
            t += max(every, length)
    return times, length


def build(overlays, video_w, fps, duration, parts, first_input, main_label="0:v", preview=False):
    """ត្រឡប់ (input args, filter chains, label ចុងក្រោយ)។
    preview=True — ផ្ទាំងមើលជាមុន: Lower third ត្រូវបង្ហាញភ្លាម (យកស៊ុមពាក់កណ្តាលវីដេអូ)"""
    inputs, chains = [], []
    cur, idx, n = f"[{main_label}]", first_input, 0
    for spec in overlays:
        info = media_info(spec["path"])
        decoder = ["-c:v", info["decoder"]] if info["decoder"] else []
        base = _clip_chain(spec, info, video_w)
        x, y = _xy(spec, video_w)
        if spec.get("mode") == "timed" and not preview:
            times, length = show_times(spec, duration, parts)
            entries = [(t, length) for t in times]
        else:
            entries = [(None, None)]
        for t, length in entries:
            if preview:
                seek = [] if info["image"] else ["-ss", f"{min(info['duration'] * 0.6, max(info['duration'] - 0.1, 0)):.3f}"]
                src = ["-loop", "1"] if info["image"] else []
                inputs += [*src, *seek, *decoder, "-i", spec["path"]]
                extra = []
            elif t is None:  # Logo — ជានិច្ច
                loop = ["-loop", "1", "-framerate", f"{fps:.3f}"] if info["image"] else ["-stream_loop", "-1"]
                inputs += [*loop, *decoder, "-i", spec["path"]]
                extra = []
            elif info["image"]:  # Lower third រូបភាព — បង្ហាញ length វិនាទី, fade in/out
                inputs += ["-loop", "1", "-framerate", f"{fps:.3f}", "-t", f"{length:.3f}",
                           "-itsoffset", f"{t:.3f}", "-i", spec["path"]]
                fade = min(FADE, length / 3)
                extra = [f"fade=t=in:st={t:.3f}:d={fade:.2f}:alpha=1",
                         f"fade=t=out:st={t + length - fade:.3f}:d={fade:.2f}:alpha=1"]
            else:  # Lower third វីដេអូ — លេងម្តងពីពេល t
                inputs += [*decoder, "-itsoffset", f"{t:.3f}", "-i", spec["path"]]
                extra = []
            n += 1
            chains.append(f"[{idx}:v]" + ",".join(base + extra) + f"[ov{n}]")
            chains.append(f"{cur}[ov{n}]overlay=x={x}:y={y}:eof_action=pass:format=auto[vo{n}]")
            cur, idx = f"[vo{n}]", idx + 1
    if n:
        chains.append(f"{cur}format=yuv420p[vout]")
        cur = "[vout]"
    return inputs, chains, cur


@functools.lru_cache(maxsize=1)
def pick_encoder():
    """GPU encoder (លឿនជាង 3–10 ដង) បើមាន, បើមិនមានប្រើ libx264"""
    for enc in ("h264_nvenc", "h264_qsv", "h264_amf"):
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                               "testsrc2=s=256x144:d=0.2", "-c:v", enc, "-f", "null", "-"],
                              capture_output=True, creationflags=_NO_WINDOW)
        if proc.returncode == 0:
            return enc
    return "libx264"


def encoder_args(enc):
    return {
        "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "21", "-b:v", "0",
                       "-forced-idr", "1"],
        "h264_qsv": ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "22", "-forced_idr", "1"],
        "h264_amf": ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "21", "-qp_p", "23"],
        "libx264": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
    }[enc]


# Aspect ratio សម្រាប់ផ្ទៃ Preview (ពេលគ្មានវីដេអូ ឬចង់សាកមើលទំហំផ្សេង)
ASPECTS = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (960, 960), "4:5": (864, 1080), "4:3": (960, 720)}


def _canvas(size, dur=None, moving=False):
    w, h = size
    speed = "0.01:r=25" if moving else "0"
    t = ["-t", f"{dur:.2f}"] if dur else []
    return ["-f", "lavfi", *t, "-i", f"gradients=s={w}x{h}:c0=0x2a1f4d:c1=0x5b2a55:n=2:speed={speed},"
                                      f"drawgrid=w={w // 8}:h={h // 8}:t=1:c=white@0.06"]


def render_preview(video, overlays, out_png, at_sec=None, canvas=None):
    """រូបមើលជាមុន: ស៊ុមមួយពីវីដេអូ (ឬផ្ទៃពណ៌) + Logo/Lower third។
    canvas=(w, h) — ប្រើផ្ទៃទំហំនេះជំនួសវីដេអូ (Aspect ratio ដែលបានជ្រើស)"""
    if video and os.path.isfile(video) and not canvas:
        info = media_info(video)
        w = info["width"] or 1280
        seek = at_sec if at_sec is not None else min(max(info["duration"] * 0.2, 0), 60)
        main = ["-ss", f"{seek:.2f}", "-i", video]
    else:
        canvas = canvas or ASPECTS["16:9"]
        w, main = canvas[0], _canvas(canvas)
    inputs, chains, label = build(overlays, w, 25, 1, [], 1, preview=True)
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *main, *inputs]
    if chains:
        args += ["-filter_complex", ";".join(chains), "-map", label]
    args += ["-frames:v", "1", "-update", "1", out_png]
    proc = subprocess.run(args, capture_output=True, creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-300:])
    return out_png


def clip_length(spec):
    info = media_info(spec["path"])
    return float(spec.get("show", 8)) if info["image"] else info["duration"]


def summary(spec):
    """អត្ថបទពន្យល់ពេលវេលា (សម្រាប់ផ្ទាំងបញ្ជាក់)"""
    if spec.get("motion"):
        how = (f"លោតទីតាំងរៀងរាល់ {spec.get('interval', 5)}វិ" if spec["motion"] == "jump"
               else f"រំកិលទៅមក ល្បឿន {spec.get('speed', 6)}")
        how += {"top": " · ពាក់កណ្តាលលើ", "bottom": " · ពាក់កណ្តាលក្រោម",
                "middle": " · កណ្តាល"}.get(spec.get("region"), "")
        return f"{how} · ទំហំ {spec.get('size', 15)}% · ភាពច្បាស់ {spec.get('opacity', 100)}%"
    pos = {"tl": "លើឆ្វេង", "tc": "លើកណ្តាល", "tr": "លើស្តាំ",
           "bl": "ក្រោមឆ្វេង", "bc": "ក្រោមកណ្តាល", "br": "ក្រោមស្តាំ"}[spec.get("pos", "tr")]
    head = f"{pos} · ទំហំ {spec.get('size', 15)}% · ភាពច្បាស់ {spec.get('opacity', 100)}%"
    if spec.get("x") or spec.get("y"):
        head += f" · X {float(spec.get('x', 0)):+g}% Y {float(spec.get('y', 0)):+g}%"
    if spec.get("mode") != "timed":
        return head + " · បង្ហាញជានិច្ច"
    m, sec = divmod(int(spec.get("start", 0)), 60)
    when = f"លេចនៅ {m}:{sec:02d} (បង្ហាញ {clip_length(spec):.0f}វិ)"
    if spec.get("every"):
        when += f" ហើយម្តងទៀតរៀងរាល់ {spec['every'] / 60:g} នាទី"
    if spec.get("per_part"):
        when += " — ក្នុងផ្នែកនីមួយៗ"
    return f"{head} · {when}"


def render_preview_clip(video, overlays, out_mp4, start=None, canvas=None):
    """វីដេអូ Preview ខ្លី (~10វិ): Logo + Lower third លេចនៅវិនាទីទី 1។ ត្រឡប់ out_mp4"""
    timed = [clip_length(o) for o in overlays if o.get("mode") == "timed"]
    dur = min(30.0, max(8.0, max(timed, default=0) + 3))
    specs = [dict(o, start=1.0, every=0, per_part=False) if o.get("mode") == "timed" else o for o in overlays]
    if video and os.path.isfile(video) and not canvas:
        info = media_info(video)
        w, fps = info["width"] or 1280, info["fps"]
        seek = start if start is not None else min(max(info["duration"] * 0.2, 0), 60)
        seek = max(0.0, min(seek, info["duration"] - dur))
        main = ["-ss", f"{seek:.2f}", "-t", f"{dur:.2f}", "-i", video]
        audio = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "128k"]
    else:
        canvas = canvas or ASPECTS["16:9"]
        w, fps, main, audio = canvas[0], 25.0, _canvas(canvas, dur, moving=True), []
    pre = []
    if w > 1280:  # Preview តូច → បង្កើតលឿន (រក្សា aspect ratio ដដែល)
        pre, w = ["[0:v]scale=1280:-2[m]"], 1280
    inputs, chains, label = build(specs, w, fps, dur, [(0.0, dur)], 1, main_label="m" if pre else "0:v")
    for enc in dict.fromkeys([pick_encoder(), "libx264"]):  # GPU បរាជ័យ → libx264
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *main, *inputs,
                "-filter_complex", ";".join(pre + chains), "-map", label, *audio, "-t", f"{dur:.2f}",
                *encoder_args(enc), "-movflags", "+faststart", out_mp4]
        proc = subprocess.run(args, capture_output=True, creationflags=_NO_WINDOW)
        if proc.returncode == 0:
            break
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-300:])
    return out_mp4


def orientation(path):
    """ឧ. "1080×1920 · 9:16 បញ្ឈរ" """
    info = media_info(path)
    w, h = info["width"] or 0, info["height"] or 0
    if not w or not h:
        return ""
    ratio = min(ASPECTS, key=lambda k: abs(ASPECTS[k][0] / ASPECTS[k][1] - w / h))
    kind = "ដេក" if w > h * 1.05 else "បញ្ឈរ" if h > w * 1.05 else "ការ៉េ"
    return f"{w}×{h} · {ratio} {kind}"
