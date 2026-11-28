<#
.SYNOPSIS
    Build libwdi's wdi-simple.exe from a pinned commit for the Mod Manager's WinUSB driver step.
.DESCRIPTION
    libwdi (the engine behind Zadig, LGPL-3.0) publishes no wdi-simple.exe binary, so this
    script follows upstream's own VS2022 CI recipe at a pinned commit: it downloads the WDK 8
    redistributable plus the libusb-win32 / libusbK redistributables, builds the solution for
    Release|x64 with MSBuild, and writes <OutDir>\wdi-simple.exe plus manifest.json
    (SHA-256, libwdi commit, redistributable URLs). The Mod Manager refuses to run the
    helper unless its hash matches that manifest.

    Requires Visual Studio 2022 (MSBuild with the C++ workload), git, curl and 7-Zip on PATH;
    GitHub's windows-latest runner has all of them.
#>
param(
    [string]$OutDir = "",
    [string]$WorkDir = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutDir) { $OutDir = Join-Path $RepoRoot "build\libwdi\out" }
if (-not $WorkDir) { $WorkDir = Join-Path $RepoRoot "build\libwdi\src" }

# Pinned upstream identity (pbatard/libwdi master, 2025-07-17).
$LibwdiRepo = "https://github.com/pbatard/libwdi.git"
$LibwdiCommit = "30df0c0e051b0132c4b9ebed8c054bc8eb3aaaec"
$WdkUrl = "https://go.microsoft.com/fwlink/p/?LinkID=253170"
$Libusb0Url = "https://github.com/mcuee/libusb-win32/releases/download/release_1.4.0.0/libusb-win32-bin-1.4.0.0.zip"
$LibusbKUrl = "https://github.com/mcuee/libusbk/releases/download/V3.1.0.0/libusbK-3.1.0.0-bin.7z"

function Invoke-Checked([string]$Exe, [string[]]$ArgList) {
    & $Exe @ArgList
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($ArgList -join ' ') failed with exit code $LASTEXITCODE" }
}

if (Test-Path $WorkDir) { Remove-Item -Recurse -Force $WorkDir }
New-Item -ItemType Directory -Force $WorkDir | Out-Null
New-Item -ItemType Directory -Force $OutDir | Out-Null

Write-Host "[1/4] Fetching libwdi $LibwdiCommit..."
$Src = Join-Path $WorkDir "libwdi"
Invoke-Checked git @("clone", "--quiet", $LibwdiRepo, $Src)
Invoke-Checked git @("-C", $Src, "checkout", "--quiet", $LibwdiCommit)
Invoke-Checked git @("-C", $Src, "submodule", "update", "--init", "--recursive", "--quiet")

Write-Host "[2/4] Downloading WDK 8 / libusb-win32 / libusbK redistributables..."
Push-Location $Src
try {
    Invoke-Checked curl.exe @("-sSL", $WdkUrl, "-o", "wdk-redist.msi")
    Invoke-Checked curl.exe @("-sSL", $Libusb0Url, "-o", "libusb0-redist.zip")
    Invoke-Checked curl.exe @("-sSL", $LibusbKUrl, "-o", "libusbk-redist.7z")
    $msi = Start-Process msiexec.exe -ArgumentList @("/a", "wdk-redist.msi", "/qn", "TARGETDIR=$Src\wdk") -Wait -PassThru
    if ($msi.ExitCode -ne 0) { throw "WDK redistributable extraction failed ($($msi.ExitCode))" }
    Invoke-Checked 7z @("x", "-y", "libusb0-redist.zip")
    Invoke-Checked 7z @("x", "-y", "libusbk-redist.7z")
    Get-ChildItem -Directory -Filter "libusb-win32*" | Select-Object -First 1 | Rename-Item -NewName "libusb0"
    Get-ChildItem -Directory -Filter "libusbK*" | Select-Object -First 1 | Rename-Item -NewName "libusbk"

    # Static CRT (/MT): the helper must run on a fresh PC without the Visual C++ redistributable.
    # Directory.Build.targets is imported after each project's own settings, so it wins.
    $StaticCrt = @'
<Project>
  <ItemDefinitionGroup Condition="'$(Configuration)'=='Release'">
    <ClCompile>
      <RuntimeLibrary>MultiThreaded</RuntimeLibrary>
    </ClCompile>
  </ItemDefinitionGroup>
</Project>
'@
    [System.IO.File]::WriteAllText((Join-Path $Src "Directory.Build.targets"), $StaticCrt, (New-Object System.Text.UTF8Encoding($false)))

    Write-Host "[3/4] Building libwdi (Release|x64, static CRT)..."
    $msbuild = (Get-Command msbuild.exe -ErrorAction SilentlyContinue).Source
    if (-not $msbuild) {
        $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
        $msbuild = & $vswhere -latest -requires Microsoft.Component.MSBuild -find "MSBuild\**\Bin\MSBuild.exe" | Select-Object -First 1
    }
    if (-not $msbuild) { throw "MSBuild was not found (install Visual Studio 2022 with the C++ workload)." }
    $macros = 'WDK_DIR="../wdk/Windows Kits/8.0";LIBUSB0_DIR="../libusb0";LIBUSBK_DIR="../libusbk/bin"'
    Invoke-Checked $msbuild @(".\libwdi.sln", "/m", "/p:Configuration=Release", "/p:Platform=x64", "/p:BuildMacros=`"$macros`"")
} finally {
    Pop-Location
}

Write-Host "[4/4] Writing wdi-simple.exe and manifest.json..."
$Built = Get-ChildItem -Path $Src -Recurse -Filter "wdi-simple.exe" | Where-Object { $_.FullName -match "\\x64\\Release\\" } | Select-Object -First 1
if (-not $Built) { throw "wdi-simple.exe was not produced by the build." }
$Dest = Join-Path $OutDir "wdi-simple.exe"
Copy-Item $Built.FullName $Dest -Force
Copy-Item (Join-Path $Src "COPYING") (Join-Path $OutDir "LICENSE-libwdi.txt") -Force
& (Join-Path $PSScriptRoot "audit_dependencies.ps1") -Path $OutDir -NoVcRuntime @("wdi-simple.exe")
if ($LASTEXITCODE -ne 0) { throw "wdi-simple.exe imports DLLs a fresh Windows install does not have." }
$Sha = (Get-FileHash $Dest -Algorithm SHA256).Hash.ToLowerInvariant()
$Manifest = [ordered]@{
    tool = "wdi-simple"
    sha256 = $Sha
    size = (Get-Item $Dest).Length
    libwdi_repo = $LibwdiRepo
    libwdi_commit = $LibwdiCommit
    platform = "x64"
    configuration = "Release"
    license = "LGPL-3.0 (see LICENSE-libwdi.txt)"
    redistributables = [ordered]@{ wdk = $WdkUrl; libusb0 = $Libusb0Url; libusbk = $LibusbKUrl }
    built_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}
$Manifest | ConvertTo-Json -Depth 4 | Out-File -FilePath (Join-Path $OutDir "manifest.json") -Encoding utf8
Write-Host "wdi-simple.exe SHA-256 $Sha -> $OutDir"
