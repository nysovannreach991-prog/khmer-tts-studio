"""បោះពុម្ព Update ទៅ GitHub — សម្រាប់ម្ចាស់កម្មវិធីប៉ុណ្ណោះ (ប្រើ publish_update.bat)។

1. បង្កើន version និងសរសេរ manifest.json (បញ្ជីឯកសារ + SHA-256)
2. git commit ហើយ push ទៅ GitHub → កម្មវិធីរបស់មិត្តភក្តិនឹងឃើញ Update ថ្មី
"""
import json
import os
import subprocess
import sys

import updater

APP_DIR = updater.APP_DIR
INCLUDE_EXT = {".py", ".bat", ".txt", ".html", ".ttf", ".otf", ".json", ".md", ".ico", ".png"}
EXCLUDE_DIRS = {"outputs", "__pycache__", ".git", ".update_pending", ".cache"}
EXCLUDE_FILES = {"manifest.json", "gui_error.log"}
GITIGNORE = "outputs/\n__pycache__/\n.update_pending/\n*.log\n*.ini\n"


def git(*args, check=True):
    proc = subprocess.run(["git", *args], cwd=APP_DIR, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    out = (proc.stdout + proc.stderr).strip()
    if out:
        print("   " + out.replace("\n", "\n   "))
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} បរាជ័យ")
    return proc


def ask(prompt, default=""):
    value = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    return value or default


def collect_files():
    files = {}
    for root, dirs, names in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for name in names:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, APP_DIR).replace(os.sep, "/")
            if name in EXCLUDE_FILES or os.path.splitext(name)[1].lower() not in INCLUDE_EXT:
                continue
            files[rel] = updater.sha256_file(path)
    return dict(sorted(files.items()))


def next_version(current):
    if current in ("", "0"):
        return "1.0.0"
    parts = [int(x) if x.isdigit() else 0 for x in current.split(".")]
    parts += [0] * (3 - len(parts))
    parts[-1] += 1
    return ".".join(map(str, parts))


def setup_repo():
    cfg = updater.load_config()
    if not cfg:
        user = git("config", "user.name", check=False).stdout.strip() or "username"
        repo = ask("GitHub repo (owner/name)", f"{user}/khmer-tts-studio")
        repo = repo.replace("https://github.com/", "").removesuffix(".git").strip("/")
        cfg = {"repo": repo, "branch": "main"}
        with open(updater.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        print(f"✓ បានរក្សាទុក update_config.json → {repo}")
    if not os.path.isdir(os.path.join(APP_DIR, ".git")):
        git("init", "-b", cfg["branch"])
        git("remote", "add", "origin", f"https://github.com/{cfg['repo']}.git")
    gitignore = os.path.join(APP_DIR, ".gitignore")
    if not os.path.exists(gitignore):
        with open(gitignore, "w", encoding="utf-8") as f:
            f.write(GITIGNORE)
    return cfg


def main():
    print("=== បោះពុម្ព Update ទៅ GitHub ===\n")
    cfg = setup_repo()
    current = updater.local_version()
    version = ask(f"Version (បច្ចុប្បន្ន {current})", next_version(current))
    notes = ask("អ្វីដែលថ្មីក្នុង Update នេះ (មិត្តភក្តិនឹងឃើញសារនេះ)", "កែលម្អ និងជួសជុលកំហុស")

    files = collect_files()
    manifest = {"version": version, "notes": notes, "files": files}
    with open(updater.MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\n✓ manifest.json — v{version} · {len(files)} ឯកសារ")

    git("add", "-A")
    git("commit", "-m", f"v{version}: {notes}", check=False)
    print("\nកំពុង push ទៅ GitHub (លើកដំបូងអាចបើក browser ឱ្យ login)...")
    try:
        git("push", "-u", "origin", cfg["branch"])
    except RuntimeError:
        print(f"\n✕ push បរាជ័យ។ សូមពិនិត្យថា៖\n"
              f"  1. បានបង្កើត repo ទទេនៅ https://github.com/new ឈ្មោះ '{cfg['repo'].split('/')[-1]}' (Public)\n"
              f"  2. បាន login GitHub ជាគណនី '{cfg['repo'].split('/')[0]}'\n"
              f"បន្ទាប់មកដំណើរការ publish_update.bat ម្តងទៀត។")
        return 1
    print(f"\n✔ រួចរាល់! v{version} ត្រូវបានបោះពុម្ពនៅ https://github.com/{cfg['repo']}\n"
          f"  កម្មវិធីរបស់មិត្តភក្តិនឹងឃើញ Update នេះពេលបើកលើកក្រោយ (ឬចុច ជំនួយ → ពិនិត្យ Update)។")
    return 0


if __name__ == "__main__":
    sys.exit(main())
