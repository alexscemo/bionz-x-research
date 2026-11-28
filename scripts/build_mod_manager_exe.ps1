<#
.SYNOPSIS
    1-click Windows standalone packager for Sony BIONZ X Mod Manager.
.DESCRIPTION
    Provisions an isolated .venv-packager environment, caches Sony-PMCA-RE,
    bundles libusb-1.0, shipped .mod binaries, Mod Loader v2, Arrivederci v5,
    the USB debugger payloads and the libwdi wdi-simple.exe WinUSB helper, and builds
    dist\BionzX-Mod-Manager\ via PyInstaller (about 40 MB). The result runs on an offline
    PC with nothing else installed: the window uses the WebView2 runtime or Microsoft Edge
    that Windows already includes. A release build fails instead of producing an app that
    needs a download. -BundleWebView2 adds Microsoft's ~950 MB Fixed Version runtime for
    locked-down PCs without Edge; it is off by default to keep the package small.
#>
param(
    [switch]$OneFile,
    [string]$Mlv2Dir = "",
    # Kept for compatibility: the USB debugger payloads are now always bundled.
    [switch]$IncludeDebuggerBinaries,
    # Developer builds only: allow a package without the USB debugger payloads.
    [switch]$AllowMissingDebuggerBinaries,
    [switch]$SkipPmcaDownload,
    [switch]$SkipReleaseDownload,
    # Folder holding wdi-simple.exe + manifest.json from installer\build_wdi_simple.ps1.
    [string]$WdiSimpleDir = "",
    # Developer builds only: allow a package without the WinUSB driver helper.
    [switch]$AllowMissingDriverHelper,
    # Opt-in: bundle the WebView2 Fixed Version runtime (pinned in installer\webview2_runtime.json,
    # ~950 MB unpacked). Default builds use the WebView2 runtime / Microsoft Edge in Windows.
    [switch]$BundleWebView2,
    # With -BundleWebView2: a local .cab or an already extracted folder instead of the pinned URL.
    [string]$WebView2Cab = "",
    [string]$WebView2RuntimeDir = "",
    [string]$Version = "0.0.0"
)

$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 turns any native stderr line into a terminating error under "Stop";
# pip's version-check warning alone would abort the build.
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

function Resolve-BasePython {
    foreach ($candidate in @(
        (Join-Path $RepoRoot ".venv\Scripts\python.exe"),
        (Join-Path $RepoRoot "venv\Scripts\python.exe")
    )) {
        if (Test-Path $candidate) { return $candidate }
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    throw "Python 3.11+ was not found. Install Python and retry."
}

if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "-Version must look like 1.2.3, got '$Version'." }

$PackagerPy = Join-Path $RepoRoot ".venv-packager\Scripts\python.exe"
if (-not (Test-Path $PackagerPy)) {
    $BasePy = Resolve-BasePython
    Write-Host "[1/5] Creating isolated .venv-packager environment..."
    & $BasePy -m venv (Join-Path $RepoRoot ".venv-packager")
}

Write-Host "[2/5] Installing GUI & PyInstaller dependencies..."
$ErrorActionPreference = "Continue"
& $PackagerPy -m pip install --upgrade pip --quiet
& $PackagerPy -m pip install -r (Join-Path $RepoRoot "requirements-gui.txt") --quiet
$PipExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($PipExit -ne 0) { throw "pip install -r requirements-gui.txt failed." }

Write-Host "[3/5] Preparing bundled Sony-PMCA-RE, official MLv2, and Arrivederci v5..."
$PmcaPath = ""
if ($SkipPmcaDownload) {
    $PmcaPath = & $PackagerPy -c "from src.a6400.mod_manager.tools_bundle import find_pmca_dir; p = find_pmca_dir(); print(p or '')"
} else {
    $PmcaPath = & $PackagerPy -c "from src.a6400.mod_manager.tools_bundle import ensure_pmca; print(ensure_pmca())"
}
$PmcaPath = "$PmcaPath".Trim()
if (-not $PmcaPath -or -not (Test-Path (Join-Path $PmcaPath "pmca\usb\sony.py"))) {
    throw "Sony-PMCA-RE was not found; refusing to build a package that downloads PMCA at runtime."
}

if ($Mlv2Dir) {
    & $PackagerPy -c "import sys; from pathlib import Path; from src.a6400.mod_manager.tools_bundle import validate_mlv2_build; print(validate_mlv2_build(Path(sys.argv[1]))['path'])" $Mlv2Dir
} elseif (-not $SkipReleaseDownload) {
    $Mlv2Dir = (& $PackagerPy -c "from src.a6400.mod_manager.tools_bundle import ensure_mlv2_release; print(ensure_mlv2_release()['path'])").Trim()
}

$ArrivederciPath = ""
if (-not $SkipReleaseDownload) {
    $ArrivederciPath = (& $PackagerPy -c "from src.a6400.mod_manager.tools_bundle import ensure_arrivederci_v5; print(ensure_arrivederci_v5())").Trim()
} else {
    $ArrivederciPath = (& $PackagerPy -c "from src.a6400.mod_manager.tools_bundle import find_arrivederci_v5; p = find_arrivederci_v5(); print(p or '')").Trim()
}
if (-not $SkipReleaseDownload) {
    if (-not $Mlv2Dir -or -not (Test-Path (Join-Path $Mlv2Dir "core.bin"))) { throw "Mod Loader v2 release was not prepared." }
    if (-not $ArrivederciPath -or -not (Test-Path $ArrivederciPath)) { throw "Arrivederci v5 was not prepared." }
}

Write-Host "[4/5] Verifying the WinUSB driver helper (libwdi wdi-simple.exe)..."
$WdiResolved = ""
if ($WdiSimpleDir) {
    $WdiExe = Join-Path $WdiSimpleDir "wdi-simple.exe"
    $WdiManifest = Join-Path $WdiSimpleDir "manifest.json"
    if (-not (Test-Path $WdiExe) -or -not (Test-Path $WdiManifest)) {
        throw "-WdiSimpleDir must contain wdi-simple.exe and manifest.json (run installer\build_wdi_simple.ps1)."
    }
    $Expected = ((Get-Content $WdiManifest -Raw | ConvertFrom-Json).sha256).ToLowerInvariant()
    $Actual = (Get-FileHash $WdiExe -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Expected -ne $Actual) { throw "wdi-simple.exe SHA-256 $Actual does not match manifest $Expected." }
    $WdiResolved = (Resolve-Path $WdiSimpleDir).Path
} elseif (-not $AllowMissingDriverHelper) {
    throw "No -WdiSimpleDir given. Build it with installer\build_wdi_simple.ps1, or pass -AllowMissingDriverHelper for a developer build."
} else {
    Write-Warning "Building WITHOUT the WinUSB driver helper; users will need Zadig for service mode."
}

Write-Host "[4b/5] Bundling the pinned USB debugger payloads (offline first-time camera setup)..."
$DbgOk = & $PackagerPy -c "from src.a6400.targets.usb_debugger_compat import get_debugger_target; from tools.setup_usb_debugger import ensure_payload_binaries; ensure_payload_binaries(get_debugger_target('ilce_6400')); print('ok')"
$DbgOk = "$($DbgOk | Select-Object -Last 1)".Trim()  # earlier lines are download progress
if ($DbgOk -ne "ok") {
    if ($AllowMissingDebuggerBinaries) {
        Write-Warning "Building WITHOUT the USB debugger payloads; first-time camera setup will fail offline."
    } else {
        throw "USB debugger payloads are missing or fail their pinned SHA-256; refusing a build that needs a download at runtime."
    }
}
$IncludeDbg = ($DbgOk -eq "ok")

$Wv2Dir = ""
$Wv2Version = ""
$Wv2Stage = Join-Path $RepoRoot "build\webview2"
if (($WebView2Cab -or $WebView2RuntimeDir) -and -not $BundleWebView2) {
    throw "-WebView2Cab / -WebView2RuntimeDir need -BundleWebView2."
}
if (-not $BundleWebView2) {
    Write-Host "[4c/5] Using the WebView2 runtime / Microsoft Edge that Windows includes (no bundled runtime)."
} else {
    Write-Host "[4c/5] Bundling the WebView2 Fixed Version runtime (opt-in, ~950 MB)..."
    if ($OneFile) { throw "-OneFile would unpack the WebView2 runtime on every launch; drop -OneFile or -BundleWebView2." }
}
if ($BundleWebView2 -and $WebView2RuntimeDir) {
    $Exe = Get-ChildItem $WebView2RuntimeDir -Recurse -Filter msedgewebview2.exe | Select-Object -First 1
    if (-not $Exe) { throw "-WebView2RuntimeDir has no msedgewebview2.exe." }
    $Wv2Dir = $Exe.DirectoryName
    $Wv2Version = (Get-Item $Exe.FullName).VersionInfo.ProductVersion
} elseif ($BundleWebView2) {
    $Pin = Get-Content (Join-Path $RepoRoot "installer\webview2_runtime.json") -Raw | ConvertFrom-Json
    if (-not $Pin.sha256) {
        throw "installer\webview2_runtime.json is not pinned. Run installer\pin_webview2_runtime.ps1 with a downloaded Fixed Version .cab."
    }
    New-Item -ItemType Directory -Force $Wv2Stage | Out-Null
    $Cab = $WebView2Cab
    if (-not $Cab) {
        if (-not $Pin.url) { throw "No pinned WebView2 download URL; pass -WebView2Cab <file>." }
        $Cab = Join-Path $Wv2Stage $Pin.filename
        if (-not (Test-Path $Cab)) {
            Write-Host "  Downloading $($Pin.filename)..."
            Invoke-WebRequest -Uri $Pin.url -OutFile $Cab -UseBasicParsing
        }
    }
    $CabItem = Get-Item $Cab
    $CabSha = (Get-FileHash $CabItem.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($CabItem.Length -ne [int64]$Pin.size -or $CabSha -ne $Pin.sha256.ToLowerInvariant()) {
        throw "WebView2 cab $($CabItem.Name) ($($CabItem.Length) B, $CabSha) does not match the pin ($($Pin.size) B, $($Pin.sha256))."
    }
    $Extract = Join-Path $Wv2Stage "extract-$($Pin.version)"
    if (Test-Path $Extract) { Remove-Item $Extract -Recurse -Force }
    New-Item -ItemType Directory -Force $Extract | Out-Null
    & "$env:SystemRoot\System32\expand.exe" $CabItem.FullName "-F:*" $Extract | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "expand.exe failed for $($CabItem.Name) ($LASTEXITCODE)." }
    $Exe = Get-ChildItem $Extract -Recurse -Filter msedgewebview2.exe | Select-Object -First 1
    if (-not $Exe) { throw "The WebView2 cab did not contain msedgewebview2.exe." }
    $Wv2Dir = $Exe.DirectoryName
    $Wv2Version = $Pin.version
}
$Wv2VersionFile = ""
if ($Wv2Dir) {
    $Wv2VersionFile = Join-Path $Wv2Stage "bionzx_runtime_version.txt"
    New-Item -ItemType Directory -Force $Wv2Stage | Out-Null
    [System.IO.File]::WriteAllText($Wv2VersionFile, $Wv2Version, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "  WebView2 Fixed Version $Wv2Version from $Wv2Dir"
}

$VersionFile = Join-Path $RepoRoot "build\mod-manager\version_info.txt"
New-Item -ItemType Directory -Force (Split-Path $VersionFile) | Out-Null
$Parts = $Version.Split(".")
$VersionText = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($($Parts[0]), $($Parts[1]), $($Parts[2]), 0), prodvers=($($Parts[0]), $($Parts[1]), $($Parts[2]), 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'BIONZ X Mod Manager'),
      StringStruct('FileDescription', 'Sony BIONZ X Mod Manager'),
      StringStruct('FileVersion', '$Version'),
      StringStruct('ProductVersion', '$Version'),
      StringStruct('OriginalFilename', 'BionzX-Mod-Manager.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
# No BOM: PyInstaller evaluates this file as Python source.
[System.IO.File]::WriteAllText($VersionFile, $VersionText, (New-Object System.Text.UTF8Encoding($false)))
# Plain version string read by the app's update check (src/a6400/mod_manager/updates.py).
$AppVersionFile = Join-Path $RepoRoot "build\mod-manager\app_version.txt"
[System.IO.File]::WriteAllText($AppVersionFile, $Version, (New-Object System.Text.UTF8Encoding($false)))
$env:BIONZX_APP_VERSION_FILE = $AppVersionFile

$env:BIONZX_REPO_ROOT = $RepoRoot
$env:BIONZX_PMCA_DIR = $PmcaPath
$env:BIONZX_MLV2_DIR = $Mlv2Dir
$env:BIONZX_ARRIVEDERCI_PATH = $ArrivederciPath
$env:BIONZX_WDI_DIR = $WdiResolved
$env:BIONZX_VERSION_FILE = $VersionFile
$env:BIONZX_INCLUDE_DEBUGGER_BIN = $(if ($IncludeDbg) { "1" } else { "0" })
$env:BIONZX_WEBVIEW2_DIR = $Wv2Dir
$env:BIONZX_WEBVIEW2_VERSION_FILE = $Wv2VersionFile
$env:BIONZX_ONEFILE = $(if ($OneFile) { "1" } else { "0" })

Write-Host "[5/5] Building standalone Windows executable via PyInstaller..."
# PyInstaller logs to stderr; keep Windows PowerShell 5.1 from treating that as a failure.
$ErrorActionPreference = "Continue"
& $PackagerPy -m PyInstaller --noconfirm --clean --log-level WARN `
    --workpath (Join-Path $RepoRoot "build\mod-manager") `
    --distpath (Join-Path $RepoRoot "dist") `
    (Join-Path $RepoRoot "tools\mod_manager.spec")
$PyiExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($PyiExit -ne 0) {
    throw "PyInstaller failed with exit code $PyiExit (close any running BionzX-Mod-Manager.exe and retry)."
}

if (-not $OneFile) {
    # Portable zip: a double-clickable way into practice mode (pretend camera, nothing real changes).
    $PracticeCmd = Join-Path $RepoRoot "dist\BionzX-Mod-Manager\Try without a camera.cmd"
    $CmdText = "@echo off`r`nrem Opens the Mod Manager with a pretend camera. Nothing real is changed.`r`nstart `"`" `"%~dp0BionzX-Mod-Manager.exe`" --practice`r`n"
    [System.IO.File]::WriteAllText($PracticeCmd, $CmdText, [System.Text.Encoding]::ASCII)
}

if ($OneFile) {
    Write-Host "`nDone! Standalone EXE: $RepoRoot\dist\BionzX-Mod-Manager.exe"
} else {
    Write-Host "`nDone! Standalone bundle: $RepoRoot\dist\BionzX-Mod-Manager\BionzX-Mod-Manager.exe"
}
