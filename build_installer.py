"""បង្កើតកម្មវិធីដំឡើង AITeam1_Setup_vX.exe សម្រាប់ផ្ញើឱ្យមិត្តភក្តិ — សម្រាប់ម្ចាស់កម្មវិធីប៉ុណ្ណោះ (build_installer.bat)។

មិត្តភក្តិ: ចុចពីរដង → Install → រួចរាល់ (គ្មាន admin, ដំឡើង Python + library + ffmpeg ដោយស្វ័យប្រវត្តិ,
បង្កើត shortcut លើ Desktop)។ បន្ទាប់មក កម្មវិធី Update ខ្លួនឯងពី GitHub រាល់ពេលបើក។
ត្រូវការ Inno Setup 6 (https://jrsoftware.org/isinfo.php ឬ winget install JRSoftware.InnoSetup)។
"""
import os
import shutil
import subprocess
import sys

import gemini_direct
import publish
import updater

APP_DIR = updater.APP_DIR
BUILD_DIR = os.path.join(APP_DIR, "build")
STAGING = os.path.join(BUILD_DIR, "staging")
DIST_DIR = os.path.join(APP_DIR, "dist")
APP_ID = "{{6B1D3C52-8F4E-4E0B-9A57-2F1C9E7A41D3}"  # កុំប្តូរ — Windows ស្គាល់ថាជាកម្មវិធីដដែលពេលដំឡើងកំណែថ្មីពីលើ
ISCC_PATHS = [
    os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Inno Setup 6", "ISCC.exe"),
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Inno Setup 6", "ISCC.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
]

# Gemini Router engine ដែលដាក់ក្នុង Setup → {app}\gemini_router (មិនយក profiles / settings.json / telemetry = ទិន្នន័យ និង key របស់ម្ចាស់)
ROUTER_FILES = ("engine.py", "telemetry_db.py", "captcha_solver.py", "login_chrome.py", "manual_chrome.py")
ROUTER_REPO = os.path.join(APP_DIR, publish.ROUTER_DIR)  # ច្បាប់ចម្លងក្នុង repo → ទៅ GitHub / Update / Setup
ROUTER_REQ = "playwright==1.64.1\nfastapi>=0.110\nuvicorn>=0.28\npydantic>=2.0\n"  # playwright ចាស់ជាង 1.64 គាំង

PS = r"{sys}\WindowsPowerShell\v1.0\powershell.exe"
QUIET = r'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\launcher.ps1"" -Quiet'
SETUP = r'-NoProfile -ExecutionPolicy Bypass -File ""{app}\launcher.ps1"" -Setup'

ISS = r"""; បង្កើតដោយ build_installer.py — កុំកែដោយដៃ
[Setup]
AppId={app_id}
AppName=AI Team #1
AppVersion={version}
AppVerName=AI Team #1 v{version}
AppPublisher=AI Team #1
DefaultDirName={{localappdata}}\AITeam1\app
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={dist}
OutputBaseFilename={out_name}
SetupIconFile={app_dir}\icon.ico
UninstallDisplayIcon={{app}}\icon.ico
UninstallDisplayName=AI Team #1
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel2=This will install AI Team #1 v{version}.%n%nការដំឡើងនឹងទាញយក Python, library និង ffmpeg ដោយស្វ័យប្រវត្តិ (ត្រូវការអ៊ីនធឺណិត ប្រហែល 5–10 នាទីលើកដំបូង)។%n%nបន្ទាប់ពីនេះ កម្មវិធីនឹង Update ខ្លួនឯងរាល់ពេលបើក។
FinishedLabel=AI Team #1 is ready. Use the "AI Team #1" icon on your Desktop.%n%nដំឡើងរួចរាល់ — ចុច icon "AI Team #1" លើ Desktop ដើម្បីបើក។

[Files]
Source: "{staging}\*"; DestDir: "{{app}}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{{autodesktop}}\AI Team #1"; Filename: "{ps}"; Parameters: "{quiet}"; WorkingDir: "{{app}}"; IconFilename: "{{app}}\icon.ico"; Flags: runminimized
Name: "{{autoprograms}}\AI Team #1"; Filename: "{ps}"; Parameters: "{quiet}"; WorkingDir: "{{app}}"; IconFilename: "{{app}}\icon.ico"; Flags: runminimized

[Run]
Filename: "{ps}"; Parameters: "{setup}"; WorkingDir: "{{app}}"; StatusMsg: "Installing Python, libraries and ffmpeg (first time: about 5-10 minutes)..."; Flags: waituntilterminated
Filename: "{ps}"; Parameters: "{quiet}"; WorkingDir: "{{app}}"; Description: "Open AI Team #1"; Flags: postinstall nowait skipifsilent runminimized

[UninstallDelete]
Type: filesandordirs; Name: "{{app}}"
"""


def find_iscc():
    found = next((p for p in ISCC_PATHS if os.path.isfile(p)), None) or shutil.which("ISCC")
    if not found:
        raise RuntimeError("រកមិនឃើញ Inno Setup 6 — ដំឡើងពី https://jrsoftware.org/isinfo.php "
                           "(ឬ: winget install JRSoftware.InnoSetup) ហើយដំណើរការម្តងទៀត")
    return found


def bundle_router(dest):
    """ចម្លង engine របស់ Gemini Router ទៅ staging · ត្រឡប់ថតប្រភព ឬ "" បើរកមិនឃើញ"""
    src = os.environ.get("GEMINI_ROUTER_DIR", "")
    if not gemini_direct.is_valid(src):
        try:
            from PyQt5.QtCore import QSettings
            src = str(QSettings("KhmerTTS", "Studio").value("router_dir", "") or "")
        except Exception:  # noqa: BLE001
            src = ""
    if not gemini_direct.is_valid(src) or os.path.normcase(os.path.abspath(src)) == os.path.normcase(ROUTER_REPO):
        raise RuntimeError("កំណត់ Folder Gemini Router ក្នុង ⚙ Settings ឬ GEMINI_ROUTER_DIR ជាមុនសិន")
    os.makedirs(dest, exist_ok=True)
    for name in ROUTER_FILES:
        path = os.path.join(src, name)
        if not os.path.isfile(path):
            raise RuntimeError(f"Gemini Router ខ្វះ {name} ({src})")
        if name == "engine.py":
            with open(path, encoding="utf-8") as f:
                code = f.read()
            # កម្មវិធីរត់ដោយ pythonw (គ្មាន console) — ផ្ទាំងចូល Google ត្រូវការ python.exe
            old = "py_exe = sys.executable\n"
            if old not in code:
                raise RuntimeError("engine.py ផ្លាស់ប្តូរ — រកមិនឃើញ 'py_exe = sys.executable'")
            code = code.replace(old, 'py_exe = sys.executable.replace("pythonw.exe", "python.exe")\n')
            with open(os.path.join(dest, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(code)
        else:
            shutil.copy2(path, os.path.join(dest, name))
    with open(os.path.join(dest, "requirements.txt"), "w", encoding="ascii") as f:
        f.write(ROUTER_REQ)
    return src


def main():
    yes = "--yes" in sys.argv
    if "--sync-router" in sys.argv:  # យក engine ថ្មីពី Folder Gemini Router មកដាក់ក្នុង repo (មុន publish)
        src = bundle_router(ROUTER_REPO)
        print(f"✓ gemini_router ← {src}" if src else "✕ រកមិនឃើញ Folder Gemini Router")
        return 0 if src else 1
    version = updater.local_version()
    files = publish.collect_files()
    published = updater.local_manifest().get("files", {})
    unpublished = sorted(p for p, h in files.items() if published.get(p) != h)
    if unpublished:
        print("⚠ ឯកសារខាងក្រោមមិនទាន់ publish ទៅ GitHub ទេ:")
        for p in unpublished[:15]:
            print(f"   • {p}")
        print("  ល្អបំផុត: ដំណើរការ publish_update.bat ជាមុនសិន (ដើម្បីឱ្យ Setup និង GitHub ដូចគ្នា)")
        if not yes and input("  បន្តបង្កើត Setup ដដែល? (y/N): ").strip().lower() != "y":
            return 1

    iscc = find_iscc()
    shutil.rmtree(STAGING, ignore_errors=True)
    for rel in [*files, "manifest.json"]:
        src = os.path.join(APP_DIR, rel)
        if os.path.isfile(src):
            dst = os.path.join(STAGING, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    for owner_only in publish.EXCLUDE_FILES | {"build_installer.py", "build_installer.bat"}:
        assert not os.path.exists(os.path.join(STAGING, owner_only)) or owner_only == "manifest.json", owner_only

    router = os.path.isfile(os.path.join(STAGING, publish.ROUTER_DIR, "engine.py"))
    print("✓ Gemini Router engine នៅក្នុង Setup" if router else "⚠ Setup នេះគ្មាន engine — python build_installer.py --sync-router")
    out_name = f"AITeam1_Setup_v{version}"
    os.makedirs(DIST_DIR, exist_ok=True)
    iss = os.path.join(BUILD_DIR, "AITeam1.iss")
    with open(iss, "w", encoding="utf-8-sig") as f:  # BOM → Inno អានជា UTF-8 (អក្សរខ្មែរ)
        f.write(ISS.format(app_id=APP_ID, version=version, dist=DIST_DIR, app_dir=APP_DIR, staging=STAGING,
                           ps=PS, quiet=QUIET, setup=SETUP, out_name=out_name))
    print(f"🔨 កំពុងបង្កើត Setup v{version} ({len(files)} ឯកសារ)...")
    proc = subprocess.run([iscc, "/Q", iss], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print(proc.stdout[-2000:], proc.stderr[-2000:])
        raise RuntimeError("Inno Setup បរាជ័យ")
    out = os.path.join(DIST_DIR, out_name + ".exe")
    print(f"✓ រួចរាល់: {out} ({os.path.getsize(out) / 1e6:.1f} MB)")
    print("  ផ្ញើឯកសារនេះឱ្យមិត្តភក្តិ — ចុចពីរដង → Install → រួចរាល់")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(f"✕ {e}")
        sys.exit(1)
