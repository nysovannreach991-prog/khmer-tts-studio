"""ffmpeg — ទាញយក និងដំឡើងដោយស្វ័យប្រវត្តិ (មិត្តភក្តិមិនចាំបាច់ដំឡើងដោយខ្លួនឯង)។

ffmpeg.exe / ffprobe.exe ត្រូវរក្សាទុកក្នុង %LOCALAPPDATA%\\AITeam1\\ffmpeg ហើយបន្ថែមទៅ PATH
របស់កម្មវិធីពេលចាប់ផ្តើម — subprocess ទាំងអស់ ("ffmpeg", "ffprobe") រកឃើញវាដោយស្វ័យប្រវត្តិ។
"""
import os
import shutil
import tempfile
import urllib.request
import zipfile

BIN_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AITeam1", "ffmpeg")
URLS = [
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
]
NEEDED = ("ffmpeg.exe", "ffprobe.exe")
MISSING_MSG = ("រកមិនឃើញ ffmpeg — ចុច ▶ Preview វីដេអូ ឬប៊ូតុងដំណើរការណាមួយ "
               "ដើម្បីទាញយកដោយស្វ័យប្រវត្តិ")


def add_to_path(bin_dir=BIN_DIR):
    """ហៅពេលកម្មវិធីចាប់ផ្តើម — ffmpeg ដែលបានទាញយកមានអាទិភាពលើ PATH"""
    if all(os.path.isfile(os.path.join(bin_dir, n)) for n in NEEDED):
        parts = os.environ.get("PATH", "").split(os.pathsep)
        if bin_dir not in parts:
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


def available():
    return all(shutil.which(n.removesuffix(".exe")) for n in NEEDED)


def download(progress=None, bin_dir=BIN_DIR):
    """ទាញយក ffmpeg (~110MB) ហើយពន្លាតែ ffmpeg.exe + ffprobe.exe។ progress(done_bytes, total_bytes)"""
    errors = []
    for url in URLS:
        tmp = tempfile.mkdtemp(prefix="ffmpeg_dl_")
        try:
            zip_path = os.path.join(tmp, "ffmpeg.zip")
            req = urllib.request.Request(url, headers={"User-Agent": "AITeam1"})
            with urllib.request.urlopen(req, timeout=60) as resp, open(zip_path, "wb") as f:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    block = resp.read(1 << 17)
                    if not block:
                        break
                    f.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
            with zipfile.ZipFile(zip_path) as z:
                members = {os.path.basename(m): m for m in z.namelist() if m.lower().endswith(NEEDED)}
                if not all(n in members for n in NEEDED):
                    raise RuntimeError("zip មិនមាន ffmpeg.exe / ffprobe.exe")
                os.makedirs(bin_dir, exist_ok=True)
                for name in NEEDED:
                    with z.open(members[name]) as src, open(os.path.join(bin_dir, name + ".part"), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                for name in NEEDED:
                    os.replace(os.path.join(bin_dir, name + ".part"), os.path.join(bin_dir, name))
            add_to_path(bin_dir)
            return bin_dir
        except Exception as e:  # noqa: BLE001 — សាក URL បន្ទាប់
            errors.append(f"{url.split('/')[2]}: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    raise RuntimeError("ទាញយក ffmpeg មិនបាន — " + " | ".join(errors))
