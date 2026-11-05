"""✨ Simple Auto — Folder រឿង (Part ច្រើន) → Merge → SRT (SenseVoice) → បកប្រែខ្មែរ (Bayon/Gemini) →
សំឡេង Edge TTS → វីដេអូ។

ធ្វើម្តងមួយរឿង (មិនស្របគ្នា) — model ស្តាប់ / លុបការនិយាយ មិនប្រជែង GPU គ្នា (VRAM ពេញ → Windows ប្រើ RAM
ជំនួស ហើយយឺតខ្លាំងដូចគាំង)។ GUI អាន state[] (មិនមែន thread-safe ពេញលេញ តែគ្រប់គ្រាន់សម្រាប់បង្ហាញ)។
"""
import os
import re
import shutil
import tempfile
import threading
import time
import traceback

import funasr_srt
import srt_dub
import transcribe
import video_dub
import video_merge
import vocal_remove
import voice_gender
import whisper_srt

WHISPER_MODEL = "large-v3-turbo"  # GPU: លឿន និងច្បាស់ជាង SenseVoice (ពាក្យខ្លីៗ មិនបាត់)

STAGES = [("merge", "🧩 Merge"), ("asr", "📝 SRT"), ("translate", "🌐 ខ្មែរ"), ("tts", "🔊 សំឡេង"),
          ("video", "🎬 វីដេអូ")]
WEIGHTS = {"merge": 5, "asr": 15, "translate": 15, "tts": 35, "video": 30}  # សម្រាប់ % សរុបនៃរឿងមួយ
_KHMER = re.compile(r"[ក-៿]")
_CJK = re.compile(r"[㐀-鿿぀-ヿ가-힯]")


def _read_srt(path):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return srt_dub.parse_srt(f.read())


def _poll(job_id, on_tick, stop):
    """រង់ចាំ job (srt_dub.jobs) ចប់ — on_tick(job) រាល់ 0.3វិ"""
    while True:
        job = srt_dub.jobs[job_id]
        on_tick(job)
        if job["status"] == "done":
            return job
        if job["status"] == "error":
            raise RuntimeError(job["error"])
        if stop.is_set():
            raise InterruptedError
        time.sleep(0.3)


def _write_srt(path, cues, field="text"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for i, c in enumerate(cues, 1):
            # "(ស្រី) ..." / "(ប្រុស) ..." — srt_dub.parse_srt អានភេទវិញ (ប្រើឡើងវិញពេលក្រោយ មិនសួរ Gemini ម្តងទៀត)
            label = transcribe.GENDER_LABEL.get(c.get("gender") or "")
            text = f"({label}) {c[field]}" if label else c[field]
            f.write(f"{i}\n{transcribe._fmt(c['start'] / 1000)} --> {transcribe._fmt(c['end'] / 1000)}\n{text}\n\n")


class Pipeline:
    def __init__(self, stories, opts):
        """stories = [{"folder", "videos", "srts"}] · opts: key, model, engine, lang, dub (dict សម្រាប់
        backend._dub_setup ដោយគ្មាន cues), dub_setup (function), out_dir, orig_volume, part_sec, overlays, ads,
        keep_full"""
        self.stories, self.opts = stories, opts
        self.state = [{"name": os.path.basename(s["folder"]), "status": "waiting", "stage": None, "stage_pct": 0,
                       "pct": 0, "msg": "", "out": None, "lines": 0, "t0": None, "t1": None, "notes": []}
                      for s in stories]
        self.stop = threading.Event()
        self.t0 = time.time()
        self.finished = False
        self.log = []  # (ពេល, សារ) — GUI បង្ហាញ
        self.sep = {}  # រឿង → (job លុបការនិយាយ, ឯកសារភ្លេង)
        self.vocals = {}  # រឿង → សំឡេងនិយាយសុទ្ធ (Demucs) — Gemini ស្តាប់ភេទច្បាស់ជាង

    # ---------- progress ----------
    def _set(self, i, stage, pct, msg=None):
        st = self.state[i]
        pct = max(0, min(100, int(pct)))
        if (stage, pct, msg) != (st["stage"], st["stage_pct"], st["msg"] if msg is None else msg):
            st["since"] = time.time()  # វឌ្ឍនភាពចុងក្រោយ — GUI បង្ហាញ "⏳ កំពុងរង់ចាំ Xs" ពេលយូរ
        st["stage"], st["stage_pct"] = stage, pct
        if msg is not None:
            st["msg"] = msg
        order = [k for k, _ in STAGES]
        done = sum(WEIGHTS[k] for k in order[:order.index(stage)])
        st["pct"] = int(done + WEIGHTS[stage] * st["stage_pct"] / 100)

    def _say(self, msg):
        self.log.append((time.time(), msg))

    def overall(self):
        """% សរុបនៃរឿងទាំងអស់"""
        if not self.state:
            return 100
        return int(sum(100 if s["status"] in ("done", "failed") else s["pct"] for s in self.state) / len(self.state))

    # ---------- run ----------
    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        """ម្តងមួយរឿង: Merge → SRT → ខ្មែរ → សំឡេង → វីដេអូ ហើយទើបរឿងបន្ទាប់"""
        try:
            for i in range(len(self.stories)):
                if self.stop.is_set():
                    break
                st = self.state[i]
                st.update(status="running", t0=time.time(), since=time.time())
                tmp = tempfile.mkdtemp(prefix="simple_")
                try:
                    video, cues = self._prepare(i, tmp)
                    if self.stop.is_set():
                        raise InterruptedError
                    self._dub(i, video, cues)
                    st.update(status="done", t1=time.time())
                    self._say(f"✓ {st['name']}: {st['msg']}")
                except InterruptedError:
                    st.update(status="waiting", msg="បានបញ្ឈប់")
                    break
                except Exception as e:  # noqa: BLE001
                    st.update(status="failed", msg=str(e)[-300:], t1=time.time(),
                              trace=traceback.format_exc()[-3000:])
                    self._say(f"✕ {st['name']}: {str(e)[-200:]}")
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
        finally:
            for st in self.state:  # ឈប់មុនដល់ → រឿងដែលមិនទាន់ធ្វើ
                if st["status"] == "running":
                    st.update(status="waiting", msg="បានបញ្ឈប់")
            self.finished = True

    # ---------- ជំហាន 1–3: Merge → SRT → ខ្មែរ ----------
    def _prepare(self, i, tmp):
        story, st, o = self.stories[i], self.state[i], self.opts
        name, folder, videos = st["name"], story["folder"], story["videos"]
        # 1) Merge — ចម្លងផ្ទាល់ (លឿន) ប្រើទាំងសម្រាប់ស្តាប់ និងសម្រាប់វីដេអូចុងក្រោយ
        if len(videos) > 1:
            self._set(i, "merge", 0, f"Merge {len(videos)} Part...")
            job = video_merge.start_job(videos, tmp, "merged", False, [])
            m = _poll(job, lambda j: self._set(i, "merge", j["done"]), self.stop)
            video = os.path.join(tmp, m["file"])
            if m.get("mode") == "reencode":
                st["notes"].append("Part មានទម្រង់ខុសគ្នា → encode ឡើងវិញ")
        else:
            video = videos[0]
        self._set(i, "merge", 100, "")

        # 2) SRT — មានស្រាប់ក្នុង Folder? (ខ្មែរ → ប្រើភ្លាម · ភាសាដើម → បកប្រែតែប៉ុណ្ណោះ)
        cues = self._existing_srt(story)
        km_path = os.path.join(folder, f"{name}.srt")
        orig_path = os.path.join(folder, "SRT_ភាសាដើម", f"{name}.srt")
        if cues and _KHMER.search("".join(c["text"] for c in cues[:30])):
            st["notes"].append("ប្រើ SRT ខ្មែរដែលមានស្រាប់")
            self._set(i, "translate", 100, f"SRT ខ្មែរមានស្រាប់ ({len(cues)} បន្ទាត់)")
            st["lines"] = len(cues)
            self._start_novocal(i, video, tmp)
            return video, cues
        if not cues and os.path.exists(orig_path):  # បានស្តាប់រួចពីមុន (ឧ. បកប្រែបរាជ័យ)
            cues = _read_srt(orig_path)
        if cues:
            st["notes"].append("ប្រើ SRT ភាសាដើមដែលមានស្រាប់ (មិនស្តាប់ម្តងទៀត)")
            self._set(i, "asr", 100, f"SRT ភាសាដើមមានស្រាប់ ({len(cues)} បន្ទាត់)")
        else:
            out = os.path.join(tmp, "orig.srt")
            asr_src = self._clean_voice(i, video, tmp) if o.get("clean_voice") or o.get("novocal") else video
            self._set(i, "asr", 0, "កំពុងបើក model ស្តាប់...")
            if o["engine"] in ("whisper", "gemini"):  # process ដាច់ដោយឡែក → ចប់ហើយ VRAM ទំនេរវិញ
                job = whisper_srt.start_job(asr_src, out, WHISPER_MODEL, o["lang"], o.get("gpu", True))
            else:
                job = funasr_srt.start_job(asr_src, out, o["engine"], o["lang"])

            def tick(j):
                if j["status"] == "download" and not j["done"]:
                    self._set(i, "asr", 0, "កំពុងបើក model ស្តាប់...")
                elif j["status"] == "download":
                    self._set(i, "asr", 0, f"ទាញយក model (ម្តងគត់) {j['done']}%")
                elif j["status"] == "load":
                    self._set(i, "asr", 0, "កំពុងបើក model ស្តាប់...")
                else:
                    self._set(i, "asr", 100 * j["done"] / max(j["total"], 1),
                              f"កំពុងស្តាប់ ({'GPU' if 'cuda' in (j.get('device') or '') else 'CPU'})")
            j = _poll(job, tick, self.stop)
            st["notes"] += j.get("warnings", [])[:1]  # ឧ. GPU ប្រើមិនបាន → CPU
            if o["engine"] not in ("whisper", "gemini") and o.get("novocal"):
                funasr_srt.stop_servers()  # ដោះ VRAM ឱ្យ Demucs
            cues = _read_srt(out)
            if not cues:
                raise RuntimeError("រកមិនឃើញការនិយាយក្នុងវីដេអូ")
            try:  # រក្សាទុកភាសាដើម — ពេលក្រោយមិនចាំបាច់ស្តាប់ម្តងទៀត
                _write_srt(orig_path, cues)
            except OSError:
                pass

        self._start_novocal(i, video, tmp)  # ស្របពេលជាមួយការបកប្រែ + TTS (បណ្តាញ មិនប្រើ GPU)

        from app import gemini_post, hears_audio
        if o["engine"] == "gemini":  # 3) ភេទ ស្រី/ប្រុស + កែពាក្យ + បកប្រែ
            voice_src = self.vocals.get(i) or video
            if hears_audio(o["key"]):  # Gemini ស្តាប់សំឡេង (Relay / Bayon / Google) — ម្តងតែមួយ
                def window_done(done, total):
                    self._set(i, "translate", 100 * done / total, f"Gemini ស្តាប់ + ចាប់ភេទ + បកប្រែ {done}/{total}")
                self._set(i, "translate", 0, f"Gemini ស្តាប់ + ចាប់ភេទ ស្រី/ប្រុស ({len(cues)} បន្ទាត់)...")
                km = transcribe.gemini_voice_lines(voice_src, cues, "km", o["key"], o["model"], gemini_post,
                                                   on_window=window_done)
            else:  # Server ផ្ទាល់ខ្លួន (Router) មិនបាន upload សំឡេងទៅ Gemini → បកប្រែអត្ថបទ · ភេទតាម pitch ខាងក្រោម
                st["notes"].append("Server ផ្ទាល់ខ្លួនមិនស្តាប់សំឡេង — ចាប់ភេទតាមកម្ពស់សំឡេង (pitch) លើកុំព្យូទ័រ")
                km = self._translate_text(i, cues, o, gemini_post)
            if any(c["gender"] is None for c in km):  # Gemini មិនប្រាប់ (ឬមិនប្រាកដ) → pitch លើកុំព្យូទ័រផ្ទាល់
                self._set(i, "translate", 99, "ចាប់ភេទតាមកម្ពស់សំឡេង...")
                try:
                    audio = whisper_srt._load_audio(voice_src)
                    if isinstance(audio, str):
                        raise RuntimeError("ffmpeg")
                    for c, g in zip(km, voice_gender.detect(audio, km)):
                        if c["gender"] is None:
                            c["gender"] = g
                except Exception as e:  # noqa: BLE001 — គ្មានភេទ → សំឡេងដែលជ្រើស (មិនបញ្ឈប់រឿង)
                    st["notes"].append(f"ចាប់ភេទតាម pitch មិនបាន ({str(e)[:60]})")
            try:
                _write_srt(orig_path, km, "original")
                _write_srt(km_path, km)
            except OSError:
                pass
            st["lines"] = len(km)
            female = sum(c["gender"] == "female" for c in km)
            male = sum(c["gender"] == "male" for c in km)
            st["notes"].append(f"👩 ស្រី {female} · 👨 ប្រុស {male} បន្ទាត់")
            self._set(i, "translate", 100, f"បកប្រែរួច {len(km)} បន្ទាត់ (👩 {female} · 👨 {male})")
            return video, km

        km = self._translate_text(i, cues, o, gemini_post)  # 3) បកប្រែអត្ថបទ (ដុំៗស្របគ្នា)
        try:
            _write_srt(km_path, km)
        except OSError:
            pass
        st["lines"] = len(km)
        self._set(i, "translate", 100, f"បកប្រែរួច {len(km)} បន្ទាត់")
        return video, km

    def _translate_text(self, i, cues, o, gemini_post):
        """បកប្រែបន្ទាត់ (អត្ថបទ) → cue ដដែល + original · gender រក្សាដូចដើម (None = មិនដឹង)"""
        total = -(-len(cues) // transcribe.TRANSLATE_BATCH)
        count = [0]

        def batch_done():
            count[0] += 1
            self._set(i, "translate", 100 * count[0] / total, f"កំពុងបកប្រែ {count[0]}/{total} ដុំ")
        self._set(i, "translate", 0, f"កំពុងបកប្រែ {len(cues)} បន្ទាត់...")
        texts = transcribe.translate_lines([c["text"] for c in cues], "km", o["key"], o["model"], gemini_post,
                                           on_batch=batch_done)
        return [dict(c, original=c["text"], text=t, gender=c.get("gender")) for c, t in zip(cues, texts)]

    def _clean_voice(self, i, video, tmp):
        """បំបែកសំឡេងនិយាយចេញពីភ្លេង (Demucs) មុនស្តាប់ — ចាប់បានការនិយាយតាមទូរស័ព្ទ / តិចៗក្រោមភ្លេង
        (សាក: សំឡេងទូរស័ព្ទក្រោមភ្លេង 5 → 22 បន្ទាត់) · ភ្លេង (គ្មានការនិយាយ) ប្រើឡើងវិញសម្រាប់ "លុបតែការនិយាយ"
        បរាជ័យ → ស្តាប់វីដេអូដើម (មិនបញ្ឈប់រឿង)"""
        bg = os.path.join(tmp, "novocal.flac")

        def tick(j):
            if j["status"] == "download":
                self._set(i, "asr", 0, f"ទាញយក model បំបែកសំឡេង (ម្តងគត់) {j['done']}%")
            elif j["status"] == "running":
                self._set(i, "asr", 0, f"បំបែកសំឡេងនិយាយចេញពីភ្លេង {100 * j['done'] // max(j['total'], 1)}%")
        try:
            job = vocal_remove.start_job(video, bg, keep_vocals=True)
            vocal_remove.wait(job, tick, self.stop)
        except InterruptedError:
            raise
        except Exception as e:  # noqa: BLE001
            self.state[i]["notes"].append(f"បំបែកសំឡេងមិនបាន ({str(e)[:80]}) — ស្តាប់វីដេអូដើម")
            return video
        if self.opts.get("novocal"):
            self.sep[i] = (job, bg)  # ចប់រួចហើយ → _start_novocal មិនធ្វើម្តងទៀត
        self.vocals[i] = os.path.splitext(bg)[0] + "_vocals.wav"
        return self.vocals[i]

    def _start_novocal(self, i, video, tmp):
        """លុបការនិយាយ (GPU) ក្រោយការស្តាប់ចប់ — រង់ចាំវាពេលដាក់ចូលវីដេអូ"""
        if self.opts.get("novocal") and i not in self.sep:
            bg = os.path.join(tmp, "novocal.flac")
            self.sep[i] = (vocal_remove.start_job(video, bg), bg)

    @staticmethod
    def _existing_srt(story):
        """SRT ក្នុង Folder: មួយ → ប្រើ · មួយក្នុងមួយ Part → ភ្ជាប់ដោយបន្ថែមរយៈពេល Part មុនៗ"""
        srts, videos = story["srts"], story["videos"]
        if not srts:
            return None
        if len(srts) > 1 and len(srts) == len(videos):
            cues, offset = [], 0
            for srt, video in zip(srts, videos):
                for c in _read_srt(srt):
                    cues.append(dict(c, start=c["start"] + offset, end=c["end"] + offset, index=len(cues) + 1))
                offset += int(video_dub.probe(video)[0] * 1000)
            return cues
        return _read_srt(srts[0])

    # ---------- ជំហាន 4–5: សំឡេង → វីដេអូ ----------
    def _dub(self, i, video, cues):
        st, o = self.state[i], self.opts
        args = o["dub_setup"](dict(o["dub"], cues=[dict(c, index=n) for n, c in enumerate(cues, 1)]))
        name_base = f"{o['safe_name'](st['name']) or 'video'}_dub_{time.strftime('%Y%m%d_%H%M%S')}"

        def post(job, pcm):
            bg = None
            if i in self.sep:
                sub, path = self.sep[i]
                bg = video_dub.separate_vocals(video, path, job, sub)
            job["folder"], job["parts"] = video_dub.mix_and_split(
                video, pcm, args["cues"], o["out_dir"], name_base, o["orig_volume"], o["part_sec"], job,
                o["overlays"], o["ads"], o["keep_full"], bg_audio=bg, duck=o.get("duck", 0))

        self._set(i, "tts", 0, f"កំពុងបញ្ចូលសំឡេង 0/{len(args['cues'])}")
        job = srt_dub.start_job(**args, post=post)

        def tick(j):
            if j["status"] == "running":
                self._set(i, "tts", 100 * j["done"] / max(j["total"], 1),
                          f"កំពុងបញ្ចូលសំឡេង {j['done']}/{j['total']}")
            elif j["status"] == "mixing":
                self._set(i, "tts", 100, "កំពុងតម្រឹមពេលវេលា...")
            elif j["status"] == "separating":
                self._set(i, "video", 0, j.get("sep_msg") or "កំពុងលុបការនិយាយ...")
            elif j["status"] == "video":
                self._set(i, "video", j.get("stage_pct", 0), "កំពុងដាក់សំឡេងចូលវីដេអូ" +
                          (" + កាត់ជាផ្នែក" if o["part_sec"] else "") + "...")
        j = _poll(job, tick, self.stop)
        st["out"] = os.path.join(o["out_dir"], j["folder"])
        parts = len(j.get("parts") or [])
        # វីដេអូពេញ (សម្រាប់ Upload ទៅ Google Drive): _full.mp4 ពេលកាត់ជាផ្នែក ឬវីដេអូតែមួយ
        if j.get("full"):
            st["full_file"] = os.path.join(o["out_dir"], j["full"])
        elif parts == 1:
            st["full_file"] = os.path.join(o["out_dir"], j["parts"][0]["path"])
        self._set(i, "video", 100, f"{parts} ផ្នែក · {st['lines']} បន្ទាត់" if parts > 1 else f"{st['lines']} បន្ទាត់")
        # ការព្រមានសំខាន់ៗប៉ុណ្ណោះ (មិនមែន "ពន្យារ 0.0s" ដែលមិនមានន័យ)
        st["notes"] += [w for w in j.get("warnings", []) if "ត្រូវបានពន្យារ" not in w][:3]
