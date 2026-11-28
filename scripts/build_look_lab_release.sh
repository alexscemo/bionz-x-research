#!/usr/bin/env bash
# Build the portable BIONZ Look Lab release for macOS or Linux.
#
# macOS/Linux counterpart of build_look_lab_release.ps1: runs the Rust workspace tests, builds
# `bionz-app` in release mode, copies the binary into dist/BIONZ-Look-Lab-<os>-<arch>/, writes the
# SD-card instructions, archives the folder as a .tar.gz (keeps the executable bit), and checks that
# the binary links only against system libraries.
#
# Usage: scripts/build_look_lab_release.sh [--skip-tests]
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/_common.sh"

SKIP_TESTS=0
for arg in "$@"; do
    case "$arg" in
        --skip-tests) SKIP_TESTS=1 ;;
        -h | --help) sed -n '2,9p' "$0"; exit 0 ;;
        *) die "unknown argument: $arg" ;;
    esac
done

OS=$(host_os)
ARCH=$(host_arch)
STUDIO="$REPO_ROOT/apps/isp-studio"
DIST="$REPO_ROOT/dist"
NAME="BIONZ-Look-Lab-$OS-$ARCH"
BUNDLE="$DIST/$NAME"
BIN="$BUNDLE/bionz-look-lab"
ARCHIVE="$DIST/$NAME.tar.gz"
SHA_FILE="$DIST/SHA256SUMS-$OS-$ARCH.txt"

command -v cargo >/dev/null || die "cargo was not found on PATH; install Rust (https://rustup.rs)"

echo "[1/5] Rust workspace tests"
if [ "$SKIP_TESTS" = 1 ]; then
    echo "      skipped (--skip-tests)"
else
    (cd "$STUDIO" && cargo test --workspace --exclude bionz-py)
fi

echo "[2/5] cargo build --release -p bionz-app"
(cd "$STUDIO" && cargo build --release -p bionz-app)

BUILT="${CARGO_TARGET_DIR:-$STUDIO/target}/release/bionz-look-lab"
[ -x "$BUILT" ] || die "expected $BUILT; did the binary target get renamed?"

echo "[3/5] Bundle into dist/$NAME"
mkdir -p "$BUNDLE"
cp -f "$BUILT" "$BIN"
chmod 755 "$BIN"

echo "[4/5] README.txt"
if [ "$OS" = macos ]; then
    PLATFORM_TITLE="macOS"
    LAUNCH_NOTE='Run ./bionz-look-lab from Terminal, or open it from Finder. The binary is not notarized: if
macOS blocks it, run  xattr -d com.apple.quarantine bionz-look-lab  once in this folder.'
else
    PLATFORM_TITLE="Linux"
    LAUNCH_NOTE='Run ./bionz-look-lab. It needs a running X11 or Wayland session with OpenGL; the window
libraries (libX11/libxkbcommon/libwayland/libGL) are loaded from the system at start-up.'
fi

cat >"$BUNDLE/README.txt" <<EOF
BIONZ Look Lab - portable $PLATFORM_TITLE build
==========================================

bionz-look-lab is self-contained: the twelve shipped film-simulation looks and the ARM mod
template are compiled into the binary, so it needs no preset folder, Python or GCC next to it.

$LAUNCH_NOTE

Using the looks in the app
--------------------------
Open a photo or a Sony ARW (Ctrl/Cmd+O, or drag it onto the window), pick a look on the left, and
move the sliders. Compare side B is the stock camera rendering, or a real camera JPEG you add with
"Add camera JPEG".

Putting a look on the camera
----------------------------
1. Click "Export Camera Mod (.mod)" in the toolbar.
2. Pick the mod kind:
     Single look      -> one .mod per look, switched on/off in MENU -> Mods
     Live picker      -> one FilmSimulations.mod with every look; it opens in Live View and
                         Left/Right (or Up/Down) switches the look live, ENTER locks it
3. Pick the camera body:
     a6400 + a6100  -> writes <folder>/a6400/mods/... and <folder>/a6100/mods/...
     one body only  -> writes <folder>/mods/..., or <folder>/... if the folder you chose is
                       already named "mods" (for example <SD card>/mods)
4. Choose the destination folder, or the SD card root, and click "Export .mod".
5. If you exported to a folder other than the card, copy the .mod file to <SD card>/mods/ (create
   the folder if it does not exist). One .mod file per look; keep the file name as generated.
6. Put the card back into the camera, start Mod Loader v2, open MENU -> Mods and switch the look on.

Important
---------
* Turning the look off writes the stock tables back to RAM. Do not delete or rename the .mod on the
  card while the look is enabled.
* The .mod targets ILCE-6400 Ver.2.00 (a6400) and ILCE-6100 Ver.1.00 (a6100). Exporting for the
  wrong body writes the right tables to the wrong physical addresses.
* A look built with a Color Mode base matrix (Movie/Arrivederci presets) is refused: those looks
  need mods/arri_isp/arrivederci, not the Film Sims payload.
* Nothing here talks to the camera. Files are written to the folder you choose, nowhere else.

Exporting without the window (optional)
---------------------------------------
    ./bionz-look-lab --export-mod <folder> [--kind single|live] [--look "Gold 200"] [--bodies both|a6400|a6100]

It prints a JSON receipt with the files it wrote. Without a recognised flag the app starts normally.

Regenerating the ARM templates (developers)
-------------------------------------------
The SMOD v2 payloads are built once and committed under apps/isp-studio/crates/bionz-preset/assets/.
With the cross toolchain from scripts/setup_arm_toolchain.sh in place:
* film_sim_template.blob (+ .json):
    bash mods/film_sims/template/build_template_wsl.sh
* film_sim_live_template.mod (+ .json):
    bash mods/film_sims/live_picker/build_live_template_wsl.sh

Rebuild them only when the C template sources change.
EOF

echo "[5/5] Archive and portability checks"
rm -f "$ARCHIVE"
tar -czf "$ARCHIVE" -C "$DIST" "$NAME"

# The binary must not depend on anything a fresh machine lacks (Homebrew/MacPorts dylibs, a
# library that only resolves on the build host, ...).
if [ "$OS" = macos ]; then
    DEPS=$(otool -L "$BIN" | tail -n +2 | awk '{print $1}')
    FOREIGN=$(printf '%s\n' "$DEPS" | grep -Ev '^(/usr/lib/|/System/Library/)' || true)
else
    LDD_OUT=$(ldd "$BIN" 2>&1 || true)
    DEPS=$(printf '%s\n' "$LDD_OUT" | awk '{print $1}')
    FOREIGN=$(printf '%s\n' "$LDD_OUT" | awk '
        /not found/ { print $1; next }
        $3 ~ /^\// && $3 !~ /^\/(usr\/)?lib(64|32|x32)?\// { print $1 }')
fi
if [ -n "$FOREIGN" ]; then
    die "bionz-look-lab links non-system libraries: $(tr "\n" " " <<<"$FOREIGN")"
fi

SHA=$(sha256_of "$BIN")
ARCHIVE_SHA=$(sha256_of "$ARCHIVE")
printf '%s  %s\n%s  %s\n' "$SHA" "bionz-look-lab" "$ARCHIVE_SHA" "$NAME.tar.gz" >"$SHA_FILE"

echo
echo "Linked libraries: $(tr "\n" " " <<<"$DEPS")"
echo "Only system libraries are linked."
echo
echo "Binary:  $BIN ($(file_size_mb "$BIN") MB)"
echo "Archive: $ARCHIVE ($(file_size_mb "$ARCHIVE") MB)"
echo "SHA256 (binary):  $SHA"
echo "SHA256 (archive): $ARCHIVE_SHA"
echo "Checksums:        $SHA_FILE"
