"""🎤 លុបសំឡេងនិយាយចេញពីវីដេអូ — រក្សាភ្លេង និងសូរផ្សេងៗ (Demucs htdemucs, Meta)។

ប្រើពេល Dub: សំឡេងខ្មែរ + ភ្លេងដើម (មិនឮការនិយាយភាសាចិន) ជំនួសការ Mute ទាំងអស់។
ដំណើរការក្នុង process កូន (torch ធំ — មិនផ្ទុកក្នុងកម្មវិធី) · ធ្វើម្តងមួយដុំ 60វិ (វីដេអូ 2 ម៉ោងក៏មិនអស់ RAM)
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import whisper_srt as ws

MODEL = "htdemucs"
MODEL_MB = 85
TORCH_HOME = os.path.join(ws.DATA_DIR, "torch")
SR = 44100
CHUNK = 60          # វិនាទី ក្នុងមួយដុំ
CONTEXT = 3         # វិនាទី បរិបទសងខាង (កាត់ចោលពេលភ្ជាប់) — កុំឱ្យឮព្រំដែនដុំ
# demucs + openunmix ត្រូវ --no-deps (បើមិនដូច្នេះ pip ទាញ torchaudio/torch CPU មកជាន់ torch CUDA)
INSTALL = [["torch", "dora-search", "einops", "julius", "lameenc"], ["--no-deps", "demucs>=4", "openunmix"]]


def installed():
    import importlib.util as u
    return all(u.find_spec(m) for m in ("torch", "demucs", "julius", "einops", "dora"))


def install(on_line=None):
    for packages in INSTALL:
        ws.pip_install(packages, on_line)


# ---------------- process កូន ----------------

def _separate(media, out_audio, keep_vocals):
    """media → out_audio (ភ្លេង/សូរ គ្មានការនិយាយ) [+ <out>_vocals.wav]"""
    import numpy as np
    os.environ["TORCH_HOME"] = TORCH_HOME
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    def say(**msg):
        print(json.dumps(msg, ensure_ascii=False), flush=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    say(phase="load", device=device)
    model = get_model(MODEL)
    model.to(device).eval()
    vocals_idx = model.sources.index("vocals")
    import ffmpeg_setup
    ffmpeg_setup.add_to_path()
    tmp = tempfile.mkdtemp(prefix="novocal_")
    try:
        raw = os.path.join(tmp, "in.f32")  # សំឡេងទាំងមូលនៅលើ disk (memmap) មិនមែន RAM
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", media, "-vn",
                        "-ac", "2", "-ar", str(SR), "-f", "f32le", raw], check=True, creationflags=ws._NO_WINDOW)
        audio = np.memmap(raw, dtype=np.float32, mode="r").reshape(-1, 2)
        n = len(audio)
        say(phase="run", total=round(n / SR, 1), device=device)
        outs = {"bg": open(os.path.join(tmp, "bg.f32"), "wb")}
        if keep_vocals:
            outs["vocals"] = open(os.path.join(tmp, "vocals.f32"), "wb")
        step, ctx = CHUNK * SR, CONTEXT * SR
        for start in range(0, n, step):
            a, b = max(0, start - ctx), min(n, start + step + ctx)
            x = torch.from_numpy(np.ascontiguousarray(audio[a:b].T)).to(device)
            ref = x.mean(0)
            mean, std = ref.mean(), ref.std() + 1e-8
            with torch.no_grad():
                y = apply_model(model, ((x - mean) / std)[None], device=device, split=True, overlap=0.25,
                                progress=False)[0] * std + mean
            keep = slice(start - a, start - a + min(step, n - start))
            vocals = y[vocals_idx][:, keep]
            bg = (x[:, keep] - vocals)  # ដើម − ការនិយាយ = ភ្លេង + សូរ (រក្សាគុណភាពដើមល្អជាងបូក stem)
            outs["bg"].write(bg.T.contiguous().float().cpu().numpy().tobytes())
            if keep_vocals:
                outs["vocals"].write(vocals.T.contiguous().float().cpu().numpy().tobytes())
            say(progress=round(min(start + step, n) / SR, 1))
        for f in outs.values():
            f.close()
        del audio
        for key, dst in [("bg", out_audio)] + ([("vocals", os.path.splitext(out_audio)[0] + "_vocals.wav")]
                                               if keep_vocals else []):
            subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "f32le",
                            "-ar", str(SR), "-ac", "2", "-i", os.path.join(tmp, f"{key}.f32"), dst],
                           check=True, creationflags=ws._NO_WINDOW)
        say(done=1)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------- process មេ ----------------

def _model_mb():
    total = 0
    for root, _, files in os.walk(TORCH_HOME):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total / 1e6


def start_job(media, out_audio, keep_vocals=False):
    """→ job (srt_dub.jobs): download → load → running (done/total វិនាទី) → done · out_audio = ភ្លេងគ្មានការនិយាយ"""
    from srt_dub import jobs
    job_id = uuid.uuid4().hex[:12]
    job = jobs[job_id] = {"status": "load", "done": 0, "total": 100, "error": None, "warnings": [],
                          "device": None, "file": out_audio, "seconds": 0}

    def run():
        t0 = time.time()
        try:
            before = _model_mb()
            proc = subprocess.Popen([ws._python(), os.path.abspath(__file__), media, out_audio,
                                     "1" if keep_vocals else "0"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=ws._NO_WINDOW)
            err = []
            threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True).start()
            state = {"phase": "load"}

            def watch_download():
                while proc.poll() is None and state["phase"] == "load":
                    grown = _model_mb() - before
                    if grown > 2:
                        job.update(status="download", done=min(99, int(grown * 100 / MODEL_MB)))
                    time.sleep(0.5)
            threading.Thread(target=watch_download, daemon=True).start()
            for raw in proc.stdout:
                try:
                    msg = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if msg.get("phase") == "load":
                    job["device"] = msg.get("device")
                elif msg.get("phase") == "run":
                    state["phase"] = "run"
                    job.update(status="running", total=max(int(msg["total"]), 1), done=0, device=msg.get("device"))
                elif "progress" in msg:
                    job["done"] = min(int(msg["progress"]), job["total"])
            proc.wait()
            state["phase"] = "end"
            if proc.returncode != 0:
                lines = b"".join(err).decode("utf-8", "replace").strip().splitlines()
                tail = next((l for l in reversed(lines) if "Error" in l), lines[-1] if lines else "")
                raise RuntimeError(tail or f"exit {proc.returncode}")
            job.update(done=job["total"], seconds=time.time() - t0, status="done")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "No module named" in msg:
                msg = "មិនទាន់ដំឡើង Demucs"
            job.update(status="error", error=f"លុបការនិយាយ: {msg}")

    threading.Thread(target=run, daemon=True).start()
    return job_id


def wait(job_id, on_tick=None, stop=None):
    """រង់ចាំ job ចប់ (សម្រាប់ thread ផ្សេង) → path ភ្លេង"""
    from srt_dub import jobs
    while True:
        job = jobs[job_id]
        if on_tick:
            on_tick(job)
        if job["status"] == "done":
            return job["file"]
        if job["status"] == "error":
            raise RuntimeError(job["error"])
        if stop is not None and stop.is_set():
            raise InterruptedError
        time.sleep(0.3)


if __name__ == "__main__":  # process កូន: vocal_remove.py <media> <out_audio> <keep_vocals 0|1>
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    _separate(sys.argv[1], sys.argv[2], sys.argv[3] == "1")
