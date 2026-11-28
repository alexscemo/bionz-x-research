<#
.SYNOPSIS
    Authenticode-sign release binaries when a code-signing certificate is configured.
.DESCRIPTION
    Reads the certificate from SIGN_PFX_BASE64 / SIGN_PFX_PASSWORD (GitHub Actions secrets,
    e.g. from SignPath Foundation or a purchased OV/EV certificate export). Without them it
    prints a warning and exits 0, so unsigned builds still work. With -UpdateManifest it
    rewrites the sha256/size of a libwdi manifest.json after signing wdi-simple.exe, because
    the Mod Manager refuses to run the helper when the hash does not match.
#>
param(
    [Parameter(Mandatory = $true)][string[]]$Path,
    [string]$UpdateManifest = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
if (-not $env:SIGN_PFX_BASE64) {
    Write-Warning "SIGN_PFX_BASE64 is not set; leaving $($Path -join ', ') unsigned."
    exit 0
}

$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $signtool) { throw "signtool.exe was not found (install the Windows 10/11 SDK)." }

$pfx = Join-Path $env:RUNNER_TEMP "codesign.pfx"
if (-not $env:RUNNER_TEMP) { $pfx = Join-Path ([System.IO.Path]::GetTempPath()) "codesign.pfx" }
[System.IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:SIGN_PFX_BASE64))
try {
    foreach ($file in $Path) {
        & $signtool.FullName sign /fd SHA256 /tr $TimestampUrl /td SHA256 /f $pfx /p $env:SIGN_PFX_PASSWORD $file
        if ($LASTEXITCODE -ne 0) { throw "signtool failed for $file ($LASTEXITCODE)" }
    }
} finally {
    Remove-Item $pfx -Force -ErrorAction SilentlyContinue
}

if ($UpdateManifest) {
    $manifest = Get-Content $UpdateManifest -Raw | ConvertFrom-Json
    $exe = $Path[0]
    $manifest.sha256 = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    $manifest.size = (Get-Item $exe).Length
    $manifest | Add-Member -NotePropertyName signed -NotePropertyValue $true -Force
    $json = $manifest | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText($UpdateManifest, $json, (New-Object System.Text.UTF8Encoding($false)))
}
