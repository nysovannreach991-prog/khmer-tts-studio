# AI Team #1 launcher - finds Python (or installs a private copy), installs libraries, starts the app.
# Private Python goes to %LOCALAPPDATA%\AITeam1\python (no admin rights, nothing else on the PC changes).
# Everything is logged to %LOCALAPPDATA%\AITeam1\launcher.log
param([switch]$Web)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is very slow with the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$AppDir = $PSScriptRoot
$Data = Join-Path $env:LOCALAPPDATA "AITeam1"
$PyDir = if ($env:AITEAM1_PYDIR) { $env:AITEAM1_PYDIR } else { Join-Path $Data "python" }
$PyVersion = "3.12.10"
$Log = Join-Path $Data "launcher.log"
New-Item -ItemType Directory -Force $Data | Out-Null
try { Start-Transcript -Path $Log -Force | Out-Null } catch {}

function Step($text) { Write-Host ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $text) -ForegroundColor Cyan }

function Disable-QuickEdit {
    # Clicking inside the window pauses the program (QuickEdit) - looks like a frozen black screen
    try {
        Add-Type -Namespace AITeam1 -Name Con -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int h);
[DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out uint m);
[DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, uint m);
'@
        $h = [AITeam1.Con]::GetStdHandle(-10)
        $mode = 0
        if ([AITeam1.Con]::GetConsoleMode($h, [ref]$mode)) {
            [AITeam1.Con]::SetConsoleMode($h, ($mode -band (-bnot 0x40)) -bor 0x80) | Out-Null
        }
    } catch {}
}

function Invoke-Timed($exe, $arguments, $seconds) {
    # Run a program with a time limit, return its output ("" on failure/timeout)
    $out = [IO.Path]::GetTempFileName()
    try {
        $p = Start-Process -FilePath $exe -ArgumentList $arguments -NoNewWindow -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError "$out.err"
        $null = $p.Handle  # without this PowerShell never reports ExitCode
        if (-not $p.WaitForExit($seconds * 1000)) { $p.Kill(); return "" }
        if ($p.ExitCode -ne 0) { return "" }
        return (Get-Content $out -Raw)
    } catch { return "" } finally { Remove-Item $out, "$out.err" -ErrorAction SilentlyContinue }
}

function Find-SystemPython {
    if ($env:AITEAM1_FORCE_EMBED) { return $null }
    # Every python/py on PATH, real installs first. The Microsoft Store shortcut in WindowsApps fails
    # (or could hang) - the time limit protects against that
    $all = @(Get-Command python, py -CommandType Application -All -ErrorAction SilentlyContinue)
    $cmds = @($all | Where-Object { $_.Source -notlike "*\WindowsApps\*" }) +
            @($all | Where-Object { $_.Source -like "*\WindowsApps\*" })
    foreach ($cmd in $cmds) {
        $exe = Invoke-Timed $cmd.Source '-c "import sys; print(sys.executable if sys.version_info >= (3, 10) else chr(0))"' 20
        if ($exe -and $exe.Trim() -and (Test-Path $exe.Trim())) { return $exe.Trim() }
    }
    return $null
}

function Get-File($url, $dest, $label) {
    Step "Downloading $label ..."
    for ($i = 1; $i -le 3; $i++) {
        try {
            Invoke-WebRequest $url -OutFile $dest -UseBasicParsing -TimeoutSec 300
            Write-Host ("    done ({0:N1} MB)" -f ((Get-Item $dest).Length / 1MB))
            return
        } catch {
            Write-Host "    attempt $i failed: $($_.Exception.Message)" -ForegroundColor Yellow
            if ($i -eq 3) { throw "Cannot download $label - check the Internet connection" }
            Start-Sleep 3
        }
    }
}

function Install-PrivatePython {
    Step "Python not found - installing a private copy (one time only)"
    if (Test-Path $PyDir) { Remove-Item $PyDir -Recurse -Force }
    New-Item -ItemType Directory -Force $PyDir | Out-Null
    $zip = Join-Path $env:TEMP "aiteam1_python.zip"
    Get-File "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip" $zip "Python $PyVersion (11 MB)"
    Step "Extracting Python ..."
    Expand-Archive $zip -DestinationPath $PyDir -Force
    Remove-Item $zip
    # Embedded Python ignores site-packages until "import site" is enabled
    $pth = Get-ChildItem $PyDir -Filter "python*._pth" | Select-Object -First 1
    (Get-Content $pth.FullName) -replace '^#\s*import site', 'import site' | Set-Content $pth.FullName -Encoding ASCII

    $getpip = Join-Path $env:TEMP "aiteam1_get-pip.py"
    Get-File "https://bootstrap.pypa.io/get-pip.py" $getpip "pip installer (2 MB)"
    Step "Installing pip ..."
    & (Join-Path $PyDir "python.exe") $getpip --no-warn-script-location --disable-pip-version-check
    if ($LASTEXITCODE) { throw "Installing pip failed" }
    Remove-Item $getpip
    Set-Content (Join-Path $PyDir ".ready") $PyVersion
}

try {
    Disable-QuickEdit
    Write-Host "=== AI Team #1 ===" -ForegroundColor Yellow
    Write-Host "Please wait and do not close this window. First start can take several minutes."
    Write-Host "App folder: $AppDir"
    Write-Host ""
    Step "Checking Python ..."
    $py = Find-SystemPython
    if ($py) {
        Write-Host "    using $py"
    } else {
        if (-not (Test-Path (Join-Path $PyDir ".ready"))) { Install-PrivatePython }
        $py = Join-Path $PyDir "python.exe"
        Write-Host "    using private Python ($py)"
    }

    # Libraries: install only when requirements.txt (or the Python used) changed
    $req = Join-Path $AppDir "requirements.txt"
    $stamp = (Get-FileHash $req -Algorithm SHA256).Hash + "|" + $py
    $marker = Join-Path $Data "requirements.stamp"
    $old = if (Test-Path $marker) { (Get-Content $marker -Raw).Trim() } else { "" }
    if ($old -ne $stamp) {
        Step "Installing libraries (first start only, about 100 MB) - progress is shown below ..."
        & $py -m pip install --disable-pip-version-check --no-warn-script-location --progress-bar on -r $req
        if ($LASTEXITCODE) { throw "Installing libraries failed - check the Internet connection and try again" }
        Set-Content $marker $stamp
    }

    if ($Web) {
        Step "Starting web version ..."
        Start-Process "http://127.0.0.1:5000"
        & $py (Join-Path $AppDir "app.py")
        exit 0
    }

    Step "Starting AI Team #1 ..."
    $pyw = Join-Path (Split-Path $py) "pythonw.exe"
    if (-not (Test-Path $pyw)) { $pyw = $py }
    $gui = Join-Path $AppDir "gui.py"
    $proc = Start-Process -FilePath $pyw -ArgumentList "`"$gui`"" -WorkingDirectory $AppDir -PassThru
    Start-Sleep 5
    if ($proc.HasExited -and $proc.ExitCode -ne 0) {
        # The app closed immediately - run it here so the error is visible (and logged)
        Write-Host "The app closed immediately. Error details:" -ForegroundColor Red
        & $py $gui
        throw "The app could not start (see the error above)"
    }
    Step "Done - this window will close."
    try { Stop-Transcript | Out-Null } catch {}
} catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Log file: $Log" -ForegroundColor Yellow
    Write-Host "Please send a screenshot of this window (or the log file) to the app owner."
    try { Stop-Transcript | Out-Null } catch {}
    exit 1
}
