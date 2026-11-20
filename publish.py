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
INCLUDE_EXT = {".py", ".bat", ".txt", ".html", ".ttf", ".otf", ".json", ".md", ".ico", ".png", ".ps1"}
EXCLUDE_DIRS = {"outputs", "__pycache__", ".git", ".update_pending", ".cache", "build", "dist"}
EXCLUDE_FILES = {"manifest.json", "gui_error.log",
                 "license_admin.py", "license_admin.bat",
                 "publish.py", "publish_update.bat",
                 "build_installer.py", "build_installer.bat"}  # ឧបករណ៍ម្ចាស់ — មិនផ្តល់ឱ្យមិត្តភក្តិ
GITIGNORE = ("outputs/\n__pycache__/\n.update_pending/\n*.log\n*.ini\n"
             "license_admin.py\nlicense_admin.bat\n*.key\nbuild/\ndist/\n")


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
    # រក្សាទុកឯកសារដូចនៅលើកុំព្យូទ័រទាំងស្រុង (កុំឱ្យ git ប្តូរ CRLF → LF)
    # បើមិនដូច្នោះ SHA-256 ក្នុង manifest នឹងមិនត្រូវនឹងឯកសារនៅលើ GitHub ហើយ Update នឹងបរាជ័យ
    gitattributes = os.path.join(APP_DIR, ".gitattributes")
    if not os.path.exists(gitattributes):
        with open(gitattributes, "w", encoding="utf-8") as f:
            f.write("* -text\n")
    git("config", "core.autocrlf", "false")
    return cfg


def has_unpushed():
    """មាន commit ដែលមិនទាន់ push (ឧ. login បរាជ័យលើកមុន) ហើយគ្មានការកែប្រែថ្មី"""
    if git("status", "--porcelain", check=False).stdout.strip():
        return False
    ahead = git("rev-list", "--count", "@{u}..HEAD", check=False)
    if ahead.returncode == 0:
        return ahead.stdout.strip() not in ("", "0")
    return git("rev-parse", "HEAD", check=False).returncode == 0  # មិនទាន់ធ្លាប់ push ទាល់តែសោះ


def push(cfg):
    """push ទៅ GitHub — បើ login តាម browser មិនដំណើរការ ផ្តល់ជម្រើសប្រើ Token"""
    print("\nកំពុង push ទៅ GitHub (លើកដំបូងអាចបើក browser ឱ្យ login)...")
    for attempt in range(2):
        try:
            git("push", "-u", "origin", cfg["branch"])
            return True
        except RuntimeError:
            if attempt == 1:
                break
            print("\n✕ push បរាជ័យ។ បើប៊ូតុង Authorize ក្នុង browser មិនអាចចុចបាន អាចប្រើ Token ជំនួសវិញ។")
            if ask("ប្រើ Token ជំនួស browser? (y/n)", "y").lower() != "y":
                break
            url = "https://github.com/settings/tokens/new?scopes=repo&description=khmer-tts-studio"
            print("\n  1. ទំព័រ GitHub នឹងបើក → ចុចប៊ូតុងពណ៌បៃតង 'Generate token' នៅខាងក្រោម\n"
                  "  2. Copy token (ចាប់ផ្តើមដោយ ghp_...)\n"
                  "  3. ត្រឡប់មកទីនេះ → ផ្ទាំងតូចមួយនឹងលេចឡើង → ជ្រើស 'Token' ហើយ Paste token\n")
            os.startfile(url)
            input("ចុច Enter ពេល Copy token រួច...")
            git("config", "credential.gitHubAuthModes", "pat")  # តែ repo នេះប៉ុណ្ណោះ
    print(f"\n✕ push បរាជ័យ។ សូមពិនិត្យថា៖\n"
          f"  1. មាន repo នៅ https://github.com/{cfg['repo']} (Public)\n"
          f"  2. បាន login GitHub ជាគណនី '{cfg['repo'].split('/')[0]}'\n"
          f"បន្ទាប់មកដំណើរការ publish_update.bat ម្តងទៀត (កំណែដដែលនឹងត្រូវ upload មិនបង្កើនលេខទេ)។")
    return False


def main():
    print("=== បោះពុម្ព Update ទៅ GitHub ===\n")
    cfg = setup_repo()
    current = updater.local_version()
    if has_unpushed():
        print(f"v{current} បាន commit រួចហើយ ប៉ុន្តែមិនទាន់ upload — កំពុង upload ម្តងទៀត...")
        if not push(cfg):
            return 1
        print(f"\n✔ រួចរាល់! v{current} ត្រូវបានបោះពុម្ពនៅ https://github.com/{cfg['repo']}")
        return 0
    version = ask(f"Version (បច្ចុប្បន្ន {current})", next_version(current))
    notes = ask("អ្វីដែលថ្មីក្នុង Update នេះ (មិត្តភក្តិនឹងឃើញសារនេះ)", "កែលម្អ និងជួសជុលកំហុស")

    files = collect_files()
    manifest = {"version": version, "notes": notes, "files": files}
    with open(updater.MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\n✓ manifest.json — v{version} · {len(files)} ឯកសារ")

    git("add", "-A")  # មុន renormalize — បើមិនដូច្នេះ ឯកសារដែលបានលុប (run.bat) ធ្វើឲ្យ git បរាជ័យ
    git("add", "--renormalize", ".")
    git("commit", "-m", f"v{version}: {notes}", check=False)
    if not push(cfg):
        return 1
    print(f"\n✔ រួចរាល់! v{version} ត្រូវបានបោះពុម្ពនៅ https://github.com/{cfg['repo']}\n"
          f"  កម្មវិធីរបស់មិត្តភក្តិនឹងឃើញ Update នេះពេលបើកលើកក្រោយ (ឬចុច ជំនួយ → ពិនិត្យ Update)។")
    return 0


if __name__ == "__main__":
    sys.exit(main())
