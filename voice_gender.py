"""ចាប់ភេទអ្នកនិយាយ (ស្រី/ប្រុស) តាមកម្ពស់សំឡេង (pitch, F0) — លើកុំព្យូទ័រផ្ទាល់ គ្មាន API។

ប្រើពេល Gemini មិនបានប្រាប់ភេទ (ឧ. server ផ្ទាល់ខ្លួន / Router មិនទទួលសំឡេង) ឬបន្ទាត់ដែលវាមិនប្រាកដ។
សំឡេងស្រី F0 ~165–260 Hz · ប្រុស ~85–155 Hz។ ល្អបំផុតលើសំឡេងនិយាយសុទ្ធ (Demucs vocals) តែដើរលើវីដេអូដើមដែរ។
"""
import numpy as np

SR = 16000
FRAME, HOP = 640, 160          # 40ms / 10ms
F_MIN, F_MAX = 70, 400         # Hz — ជួរសំឡេងមនុស្ស
# សាកលើរឿងចិន (62 បន្ទាត់ ធៀបនឹង Gemini): ស្រីទាំងអស់ ≥186 Hz · ប្រុសពេញវ័យ ≤187 Hz (ភាគច្រើន ≤150)
# ចន្លោះ 175–195 = មិនប្រាកដ → យកតាមបន្ទាត់ជិតខាង · ក្មេងប្រុស (>216 Hz) → "ស្រី" (សំឡេងខ្ពស់ សមជាង)
FEMALE_HZ, MALE_HZ = 195, 175
MIN_FRAMES = 6                 # frame ដែលមានសំឡេង (voiced) យ៉ាងតិច ទើបសម្រេច


def _f0_frames(x):
    """F0 (Hz) នៃ frame នីមួយៗដែលមានសំឡេង (autocorrelation) · NaN = គ្មានសំឡេង / ស្ងាត់"""
    if len(x) < FRAME:
        return np.zeros(0)
    n = (len(x) - FRAME) // HOP + 1
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[idx] * np.hanning(FRAME)
    energy = np.sqrt((frames ** 2).mean(axis=1))
    loud = energy > max(0.01, np.percentile(energy, 60) * 0.5)  # frame ដែលឮច្បាស់ប៉ុណ្ណោះ
    frames = frames - frames.mean(axis=1, keepdims=True)
    spec = np.fft.rfft(frames, 2 * FRAME, axis=1)
    ac = np.fft.irfft(spec * np.conj(spec), axis=1)[:, :FRAME]
    ac = ac / np.maximum(ac[:, :1], 1e-9)
    lo, hi = SR // F_MAX, SR // F_MIN
    seg = ac[:, lo:hi]
    lag = seg.argmax(axis=1) + lo
    peak = seg.max(axis=1)
    f0 = SR / lag
    voiced = loud & (peak > 0.55)
    # octave error (lag ទ្វេ → F0 ពាក់កណ្តាល): បើ ac នៅ lag/2 ក៏ខ្ពស់ដែរ → យក F0 ទ្វេ
    half = np.clip(lag // 2, lo, hi - 1)
    double = ac[np.arange(n), half] > 0.85 * peak
    f0 = np.where(double & (f0 * 2 <= F_MAX), f0 * 2, f0)
    return np.where(voiced, f0, np.nan)


def pitch(x):
    """median F0 (Hz) ឬ None បើសំឡេងតិចពេក"""
    f0 = _f0_frames(np.asarray(x, np.float32))
    f0 = f0[~np.isnan(f0)]
    return float(np.median(f0)) if len(f0) >= MIN_FRAMES else None


def classify(hz):
    if hz is None:
        return None
    return "female" if hz >= FEMALE_HZ else "male" if hz <= MALE_HZ else None


def detect(audio, cues, sr=SR):
    """audio = float32 mono (sr) · cues = [{start, end (ms)}] → ["female"/"male"/None ...]
    បន្ទាត់ដែលមិនប្រាកដ → យកតាមបន្ទាត់ជិតខាង (អ្នកនិយាយដដែលច្រើនតែនិយាយជាប់ៗគ្នា)"""
    out, hz = [], []
    for c in cues:
        a, b = int(c["start"] * sr / 1000), int(c["end"] * sr / 1000)
        h = pitch(audio[max(0, a):max(a, b)]) if b > a else None
        hz.append(h)
        out.append(classify(h))
    for i, g in enumerate(out):
        if g is None and hz[i] is not None:  # ចន្លោះ → ប្រៀបជាមួយបន្ទាត់ជិតខាងដែលច្បាស់
            near = [out[j] for j in (i - 1, i + 1, i - 2, i + 2) if 0 <= j < len(out) and out[j]]
            if near:
                out[i] = near[0]
    return out
