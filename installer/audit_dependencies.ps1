<#
.SYNOPSIS
    Prove that a standalone build needs only DLLs it bundles or that ship with Windows.
.DESCRIPTION
    Runs `dumpbin /dependents` over every .exe, .dll and .pyd under -Path (skipping
    -ExcludeDir folders such as Microsoft's self-contained WebView2 runtime). Each imported
    DLL must be either:
      - bundled somewhere under -Path, or
      - an API set (api-ms-win-*, ext-ms-*), or present in %SystemRoot%\System32 and not a
        redistributable that Windows does not ship (Visual C++ runtime, debug CRT, MFC, OpenMP,
        Python, libusb, .NET Core).
    With -NoVcRuntime the listed files must not import the Visual C++ runtime at all
    (wdi-simple.exe is built with the static CRT). Writes a JSON report and exits 1 on failure.
#>
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string[]]$ExcludeDir = @("webview2_runtime"),
    [string[]]$NoVcRuntime = @(),
    [string]$Report = ""
)

$ErrorActionPreference = "Stop"

function Find-Dumpbin {
    $cmd = Get-Command dumpbin.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $found = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
            -find "VC\Tools\MSVC\**\bin\Hostx64\x64\dumpbin.exe" | Select-Object -First 1
        if ($found) { return $found }
    }
    throw "dumpbin.exe was not found (install Visual Studio 2022 with the C++ workload)."
}

# Redistributables Windows does not ship: they must be bundled even if this PC happens to have them.
$NotInbox = '^(vcruntime\d+(_\d+)?d?|msvcp\d+(_\w+)?d?|concrt\d+d?|vccorlib\d+d?|vcomp\d+d?|mfc\d+\w*|ucrtbased)\.dll$|^(python\d+|libusb-1\.0|libusb0|libusbk|hostfxr|coreclr)\.dll$'
$VcRuntime = '^(vcruntime\d+(_\d+)?d?|msvcp\d+(_\w+)?d?|concrt\d+d?|vcomp\d+d?|ucrtbased)\.dll$'

$Root = (Resolve-Path $Path).Path
$Dumpbin = Find-Dumpbin
$System32 = Join-Path $env:SystemRoot "System32"
$Files = Get-ChildItem $Root -Recurse -File -Include *.exe, *.dll, *.pyd | Where-Object {
    $rel = $_.FullName.Substring($Root.Length)
    -not ($ExcludeDir | Where-Object { $rel -match "\\$([regex]::Escape($_))(\\|$)" })
}
$Bundled = @{}
Get-ChildItem $Root -Recurse -File -Include *.dll, *.pyd | ForEach-Object { $Bundled[$_.Name.ToLowerInvariant()] = $true }

$Problems = New-Object System.Collections.Generic.List[object]
$Checked = 0
foreach ($file in $Files) {
    $out = & $Dumpbin /nologo /dependents $file.FullName 2>$null
    if ($LASTEXITCODE -ne 0) { continue }  # not a PE image (rare data files with these extensions)
    $Checked++
    $deps = $out | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^[\w.\-]+\.dll$' }
    foreach ($dep in $deps) {
        $name = $dep.ToLowerInvariant()
        if ($NoVcRuntime -contains $file.Name -and $name -match $VcRuntime) {
            $Problems.Add([ordered]@{ file = $file.FullName.Substring($Root.Length + 1); dll = $dep; reason = "must use the static CRT" })
            continue
        }
        if ($Bundled.ContainsKey($name)) { continue }
        if ($name -like "api-ms-win-*" -or $name -like "ext-ms-*") { continue }
        if ((Test-Path (Join-Path $System32 $dep)) -and ($name -notmatch $NotInbox)) { continue }
        $Problems.Add([ordered]@{ file = $file.FullName.Substring($Root.Length + 1); dll = $dep; reason = "not bundled and not part of Windows" })
    }
}

$Result = [ordered]@{ root = $Root; checked = $Checked; ok = ($Problems.Count -eq 0); problems = $Problems }
$Json = $Result | ConvertTo-Json -Depth 5
if ($Report) { [System.IO.File]::WriteAllText($Report, $Json, (New-Object System.Text.UTF8Encoding($false))) }
Write-Host $Json
if ($Checked -eq 0) { throw "No PE files were checked under $Root." }
if ($Problems.Count -gt 0) { exit 1 }
