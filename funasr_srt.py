"""SenseVoice / Paraformer-zh + fsmn-vad (FunASR, Alibaba) → .srt — ស្តាប់លើកុំព្យូទ័រ ឥតគិតថ្លៃ។

* fsmn-vad កាត់សំឡេងនៅចន្លោះស្ងាត់ (ប្រយោគនីមួយៗ) → មិនកាត់ពាក់កណ្តាលពាក្យ, ពេលវេលា subtitle ត្រឹមត្រូវ
* SenseVoice-Small: លឿនបំផុត — ចិន អង់គ្លេស កាតាំង ជប៉ុន កូរ៉េ
* Paraformer-zh + ct-punc: សម្រាប់ចិន — ពេលវេលាតាមតួអក្សរ + វណ្ណយុត្តិ
Model ផ្ទុកតែម្តងក្នុង process កូនដែលនៅរស់ (--serve) — រឿងបន្ទាប់ៗមិនចាំបាច់ផ្ទុកម្តងទៀត (~20វិ)
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

import whisper_srt as ws

MODEL_DIR = os.path.join(ws.DATA_DIR, "funasr")
PACKAGES = ["torch", "funasr>=1.2", "modelscope", "kaldi-native-fbank"]
# torch កំណែ CUDA (~2.5 GB) ជំនួសកំណែ CPU — pip មិនប្តូរខ្លួនឯងទេ ព្រោះ "torch" មានរួចហើយ
# cu126 = កាតចាស់ដល់ RTX 40 (sm_50–sm_90) · RTX 50 (Blackwell, sm_120) ត្រូវការ cu128 ឡើងទៅ
GPU_PACKAGES = ["--force-reinstall", "--no-deps", "torch", "--index-url", "https://download.pytorch.org/whl/cu126"]


def _cuda_tag():
    return 128 if ws.gpu_compute_cap() >= 10 else 126


def gpu_packages():
    return GPU_PACKAGES[:-1] + [f"https://download.pytorch.org/whl/cu{_cuda_tag()}"]
ENGINES = {  # ឈ្មោះ → (model, punc, ទំហំទាញយក MB)
    "sensevoice": ("iic/SenseVoiceSmall", None, 900),
    "paraformer": ("paraformer-zh", "ct-punc", 1080),
}
SENSEVOICE_LANGS = {"zh", "en", "yue", "ja", "ko"}
BATCH = 32                 # VAD segment ក្នុងមួយដុំ
VAD_OPTS = {"max_end_silence_time": 300,       # ms — កាត់នៅចន្លោះស្ងាត់ខ្លី → មួយប្រយោគ/segment
            "max_single_segment_time": 20000}  # ms — segment វែងបំផុត
_TAG = re.compile(r"<\|[^|]*\|>")
_PUNCT = re.compile(r"^[\s，。！？、；：,.!?;:…\"'“”‘’（）()《》\-—]+$")
_CJK_TOKEN = re.compile(r"[㐀-鿿가-힯぀-ヿ]")
_TOKENS = re.compile(r"[㐀-鿿가-힯぀-ヿ]|[A-Za-z0-9'’]+|\S")


def installed():
    import importlib.util as u
    return all(u.find_spec(m) for m in ("torch", "funasr", "modelscope")) and \
        bool(u.find_spec("kaldi_native_fbank") or u.find_spec("torchaudio"))


def ensure_gpu_torch(on_line=None):
    """កាត NVIDIA + torch កំណែ CPU → ប្តូរជា torch CUDA (pip លំនាំដើមលើ Windows ផ្តល់តែកំណែ CPU)"""
    import importlib.util as u
    if u.find_spec("torch") and not gpu_ready() and ws.has_nvidia():
        stop_servers()  # process ចាស់កំពុងប្រើ torch CPU → បិទសិន (Windows ចាក់សោឯកសារ)
        ws.pip_install(gpu_packages(), on_line)


def gpu_ready():
    """torch មាន CUDA ដែលស្គាល់កាតនេះ (មិនមែនកំណែ CPU ឬ cu126 លើ RTX 50) — ពិនិត្យដោយមិន import torch (យឺត)"""
    import importlib.metadata as md
    try:
        m = re.search(r"\+cu(\d+)", md.version("torch"))
    except md.PackageNotFoundError:
        return False
    return bool(m) and int(m.group(1)) >= _cuda_tag()


# ---------------- អត្ថបទ + ពេលវេលា → បន្ទាត់ subtitle ----------------

def _words_paraformer(res, offset):
    """Paraformer: text (មានវណ្ណយុត្តិ) + timestamp[] សម្រាប់តួអក្សរ/ពាក្យ → [(start, end, text)] វិនាទី"""
    text, stamps = res.get("text") or "", res.get("timestamp") or []
    tokens = _TOKENS.findall(text)
    if not stamps or sum(not _PUNCT.match(t) for t in tokens) != len(stamps):
        return None
    out, k = [], 0
    for t in tokens:
        if _PUNCT.match(t):
            if out:
                out[-1] = (out[-1][0], out[-1][1], out[-1][2] + t)
            continue
        a, b = stamps[k]
        k += 1
        out.append((offset + a / 1000, offset + b / 1000, t if _CJK_TOKEN.match(t) or not out else " " + t))
    return out


def _even_words(text, start, end):
    """ពេលវេលាតាម segment ប៉ុណ្ណោះ → ចែកពេលតាមតួអក្សរ (វណ្ណយុត្តិភ្ជាប់នឹងពាក្យមុន) ដើម្បីកាត់បន្ទាត់វែងបាន"""
    tokens = []
    for t in _TOKENS.findall(text) or [text]:
        if _PUNCT.match(t) and tokens:
            tokens[-1] += t
        else:
            tokens.append(t if _CJK_TOKEN.match(t) or not tokens else " " + t)
    weight = [max(len(t.strip()), 1) for t in tokens]
    step, out, t0 = (end - start) / max(sum(weight), 1), [], start
    for tok, w in zip(tokens, weight):
        out.append((t0, t0 + w * step, tok))
        t0 += w * step
    return out


# ---------------- process កូន ----------------

class _Engine:
    def __init__(self, engine, say):
        os.environ["MODELSCOPE_CACHE"] = MODEL_DIR
        import torch
        from funasr import AutoModel
        self.engine = engine
        self.device = "cuda:0" if ws.torch_device(torch) == "cuda" else "cpu"
        say(phase="load", device=self.device)
        model, punc, _ = ENGINES[engine]
        common = {"disable_update": True, "device": self.device, "disable_pbar": True, "disable_log": True}
        self.vad = AutoModel(model="fsmn-vad", **common)
        self.asr = AutoModel(model=model, **({"punc_model": punc} if punc else {}), **common)
        say(phase="ready", device=self.device)

    def run(self, media, out_srt, lang, say):
        audio = ws._load_audio(media)
        if isinstance(audio, str):
            raise RuntimeError("អានសំឡេងពីវីដេអូមិនបាន (ffmpeg)")
        total = len(audio) / 16000
        say(phase="listen", total=round(total, 1), device=self.device)
        segs = self.vad.generate(input=audio, **VAD_OPTS)[0]["value"] if len(audio) else []
        texts = [None] * len(segs)
        # តម្រៀបតាមប្រវែង → ដុំនីមួយៗមានប្រវែងស្រដៀងគ្នា (padding តិច = លឿនជាង)
        order = sorted(range(len(segs)), key=lambda i: segs[i][1] - segs[i][0])
        done_sec = 0.0
        for k in range(0, len(order), BATCH):
            idx = order[k:k + BATCH]
            clips = [audio[segs[i][0] * 16:segs[i][1] * 16] for i in idx]
            if self.engine == "sensevoice":
                res = self.asr.generate(input=clips, language=lang if lang in SENSEVOICE_LANGS else "auto",
                                        use_itn=True, batch_size=len(clips))
            else:
                res = self.asr.generate(input=clips, batch_size=len(clips))
            for i, r in zip(idx, res):
                texts[i] = r
                done_sec += (segs[i][1] - segs[i][0]) / 1000
            say(progress=round(min(done_sec, total), 1))
        lines = []
        for (a, b), r in zip(segs, texts):
            text = " ".join(_TAG.sub("", (r or {}).get("text") or "").split())
            if not text or _PUNCT.match(text):
                continue
            words = _words_paraformer(r, a / 1000) if self.engine == "paraformer" else None
            lines += ws.split_words(words or _even_words(text, a / 1000, b / 1000))
        with open(out_srt, "w", encoding="utf-8") as f:
            for n, (a, b, text) in enumerate(lines, 1):
                f.write(f"{n}\n{ws._fmt(a)} --> {ws._fmt(b)}\n{text.strip()}\n\n")
        return len(lines)


def _serve(engine):
    """ផ្ទុក model ម្តង ហើយទទួលការងារពី stdin (JSON មួយបន្ទាត់) រហូតដល់ stdin បិទ (កម្មវិធីបិទ)"""
    def say(**msg):
        print(json.dumps(msg, ensure_ascii=False), flush=True)

    eng = _Engine(engine, say)
    for raw in sys.stdin:
        try:
            req = json.loads(raw)
        except ValueError:
            continue
        try:
            say(done=eng.run(req["media"], req["out"], req.get("lang", ""), say))
        except Exception as e:  # noqa: BLE001 — ការងារនេះបរាជ័យ តែ process នៅរស់សម្រាប់រឿងបន្ទាប់
            say(error=str(e) or type(e).__name__)


# ---------------- process មេ ----------------
_servers = {}
_lock = threading.Lock()


def _model_mb():
    total = 0
    for root, _, files in os.walk(MODEL_DIR):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total / 1e6


def _server(engine):
    srv = _servers.get(engine)
    if srv and srv["proc"].poll() is None:
        return srv, False
    proc = subprocess.Popen([ws._python(), os.path.abspath(__file__), "--serve", engine],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=ws._NO_WINDOW)
    srv = _servers[engine] = {"proc": proc, "err": []}

    def drain():  # stderr (log របស់ FunASR) — រក្សាតែចុងក្រោយ សម្រាប់សារកំហុស
        for line in proc.stderr:
            srv["err"] = (srv["err"] + [line.decode("utf-8", "replace").rstrip()])[-40:]
    threading.Thread(target=drain, daemon=True).start()
    return srv, True


def _crash_text(srv):
    lines = [l for l in srv["err"] if l.strip()]
    return next((l for l in reversed(lines) if "Error" in l or "error" in l), lines[-1] if lines else "")


def start_job(media, out_srt, engine, lang):
    """→ job (srt_dub.jobs): download → load → running → done · job["seconds"], job["reused"] (model ផ្ទុករួច)"""
    from srt_dub import jobs
    job_id = uuid.uuid4().hex[:12]
    job = jobs[job_id] = {"status": "load", "done": 0, "total": 100, "error": None, "warnings": [],
                          "device": None, "lines": 0, "seconds": 0, "reused": False}

    def run():
        t0 = time.time()
        try:
            with _lock:  # ម្តងមួយការងារក្នុងមួយ process
                before = _model_mb()
                srv, fresh = _server(engine)
                job["reused"] = not fresh
                proc, state = srv["proc"], {"phase": "load" if fresh else "ready"}

                def watch_download():  # model ទាញយកលើកដំបូង → ភាគរយពីទំហំថត
                    while proc.poll() is None and state["phase"] == "load":
                        grown = _model_mb() - before
                        if grown > 5:
                            job.update(status="download", done=min(99, int(grown * 100 / ENGINES[engine][2])))
                        time.sleep(0.5)
                if fresh:
                    threading.Thread(target=watch_download, daemon=True).start()
                proc.stdin.write((json.dumps({"media": media, "out": out_srt, "lang": lang or ""},
                                             ensure_ascii=False) + "\n").encode("utf-8"))
                proc.stdin.flush()
                while True:
                    raw = proc.stdout.readline()
                    if not raw:  # process ស្លាប់
                        state["phase"] = "end"
                        proc.wait()
                        raise RuntimeError(_crash_text(srv) or f"exit {proc.returncode}")
                    try:
                        msg = json.loads(raw.decode("utf-8", "replace"))
                    except ValueError:
                        continue  # log
                    if msg.get("phase") in ("load", "ready"):
                        state["phase"] = msg["phase"]
                        job.update(status="load", device=msg.get("device"))
                    elif msg.get("phase") == "listen":
                        state["phase"] = "listen"
                        job.update(status="running", total=max(int(msg["total"]), 1), done=0, device=msg.get("device"))
                    elif "progress" in msg:
                        job["done"] = min(int(msg["progress"]), job["total"])
                    elif "error" in msg:
                        raise RuntimeError(msg["error"])
                    elif "done" in msg:
                        job["lines"] = msg["done"]
                        break
            job.update(done=job["total"], seconds=time.time() - t0, status="done")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "No module named" in msg:
                msg = "មិនទាន់ដំឡើង FunASR"
            job.update(status="error", error=f"{engine}: {msg}")

    threading.Thread(target=run, daemon=True).start()
    return job_id


def stop_servers():
    for srv in _servers.values():
        try:
            srv["proc"].kill()
        except OSError:
            pass
    _servers.clear()


if __name__ == "__main__":  # process កូន: funasr_srt.py --serve <sensevoice|paraformer>
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if sys.argv[1] == "--serve":
        _serve(sys.argv[2])
