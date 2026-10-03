"""Update ពី GitHub — ទាញយកតែឯកសារដែលបានផ្លាស់ប្តូរ (ពិនិត្យ SHA-256) ហើយជំនួសឯកសារចាស់។

GitHub repo ត្រូវបានកំណត់ក្នុង update_config.json ({"repo": "owner/name", "branch": "main"})។
publish.py បង្កើត manifest.json ({"version", "notes", "files": {path: sha256}}) រួច push ទៅ GitHub។
"""
import hashlib
import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(APP_DIR, "update_config.json")
MANIFEST_FILE = os.path.join(APP_DIR, "manifest.json")
PENDING_DIR = os.path.join(APP_DIR, ".update_pending")
TIMEOUT = 20


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        return cfg if cfg.get("repo") else None
    except (OSError, ValueError):
        return None


def local_manifest():
    try:
        with open(MANIFEST_FILE, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"version": "0", "files": {}}


def local_version():
    return local_manifest().get("version", "0")


def version_tuple(v):
    return tuple(int(x) if x.isdigit() else 0 for x in str(v).split("."))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "KhmerTTSStudio-Updater",
                                               "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def _latest_ref(cfg):
    """commit ចុងក្រោយនៃ branch — ទាញឯកសារតាម commit ដើម្បីកុំឱ្យជាប់ cache ចាស់របស់ GitHub"""
    try:
        data = json.loads(_get(f"https://api.github.com/repos/{cfg['repo']}/commits/{cfg.get('branch', 'main')}"))
        return data["sha"]
    except Exception:  # noqa: BLE001 — API អាចជាប់ rate limit → ប្រើ branch វិញ
        return cfg.get("branch", "main")


def _raw_url(cfg, ref, path):
    return f"https://raw.githubusercontent.com/{cfg['repo']}/{ref}/{urllib.parse.quote(path)}"


def check():
    """ត្រឡប់ {"available", "current", "latest", "notes", "ref", "remote", "changed"} ឬ raise RuntimeError"""
    cfg = load_config()
    if not cfg:
        raise RuntimeError("មិនទាន់កំណត់ GitHub repo (update_config.json) — សូមសួរអ្នកដែលផ្តល់កម្មវិធីនេះ")
    ref = _latest_ref(cfg)
    try:
        remote = json.loads(_get(_raw_url(cfg, ref, "manifest.json")))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"មិនអាចភ្ជាប់ទៅ GitHub បានទេ ({e})") from None
    local = local_manifest()
    changed = [p for p, h in remote.get("files", {}).items()
               if not os.path.isfile(os.path.join(APP_DIR, p)) or sha256_file(os.path.join(APP_DIR, p)) != h]
    newer = version_tuple(remote.get("version", "0")) > version_tuple(local.get("version", "0"))
    return {"available": newer, "current": local.get("version", "0"),
            "latest": remote.get("version", "0"), "notes": remote.get("notes", ""),
            "ref": ref, "remote": remote, "changed": changed}


def download_and_apply(info, progress=None):
    """ទាញយកឯកសារដែលផ្លាស់ប្តូរ → ពិនិត្យ SHA-256 → ជំនួស។ ត្រឡប់ចំនួនឯកសារដែលបានជំនួស។
    ឯកសារដែលកំពុងប្រើ (មិនអាចជំនួសបាន) ត្រូវទុកក្នុង .update_pending ហើយដាក់ពេលបើកកម្មវិធីលើកក្រោយ។"""
    cfg = load_config()
    remote, changed = info["remote"], info["changed"]
    staging = tempfile.mkdtemp(prefix="tts_update_")
    try:
        # 1. ទាញយកទាំងអស់ជាមុន — បើមួយបរាជ័យ មិនប៉ះពាល់ឯកសារណាមួយទេ
        for i, path in enumerate(changed, 1):
            data = _get(_raw_url(cfg, info["ref"], path))
            if hashlib.sha256(data).hexdigest() != remote["files"][path]:
                raise RuntimeError(f"{path} មិនត្រូវនឹង checksum — សូមព្យាយាមម្តងទៀតក្នុងពីរបីនាទី")
            dest = os.path.join(staging, path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            if progress:
                progress(i, len(changed), path)

        # 2. ជំនួសឯកសារ
        for path in changed:
            target = os.path.join(APP_DIR, path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                os.replace(os.path.join(staging, path), target)
            except PermissionError:
                pending = os.path.join(PENDING_DIR, path)
                os.makedirs(os.path.dirname(pending), exist_ok=True)
                shutil.move(os.path.join(staging, path), pending)

        # 3. លុបឯកសារដែលលែងមានក្នុងកំណែថ្មី (តែឯកសារដែលធ្លាប់ស្ថិតក្នុង manifest ចាស់ប៉ុណ្ណោះ)
        for path in set(local_manifest().get("files", {})) - set(remote.get("files", {})):
            try:
                os.remove(os.path.join(APP_DIR, path))
            except OSError:
                pass

        with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
            json.dump(remote, f, ensure_ascii=False, indent=2)
        return len(changed)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def is_dev_copy():
    """ថតរបស់ម្ចាស់កម្មវិធី (មាន .git) — កុំ Update ដោយស្វ័យប្រវត្តិ ក្រែងជាន់ការកែដែលមិនទាន់ publish"""
    return os.path.isdir(os.path.join(APP_DIR, ".git"))


def auto_update(log=print):
    """Update ស្ងាត់ៗ មិនសួរ — launcher ហៅមុនបើកកម្មវិធី (ឯកសារមិនទាន់ប្រើ → មិនចាំបាច់បើកឡើងវិញ)។
    គ្មានអ៊ីនធឺណិត / GitHub មិនឆ្លើយ → បើកកំណែបច្ចុប្បន្នធម្មតា។ ត្រឡប់ True បើបាន Update"""
    apply_pending()
    if not load_config():
        return False
    if is_dev_copy():
        log("    skipped (owner's copy - publish with publish_update.bat)")
        return False
    try:
        info = check()
    except Exception:  # noqa: BLE001
        log("    skipped (offline or GitHub not reachable)")
        return False
    if not info["available"]:
        log(f"    up to date (v{info['current']})")
        return False
    log(f"    v{info['current']} -> v{info['latest']} ({len(info['changed'])} files)")
    download_and_apply(info, lambda i, n, path: log(f"    [{i}/{n}] {path}"))
    apply_pending()
    log(f"    updated to v{info['latest']}")
    return True


def apply_pending():
    """ដាក់ឯកសារ Update ដែលមិនអាចជំនួសបានពេលមុន (ហៅពេលកម្មវិធីចាប់ផ្តើម មុន import អ្វីផ្សេង)"""
    if not os.path.isdir(PENDING_DIR):
        return
    for root, _, files in os.walk(PENDING_DIR):
        for name in files:
            src = os.path.join(root, name)
            rel = os.path.relpath(src, PENDING_DIR)
            try:
                os.makedirs(os.path.dirname(os.path.join(APP_DIR, rel)), exist_ok=True)
                os.replace(src, os.path.join(APP_DIR, rel))
            except OSError:
                pass
    shutil.rmtree(PENDING_DIR, ignore_errors=True)


if __name__ == "__main__":  # launcher: python updater.py --auto
    import sys
    if "--auto" in sys.argv:
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass
        try:
            auto_update()
        except Exception as e:  # noqa: BLE001 — Update បរាជ័យ មិនរារាំងការបើកកម្មវិធីទេ
            print(f"    update failed: {e}")
