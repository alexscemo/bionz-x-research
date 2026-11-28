<#
.SYNOPSIS
    Pin the Microsoft Edge WebView2 Fixed Version runtime for opt-in -BundleWebView2 builds.
.DESCRIPTION
    Download the x64 "Fixed Version" .cab from https://developer.microsoft.com/microsoft-edge/webview2/
    (Microsoft.WebView2.FixedVersionRuntime.<version>.x64.cab), then run this script with the file and
    the exact download URL. It records version, size and SHA-256 in installer\webview2_runtime.json.
    scripts\build_mod_manager_exe.ps1 downloads (CI) or reads (-WebView2Cab) the cab and refuses it
    when it does not match. The Fixed Version runtime only gets security fixes through new app
    releases, so re-pin a current version before each release.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File installer\pin_webview2_runtime.ps1 -CabPath C:\Downloads\Microsoft.WebView2.FixedVersionRuntime.131.0.2903.112.x64.cab -Url https://msedge.sf.dl.delivery.mp.microsoft.com/...
#>
param(
    [Parameter(Mandatory = $true)][string]$CabPath,
    [string]$Url = ""
)

$ErrorActionPreference = "Stop"
$JsonPath = Join-Path $PSScriptRoot "webview2_runtime.json"
$Cab = Get-Item $CabPath
if ($Cab.Name -notmatch '^Microsoft\.WebView2\.FixedVersionRuntime\.(\d+\.\d+\.\d+\.\d+)\.x64\.cab$') {
    throw "Expected Microsoft.WebView2.FixedVersionRuntime.<version>.x64.cab, got '$($Cab.Name)'."
}
$PinVersion = $Matches[1]
if ($Url -and $Url -notmatch '^https://[^/]*\.microsoft\.com/') { throw "-Url must be an https Microsoft download URL." }

$Pin = Get-Content $JsonPath -Raw | ConvertFrom-Json
$Pin.version = $PinVersion
$Pin.filename = $Cab.Name
$Pin.url = $Url
$Pin.size = $Cab.Length
$Pin.sha256 = (Get-FileHash $Cab.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText($JsonPath, ($Pin | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Pinned WebView2 Fixed Version $PinVersion ($($Pin.size) bytes, sha256 $($Pin.sha256))."
if (-not $Url) { Write-Warning "No -Url recorded: CI cannot download the cab; local builds need -WebView2Cab." }
