# Apps

Programs people run on a PC, a phone or the camera. Each folder has its own README.

| Folder | What it is |
| :--- | :--- |
| [`isp-studio/`](isp-studio/) | **BIONZ Look Lab**: Rust/`wgpu` desktop studio for `.bzlook` color presets and Film Sim mods ([guide](../docs/BIONZ_LOOK_LAB.md)) |
| [`lut-converter/`](lut-converter/README.md) | Offline color/tone LUT fitting, ARRI presets and the golden tables the studio checks against |
| [`android/`](android/) | Android clients: [`bionz_look_lab`](android/bionz_look_lab/README.md) (looks + WiFiSync), [`cinemahud`](android/cinemahud/README.md) (monitor/PTP), [`raw-usb-ref`](android/raw-usb-ref/README.md) (raw-USB reference) |
| [`camera-daemon/`](camera-daemon/README.md) | On-camera ARM daemon (`a6400-lived`) and its host tests |

The Windows **Mod Manager** is not here: its UI is `tools/mod_manager_gui.py` +
`tools/mod_manager_ui/` and its logic is `src/a6400/mod_manager/` ([guide](../docs/MOD_MANAGER_GUI.md)).
