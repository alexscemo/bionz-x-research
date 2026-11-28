; Inno Setup 6 script for the Sony BIONZ X Mod Manager (Windows x64).
; Build: iscc /DAppVersion=1.2.3 /DSourceDir=..\dist\BionzX-Mod-Manager installer\BionzX-Mod-Manager.iss
;
; One-click, offline setup: double-click -> one administrator prompt -> "Install". Everything the
; app needs ships in {app} (Python, Sony-PMCA-RE, libusb, Mod Loader v2, mods, the USB debugger
; payloads, the libwdi WinUSB helper and Microsoft's WebView2 Fixed Version runtime), so nothing
; else is installed and no internet connection is used.
; The app keeps receipts, settings and custom mods in its user data directory, not here,
; so upgrades and uninstalls never delete them.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\BionzX-Mod-Manager"
#endif
#define AppName "BIONZ X Mod Manager"
#define AppExe "BionzX-Mod-Manager.exe"
; PyInstaller 6 onedir layout: bundled data lives under _internal (sys._MEIPASS).
#define RuntimeDir "{app}\_internal\webview2_runtime"

[Setup]
AppId={{6E0B5C9A-4F1D-4B8E-9C2A-B10F2A6400A1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=BIONZ X community project
DefaultDirName={autopf}\BIONZ X Mod Manager
DefaultGroupName={#AppName}
; Admin once, at double-click: the same elevated Setup pre-installs the service-mode driver,
; so the user sees no second prompt during installation.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 1803+: ships .NET Framework 4.7.2, which pywebview's WinForms backend needs.
MinVersion=10.0.17134
; Single page: the Ready page with its Install button. Setup closes itself and opens the app.
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=no
DisableFinishedPage=yes
OutputDir=..\dist
OutputBaseFilename=BionzX-Mod-Manager-Setup
; Keep the download small: strongest LZMA2 settings, one solid block.
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
CloseApplications=yes

[Messages]
ReadyLabel1=Setup is ready to install [name] on this computer. It works offline and installs nothing else.
ReadyLabel2a=Click Install. The camera's service-mode USB driver is set up automatically and the app opens when Setup finishes.

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{#AppName} (practice, no camera)"; Filename: "{app}\{#AppExe}"; Parameters: "--practice"; Comment: "Try the app with a pretend camera. Nothing real is changed."
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"

[Run]
; Only for builds made with -BundleWebView2 (default builds use the WebView2/Edge in Windows).
; WebView2 Fixed Version on Windows 10 runs its renderer in an AppContainer: grant the
; ALL APPLICATION PACKAGES / ALL RESTRICTED APPLICATION PACKAGES SIDs read+execute (in-box icacls).
Filename: "{sys}\icacls.exe"; Parameters: """{#RuntimeDir}"" /grant *S-1-15-2-1:(OI)(CI)(RX) /grant *S-1-15-2-2:(OI)(CI)(RX) /T /C /Q"; StatusMsg: "Preparing the app window..."; Check: DirExists(ExpandConstant('{#RuntimeDir}')); Flags: runhidden waituntilterminated
; Service-mode driver (WinUSB for 054C:0336) through the bundled, hash-checked libwdi helper. Setup is
; already elevated, so this runs without another prompt. A failure is not fatal: the app retries later.
Filename: "{app}\{#AppExe}"; Parameters: "--install-driver 054C:0336"; StatusMsg: "Setting up the camera's service-mode USB driver..."; Flags: runhidden waituntilterminated
; Open the app as the signed-in user (not elevated); Setup then closes.
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser skipifsilent

[UninstallRun]
; Give every USB device back to Windows' own drivers: removes only driver packages whose bytes
; match a bionzx_*.inf this app created (see usb_driver.our_driver_packages). The uninstaller is
; already elevated (admin install).
Filename: "{app}\{#AppExe}"; Parameters: "--restore-all-drivers"; Flags: runhidden waituntilterminated; RunOnceId: "RestoreWindowsUsbDrivers"
