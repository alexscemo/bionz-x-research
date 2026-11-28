# Sony BIONZ X Mod Manager (Windows GUI)

The **BIONZ X Mod Manager** provides a modern Windows desktop interface (WebView2 / Microsoft Edge, both part of Windows) for toggling camera mods **ON / OFF** and installing them in one click.

## Install (Windows, offline, nothing else to install)

Download `BionzX-Mod-Manager-Setup.exe` or `BionzX-Mod-Manager-win64-portable.zip` from a `mod-manager-v*` GitHub Release (built by `.github/workflows/release-mod-manager.yml`), or copy it from a USB stick. On a new PC with no internet:

1. Double-click `BionzX-Mod-Manager-Setup.exe` and approve the one administrator prompt.
2. Click **Install**. Setup copies the app to `Program Files`, sets up the camera's service-mode USB driver (`054C:0336`) in the same elevated step, creates Start-menu and desktop shortcuts, closes itself and opens the app.

Everything ships inside the app folder:
- Python;
- Sony-PMCA-RE;
- libusb-1.0;
- Mod Loader v2 (`mlv2-layout-build-4`), Arrivederci v5 and the shipped `.mod` files;
- the pinned USB debugger payloads;
- the libwdi `wdi-simple.exe` WinUSB helper (static CRT);
- the bundled Visual C++ runtime DLLs.

The download is small (app folder about 33 MB, portable zip about 18 MB) because the app window uses what Windows already includes, in this order (`webview_runtime.desktop_host`):
1. the WebView2 runtime that Windows 11 includes and Windows Update / Microsoft Edge install on Windows 10;
2. otherwise a chromeless **Microsoft Edge app window** (Edge is part of Windows 10 and 11) on the app's `127.0.0.1` UI, with a throwaway profile under `%LOCALAPPDATA%\BionzXModManager\edge-app` that is deleted when the window closes;
3. otherwise the default browser.

None of these needs a download or an install. File pickers in the Edge/browser window are the native Windows dialogs (`native_dialogs.py`), so Tcl/Tk is not shipped.

Frozen builds never download. A missing or damaged bundled file shows *"Reinstall the Mod Manager"* instead of fetching a replacement (`tools_bundle.offline_only`, `BundledAssetError`). **Tools & Setup** shows **Verify Bundled Tools** instead of **Download / Prepare Tools**. The update check is optional, gives up after 3 seconds, and stays silent offline.

Requirements: Windows 10 1803 or newer (x64), or Windows 11. If the WebView2 window cannot start, the app writes `logs\desktop-launch.log` and opens the same UI in the Edge app window (or the default browser). Administrator prompts: one at Setup, then one per app session when service mode swaps the camera's storage driver. The portable zip skips Setup, so its first service-mode session also sets up the `054C:0336` driver.

## Guided Setup (default screen)

The app opens in a simple mode that shows only Guided Setup. **Show advanced options** (sidebar, remembered per PC) brings back the target picker, **Scan Camera**, **Mods & Tweaks**, **SD Card Sync**, **Tools & Setup** and the bottom install bar. Steps 2 and 3 stay greyed out until the camera is found, a **Camera not found?** checklist opens by itself after a few failed checks, known errors are shown in plain words (the original text stays in the log), and closing the window during an install asks first.

1. **Connect:** the app polls for the camera. If it finds the camera in MTP/PC Remote mode, it sends Sony PTP `SonyReqReconnect` once to switch it to mass storage. Unsupported bodies get a read-only **Send diagnostics** button instead.
2. **Choose:** *Recommended* (verified RAM-only mods; gone after power-off) or *Recommended + unlimited recording* (adds the `recLimit` / `recLimit4k` firmware tweaks; permanent but undoable).
3. **Install:** one button and one administrator prompt per app session. A plain-language warning appears whenever Arrivederci is ON.
4. **What changed & undo:** lists every persistent change this app made on the connected camera. **Undo last install** reverts the newest install; **Restore camera to stock** reverts all of them.

## Practice mode (no camera)

`BionzX-Mod-Manager.exe --practice` (Start menu: **BIONZ X Mod Manager (practice, no camera)**; portable zip: **Try without a camera.cmd**), or **Try it without a camera** on the Connect step, swaps in a pretend `ILCE-6400` (`practice_service.PracticeCameraService`). The real planner builds the plan, so the confirmation, progress, restart prompt, "All done" panel, ledger and **Restore camera to stock** are the screens a real install shows. The camera side is simulated. Practice mode never touches USB, drivers, SD cards or the network: the transport refuses every call, and driver, SD sync and update-check calls are refused or return empty. **Use my real camera** switches back. Developers can run it with `python -m tools.mod_manager_gui --web --practice`.

## Reversibility

- **Host driver (temporary swap):** Senser sessions start an elevated driver broker (`--driver-broker`, one UAC prompt per app session). It binds WinUSB to the camera's mass-storage PID for that session only and removes that package again when the session ends, when the app closes, or when the broker exits. Restore uses `pnputil /delete-driver <oemNN.inf> /uninstall /force`. Only packages whose bytes match a `bionzx_*.inf` in the driver store are ever deleted (`usb_driver.our_driver_packages`). The service-mode driver (`054C:0336`) stays installed, since only service mode uses it. If a session crashed, Guided Setup shows **Restore Windows driver**. The uninstaller runs `--restore-all-drivers`.
- **Camera ledger:** `%LOCALAPPDATA%\BionzXModManager\cameras\<model>\ledger.json` records each persistent write with its before state and read-back. A pre-change snapshot is taken once, before the first write. Sony's MSC identity has no serial number, so bodies of the same model share one ledger on a PC.
- **Restore to stock:** one Senser session that:
  - sets each firmware tweak back to its value before the app first changed it (verified by read-back);
  - removes the USB debugger: restores `/etc/ld.so.preload` to its exact pre-install bytes (or deletes it if it did not exist), then deletes `/setting/a6400_usb_debugd.elf` and `/setting/usb_debug_launcher.so`, and verifies each is absent (Senser error 130).

  RAM-only mods need nothing; a power-cycle clears them. Every restore writes `receipts/restore-receipt-*.json`. If a restore fails part-way, its ledger entries stay active so it can be retried.
- **Not covered:** a persisted Arrivederci matrix (camera powered off with it ON). Automatic repair needs the verified stock matrix values and write path first.

## Camera USB Driver (replaces Zadig)

PMCA's Senser path (`senserShellCommand(driverName="libusb")`) needs WinUSB (or libusbK) on two devices: the camera's mass-storage device, to send the service-mode switch, and the service-mode device (`054C:0336` / `054C:02A9`, mirrored from Sony-PMCA-RE `SONY_ID_PRODUCT_SENSER`).

- When a Senser session fails and a present Sony device lacks a libusb-capable driver, the app raises `DriverMissing` and opens **Tools & Setup → Camera USB Driver...**. The dialog lists every present `054C:*` device with its bound driver service (read through SetupAPI).
- **Install WinUSB (admin)** runs the bundled `wdi-simple.exe` through a UAC prompt, only for a Senser PID or a present Sony device bound to `USBSTOR`. MTP/PTP devices are never touched. Before every run the helper's SHA-256 must match the bundled `manifest.json`, which `installer/build_wdi_simple.ps1` writes when it builds libwdi from a pinned commit. Each install writes `receipts/usb-driver-*.json` (before/after driver, exit code, helper hash).
- The manual dialog is now a fallback for when the broker cannot start (UAC declined, helper missing). While WinUSB stays bound to the mass-storage device, the camera does not appear as a drive in Explorer; **Restore Windows driver** (Guided Setup banner) undoes it.
- CLI: `--install-driver 054C:0336` (Setup uses this); `--restore-all-drivers` (uninstaller; run elevated); `--self-test [--self-test-out report.json]` checks bundled dependencies without touching USB.
- Releases are Authenticode-signed when the `SIGN_PFX_BASE64` / `SIGN_PFX_PASSWORD` repository secrets are set (`installer/sign_files.ps1`; for example via SignPath Foundation for OSS). Without them the build stays unsigned. On launch the app checks GitHub Releases for a newer `mod-manager-v*` tag and only offers a download link (`"update_check": false` in `settings.json` turns this off).

## Troubleshooting

| Symptom | Cause / fix |
| :--- | :--- |
| `error: The key material this operation needs is not set up.` | That message comes from the Rust `pmca-console serviceshell`, which ships without Senser keys. Use the Mod Manager (Python Sony-PMCA-RE Senser path) instead. |
| Service mode times out or "Camera USB driver required" | Open **Camera USB Driver...**, install WinUSB for the listed device(s), then retry. |
| Camera refuses to switch to service mode | Set `USB Connection` to **Auto** (not permanent Mass Storage), reconnect, and retry. |
| Unsupported body (e.g. `ILCE-6100`, which has no Sony firmware updater to dump) | **Scan Camera**, then **Export Onboarding Bundle (.zip)**. It is read-only and writes nothing to the camera; send the zip to the developers. |

## Run from a Checkout (developers, macOS, Linux)

Double-click [`scripts/launch_mod_manager.bat`](../scripts/launch_mod_manager.bat), or run directly from the repository root:

```powershell
# Desktop mode (WebView2; falls back to an Edge app window, then the default browser)
.\.venv-gui\Scripts\python.exe -m tools.mod_manager_gui

# Browser fallback mode (bound strictly to 127.0.0.1)
.\.venv\Scripts\python.exe -m tools.mod_manager_gui --web
```

On macOS or Linux run `scripts/launch_mod_manager.sh` (same `.venv-gui/` provisioning, without `pywin32`/`comtypes`; on Linux it adds `pywebview[qt]` because an isolated venv cannot see the system GTK bindings). `scripts/build_mod_manager_exe.sh` is the matching PyInstaller packager.

`launch_mod_manager.bat` isolates optional GUI/USB packages (`requirements-gui.txt`) in `.venv-gui/` so the preserved offline test `.venv/` stays untouched.

## What You Can Toggle

1. **Runtime Camera Mods (`RAM-Only · Mod Loader v2`):**
   - **Menu Touch Full (`MTF2`)** — verified full touch navigation in Sony's menu.
   - **Arrivederci v5** — camera-verified (`mlv2-layout-build-4`, SMOD v2) ARRI Classic look for S-Log3 / S-Gamut3.Cine (`Movie`), automatically fetched and SHA-256 verified by **Download / Prepare Tools** and the standalone packager. Always toggle **OFF** in camera `MENU -> Mods` before powering off so Sony's backup does not keep the matrix.
   - **Mods Tab Icon (`ModsTabLook`)** — custom `MODS` tab icon (requires built `.mod` output).
   - **Open Gate 3:2 (`Sensor Stage` / `Full 3240x2160 Canvas`)** — mutually exclusive, experimental offline-verified builds.
   - **Custom `.mod` binaries** — drag and drop onto the window or click **+ Add Custom .mod**; validated against the SMOD v1/v2 header and FNV-1a checksum before staging.
2. **Permanent Firmware Tweaks (`PMCA Flash`):**
   - **Unlimited Video Recording** (`recLimit`)
   - **Unlimited 4K Recording** (`recLimit4k`)
   - **Unlock All 35 Languages** (`language`)
   - **PAL / NTSC Selector** (`palNtscSelector`)
   - Click **Read Current from Camera** to read live states via PMCA Senser before making changes. Only changed tweaks are written, and every permanent write requires explicit confirmation.

## 1-Click Install Flow

1. Choose your **Target** profile in the top bar (`ILCE-6400 (FW 2.00)` by default, or `ILCE-6400A (FW 1.01)`), connect your Sony camera via USB in **Mass Storage** mode, and click **Scan Camera**.
2. Toggle desired runtime mods and/or PMCA tweaks **ON** or **OFF**.
3. Click **1-Click Install to Camera**:
   - **First-time camera setup:** verifies `usb_extcmd.ko` and `libusbcmd.so` via Senser (`ILCE-6400` / `ILCE-7C`), installs the pinned native USB debugger to `/setting` with read-back verification, and guides you through a single camera power-cycle.
   - **Unsupported or blocked bodies:** `ILCE-6400A` is blocked from Senser FileControl unless a native debugger session is already active; `ZV-E10` and `ILCE-7C` allow PMCA flash tweaks via Senser while blocking cross-target Mod Loader v2 builds; unrecognized models are routed to a read-only onboarding `.zip` without writing to the camera.
   - **Mod Loader v2 & runtime sync:** runs `mlv2_guard_arm check`, atomically pushes `core.bin` and toggled-ON `.mod` files to `/tmp_bt/mods/` with read-back verification, removes toggled-OFF managed `.mod` files, applies hooks (or hot-reloads), verifies branch targets, and saves a timestamped JSON receipt.

## SD Card Sync

Switch to the **SD Card Sync** tab to stage toggled-ON `.mod` files directly onto a removable FAT32/exFAT SD card (`<Drive>:\mods\`) with SHA-256 read-back verification and `SHA256SUMS.txt` output. Fixed system drives are never listed.

## Standalone Windows Package (.exe)

Build a self-contained, offline Windows bundle:

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\build_wdi_simple.ps1 -OutDir build\libwdi\out
powershell -ExecutionPolicy Bypass -File .\scripts\build_mod_manager_exe.ps1 -WdiSimpleDir build\libwdi\out -Version 1.0.0
powershell -ExecutionPolicy Bypass -File .\installer\audit_dependencies.ps1 -Path dist\BionzX-Mod-Manager -NoVcRuntime wdi-simple.exe
iscc /DAppVersion=1.0.0 installer\BionzX-Mod-Manager.iss
```

- `build_wdi_simple.ps1` needs Visual Studio 2022 (C++), git, curl and 7-Zip. It forces the static CRT and audits the result.
- The packager fails rather than shipping a build that needs a download. That covers PMCA, Mod Loader v2, Arrivederci, the USB debugger payloads and `wdi-simple.exe`.
- Size: the spec leaves out Tcl/Tk (Windows), pycryptodome's and comtypes' self-test suites, PMCA's own Tk GUI, pywin32's MFC layer, WebView2Loader for ARM64/x86 and XML API docs. Setup uses `lzma2/ultra64` solid compression. The release workflow fails when the app folder grows past 60 MB.
- `audit_dependencies.ps1` fails when any bundled `.exe`/`.dll`/`.pyd` imports a DLL that is neither bundled nor part of Windows.
- The release workflow runs the audit and the size gate, then `--self-test` and `--smoke-ui`, which opens the WebView2 window and exits once the UI loads. Both run with outbound network blocked for the app by a firewall rule.

Developer-only switches:
- `-AllowMissingDriverHelper`: build without `wdi-simple.exe` (service mode then needs Zadig).
- `-AllowMissingDebuggerBinaries`: build without the USB debugger payloads (first-time camera setup then fails offline).
- `-BundleWebView2` (opt-in, adds about 950 MB): ship Microsoft's WebView2 Fixed Version runtime for locked-down PCs that have neither WebView2 nor Edge. It uses the pin in `installer\webview2_runtime.json` (written by `installer\pin_webview2_runtime.ps1` from a `.cab` downloaded from <https://developer.microsoft.com/microsoft-edge/webview2/>), or `-WebView2Cab <file>` / `-WebView2RuntimeDir <folder>`. A bundled runtime gets security fixes only through new app releases, so re-pin before each such release.
- `-Mlv2Dir <path>`: override the bundled Mod Loader v2 build directory.
- `-SkipReleaseDownload`: skip downloading `mlv2-layout-build-4` / Arrivederci v5 from GitHub Releases.
- `-OneFile`: emit a single `dist\BionzX-Mod-Manager.exe` (not with `-BundleWebView2`).
