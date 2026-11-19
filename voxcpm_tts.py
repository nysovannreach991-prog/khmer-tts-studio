"""VoxCPM2 (OpenBMB · 2B · 30 ភាសា រួមទាំងខ្មែរ · 48kHz) — សំឡេង AI លើ GPU ផ្ទាល់ខ្លួន + ចម្លងសំឡេង (voice cloning)។

* preset:<id>  → សំឡេងរចនាពីការពិពណ៌នា (Voice Design) — បង្កើតគំរូម្តង រក្សាទុក រួចប្រើជា reference ឱ្យសំឡេងដដែលគ្រប់បន្ទាត់
* clone:<wav>  → ចម្លងសំឡេងពីឯកសារយោង (ឧ. សំឡេងតួអង្គដើមក្នុងវីដេអូ)
Model ផ្ទុកតែម្តងក្នុង process កូន (--serve · ~5.3 GB VRAM) ដូច funasr_srt · សំណើម្តងមួយ (GPU)។
"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import whisper_srt as ws

MODEL_ID = "openbmb/VoxCPM2"
DATA_DIR = os.path.join(ws.DATA_DIR, "voxcpm")
VOICE_DIR = os.path.join(DATA_DIR, "voices")
MODEL_MB = 4700
# សំឡេងរចនា (Voice Design) — id → ការពិពណ៌នា
PRESETS = {
    "female1": "A young woman, gentle, warm and clear voice",
    "female2": "A mature woman, calm, low and elegant voice",
    "male1": "A young man, bright, friendly and confident voice",
    "male2": "A middle-aged man, deep, steady and serious voice",
    "kid": "A little child around seven years old, bright, cute and lively voice",
    "narrator": "A professional male storyteller, deep, calm and engaging narration voice",
}
PRESET_TEXT = "សួស្តី ខ្ញុំរីករាយណាស់ដែលបានជួបអ្នកនៅថ្ងៃនេះ។ តោះយើងចាប់ផ្តើមរឿងរបស់យើងទាំងអស់គ្នា។"


def installed():
    import importlib.util as u
    return all(u.find_spec(m) for m in ("torch", "voxcpm", "torchaudio", "inflect", "wetext", "librosa", "soundfile"))


def install(on_line=None):
    """voxcpm ដោយ --no-deps (បើមិនដូច្នោះ pip ទាញ torch/torchaudio កំណែ CPU មកជាន់ torch CUDA) + អ្វីដែលវាប្រើពិត"""
    import importlib.util as u
    if not u.find_spec("torch"):
        ws.pip_install(["torch"], on_line)
    ws.pip_install(["--no-deps", "voxcpm>=2.0.3", "torchaudio"], on_line)
    ws.pip_install(["inflect", "wetext", "addict", "simplejson", "einops", "pydantic", "librosa", "soundfile",
                    "transformers>=4.36.2", "huggingface-hub", "safetensors", "tqdm"], on_line)


def _hub_dir():
    return os.path.join(os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface"), "hub",
                        "models--" + MODEL_ID.replace("/", "--"))


def model_ready():
    """Model ទាញយករួច (HF cache) — បើមិនទាន់ លើកដំបូងទាញ ~4.7 GB"""
    snaps = os.path.join(_hub_dir(), "snapshots")
    return os.path.isdir(snaps) and any(os.path.isfile(os.path.join(snaps, d, "model.safetensors")) for d in os.listdir(snaps))


def _hub_mb():
    total = 0
    for root, _, files in os.walk(_hub_dir()):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total / 1e6


# ---------------- process កូន ----------------
def _serve():
    def say(**msg):
        print(json.dumps(msg, ensure_ascii=False), flush=True)

    real_out = sys.stdout
    sys.stdout = sys.stderr  # voxcpm print ព័ត៌មានទៅ stdout → កុំឱ្យលាយជាមួយ JSON
    import soundfile as sf
    import torch
    from voxcpm import VoxCPM
    device = "cuda" if ws.torch_device(torch) == "cuda" else "cpu"
    sys.stdout = real_out
    say(phase="load", device=device)
    sys.stdout = sys.stderr
    # optimize=False: torch.compile លើ Windows ត្រូវការ compiler — មិនចាំបាច់
    model = VoxCPM.from_pretrained(MODEL_ID, load_denoiser=False, optimize=False, local_files_only=model_ready(), device=device)
    rate = model.tts_model.sample_rate
    sys.stdout = real_out
    say(phase="ready", device=device, rate=rate)
    for raw in sys.stdin:
        try:
            req = json.loads(raw)
        except ValueError:
            continue
        try:
            sys.stdout = sys.stderr
            kw = {"text": req["text"], "cfg_value": float(req.get("cfg", 2.0)), "inference_timesteps": int(req.get("steps", 10))}
            if req.get("ref"):
                kw["reference_wav_path"] = req["ref"]
            wav = model.generate(**kw)
            sf.write(req["out"], wav, rate, subtype="PCM_16")
            sys.stdout = real_out
            say(done=req["out"], sec=round(len(wav) / rate, 2))
        except Exception as e:  # noqa: BLE001 — បន្ទាត់នេះបរាជ័យ តែ process នៅរស់
            sys.stdout = real_out
            say(error=str(e) or type(e).__name__)


# ---------------- process មេ ----------------
_srv = {"proc": None, "err": [], "phase": "off", "device": ""}
_lock = threading.Lock()


def state():
    """off · load (កំពុងបើក/ទាញយក model) · ready"""
    p = _srv["proc"]
    if p is None or p.poll() is not None:
        return "off"
    return _srv["phase"]


def download_pct():
    return min(99, int(_hub_mb() * 100 / MODEL_MB)) if not model_ready() else 100


def _start():
    proc = subprocess.Popen([ws._python(), os.path.abspath(__file__), "--serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, creationflags=ws._NO_WINDOW)
    _srv.update(proc=proc, err=[], phase="load")

    def drain():
        for line in proc.stderr:
            _srv["err"] = (_srv["err"] + [line.decode("utf-8", "replace").rstrip()])[-40:]
    threading.Thread(target=drain, daemon=True).start()
    return proc


def _crash_text():
    lines = [l for l in _srv["err"] if l.strip()]
    return next((l for l in reversed(lines) if "Error" in l or "error" in l), lines[-1] if lines else "")


def _request(req, timeout=600):
    """ផ្ញើសំណើមួយ → dict ចម្លើយ ({"done", "sec"}) · ត្រូវកាន់ _lock"""
    proc = _srv["proc"]
    if proc is None or proc.poll() is not None:
        proc = _start()
    proc.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
    proc.stdin.flush()
    t0 = time.time()
    while True:
        raw = proc.stdout.readline()
        if not raw:
            proc.wait()
            _srv["phase"] = "off"
            msg = _crash_text() or f"exit {proc.returncode}"
            if "out of memory" in msg.lower():
                msg = "VRAM មិនគ្រប់ (VoxCPM2 ត្រូវការ ~6 GB) — បិទកម្មវិធីផ្សេងដែលប្រើ GPU រួចសាកម្តងទៀត"
            raise RuntimeError(f"VoxCPM2: {msg[:300]}")
        try:
            msg = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if msg.get("phase"):
            _srv.update(phase=msg["phase"], device=msg.get("device", ""))
        elif "error" in msg:
            raise RuntimeError(f"VoxCPM2: {msg['error'][:300]}")
        elif "done" in msg:
            return msg
        if time.time() - t0 > timeout and _srv["phase"] == "ready":
            raise RuntimeError("VoxCPM2: គ្មានចម្លើយ (timeout)")


def _read_pcm(path):
    """wav (48k) → PCM int16 mono នៅ srt_dub.SAMPLE_RATE"""
    import numpy as np
    import soundfile as sf
    import srt_dub
    data, rate = sf.read(path, dtype="int16", always_2d=True)
    return srt_dub.resample_pcm(np.ascontiguousarray(data[:, 0]), rate)


def preset_path(voice_id):
    return os.path.join(VOICE_DIR, f"{voice_id}.wav")


def _ensure_preset(voice_id, opts):
    """គំរូសំឡេងរបស់ preset (Voice Design ម្តង) → ប្រើជា reference ឱ្យសំឡេងដដែលរាល់បន្ទាត់"""
    path = preset_path(voice_id)
    if not os.path.isfile(path):
        os.makedirs(VOICE_DIR, exist_ok=True)
        tmp = path + ".tmp.wav"
        _request({"text": f"({PRESETS[voice_id]}){PRESET_TEXT}", "out": tmp, "steps": 10, "cfg": 2.0})
        os.replace(tmp, path)
    return path


def reset_preset(voice_id):
    """លុបគំរូ → លើកក្រោយរចនាសំឡេងថ្មី (សំឡេងផ្សេង)"""
    try:
        os.remove(preset_path(voice_id))
    except OSError:
        pass


def synth(text, voice, opts):
    """→ PCM int16 (srt_dub.SAMPLE_RATE) · voice = "preset:<id>" ឬ "clone:<wav>" · opts: steps, cfg, tmp"""
    text = " ".join((text or "").split())
    if not text:
        import numpy as np
        return np.zeros(0, np.int16)
    kind, _, arg = (voice or "preset:female1").partition(":")
    out = os.path.join(opts.get("tmp") or DATA_DIR, f"vox_{uuid.uuid4().hex[:10]}.wav")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with _lock:
        if kind == "clone" and os.path.isfile(arg):
            ref = arg
        else:
            ref = _ensure_preset(arg if arg in PRESETS else "female1", opts)
        last = None
        for attempt in range(2):
            try:
                _request({"text": text, "ref": ref, "out": out, "steps": opts.get("steps", 10), "cfg": opts.get("cfg", 2.0)})
                break
            except RuntimeError as e:
                last = e
                if "VRAM" in str(e) or attempt:
                    raise
        else:
            raise last
    try:
        return _read_pcm(out)
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


def warm():
    """បើក process + ផ្ទុក model ទុកមុន (មិនរារាំង)"""
    def run():
        if _lock.acquire(blocking=False):
            try:
                if state() == "off":
                    _start()
            except OSError:
                pass
            finally:
                _lock.release()
    threading.Thread(target=run, daemon=True).start()


def stop():
    p = _srv["proc"]
    if p is not None:
        try:
            p.kill()
        except OSError:
            pass
    _srv.update(proc=None, phase="off")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        _serve()
