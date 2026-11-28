# A6000 menu: can a native "Picture Profile" entry be added from RAM? (S0 static RE, FW 3.21)

> ILCE-6000 (CXD90014) research by voxivoid, from [voxivoid/a6000-qemu](https://github.com/voxivoid/a6000-qemu) `docs/A6000_MENU_INSERT.md` (commit `62cf3bc`). MIT. Tools: [`mods/a6000/look/`](../../mods/a6000/look/README.md). No CXD90045 claim.

Status: static analysis only, nothing run on emu or camera. Lib dir =
`firmware/unpacked/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked_unpacked/lib`.
Helpers: `mods/a6000/look/tools/elfdis.py <elf> addr:len...` (Thumb disasm by vaddr), `mods/a6000/look/tools/menutree.py <elf> <vaddr>` (dump a menu node tree).

## 1. Menu node struct (`CmnViewMenuItemStruct`, 0x28 bytes, recursive)
Decoded from `CmnViewMenuData` accessors in libObj.so (getId 0x1256cd, getExecType 0x1257af, getOptionNum 0x1257bf,
getCountAll 0x125b17, getSubItem 0x125b2f, getIndexFromId 0x1265d5):

| Off | Meaning |
|---|---|
| +0x00 | ptr to child array (array of 0x28-byte nodes) or 0 |
| +0x04 | u8 child count |
| +0x08 | id (int) |
| +0x0c,+0x10,+0x14,+0x18 | string resids `0x02000000 | strid` (or 0xffffffff = unused); text/line2/... |
| +0x1c..+0x1d | flags word (0x200 -> exec type byte at +0x1d = 2 for plain items) |
| +0x20 | option count |
Text is fetched through a virtual of the CmnViewMenuData subclass (vtable +0x38), item index stride 0x28.

## 2. Where tables live
* Statically initialised **`.data`** in each `viewUnifiedN.so` (RW: viewUnified2 `.data` 0x4a10d8..0x4c1050, viewUnified4
  `.data` 0x19a000+). No symbols (locals stripped). Handed to `CmnMenuTableUtil::initMenuData(CmnViewMenuData**, item*)`
  (defined in viewUnified2.so itself, GOT 0x49ba34, PLT 0xe754c; 3 callers 0x1067be, 0x10bb1e, 0x26a062 with tables
  at 0x4a21e4 (20 items), 0x4a2e1a, 0x4bc938 (root id 97, 19 items)). Tables are patchable without mprotect games
  except the `.rodata` ones.
* String resid -> text: `share/app/string_english.uxc` entries are `3-byte LE id, 0x1e, text\0`.
  "Picture Profile" id **0x04f6d9** (resid 0x0204f6d9), "Creative Style" 0x04e145/0x04e561/0x04e791, "Picture Effect" 0x04e5be,
  "Cinematone" present. Label strings for a PP item already exist -> no uxc decode needed for the text.

## 3. What these tables actually are (so far)
* viewUnified4 `.data` 0x1b22b4..0x1b23f4 etc: option/assignable-function lists (ids 0x0e..0x19 incl. Creative Style 0x11,
  Picture Effect 0x12) = Custom-key / Fn assignment lists, not the main MENU tabs.
* No stock node references resid 0x0204f6d9 anywhere in lib/*.so (Picture Profile string exists but is unused).
* The MENU-button pages are NOT yet located. They are likely driven by the `CmnViewSettingNode` tree in
  `CautionConfig.so` (4119 node syms, nodes in .bss, `NumOf*` in .data, properties in .rodata) + layout
  `share/app/master_camera.uxc` (undecoded uxc v7). That means inserting a visible page item may need all of: a node
  in the settings tree, an entry in the layout, and view code.

## 4. Verdict so far
* Struct + tables: understood and patchable (RW data), label string exists.
* Native insertion into the MENU pages: unproven; blocked on locating the main-menu tree (settings nodes vs layout).
* Next (S0 remainder): locate which view/table or settings node renders "Creative Style" inside the shooting-menu tab;
  find how master_camera.uxc binds a row to a node id; check a6300 viewPictureProfile for the declaration shape.

## 5. Update: setting pages = per-view static tables; PP strings all present
* viewUnified2.so `.data` table **0x4bc938** (root id 97, 19 children @0x4bc640) is the **Creative Style picker**:
  ids 0..12 Standard, Vivid, Neutral, Clear, Deep, Light, Portrait, Landscape, Sunset, Night Scene, Autumn leaves,
  Black & White, Sepia; ids 13..18 have empty labels (Style Box 1..6, text filled dynamically). Table **0x4a21e4** = Scene
  Selection (20 items). So each setting page is its own table; children text = resids at +0xc/+0x14 (label / description).
  vaddr->file offset in the RW segment = vaddr - 0x8000.
* The A6000 `string_english.uxc` (shared string table) contains the **whole PP string set**: PP1..PP7 (0x4f6d5,0x4f6d3,
  0x4f6d4,0x4f6d1,0x4f6d2,0x4f716,0x4f717), S-Log2 0x4f6cf, Cine1..4 0x4f6bb/0x4f6b9/0x4f6ba/0x4f6be, ITU709 0x4f718,
  Cinema 0x4f6f9, Knee 0x4f6c0, Black Gamma 0x4f738, Gamma 0x4f73e, Black Level 0x4f72a, "Picture Profile" 0x4f6d9.
  (Not proof of function: the table is shared across bodies.) Labels for a PP page are free; no string work needed.
* The MENU-tab row for Creative Style does not reference resid 0x0204e561 in any lib or .uxc -> row text arrives by another
  path (layout/uxc index or runtime string-id), still to be traced.

## 6. Practical RAM-only route that falls out of this
Re-label/extend the Creative Style picker table (RW .data, no mprotect): swap label resids of spare rows (e.g. Style Box
slots 13..18 or Sepia/Autumn leaves) to PP1..PP6 and the page title/row; a poller (pphook-style) reads the selected
Creative Style value and loads the matching gamma preset into SRAM 0xF3215300. Looks native (real menu, real cursor) but
the stored setting is still Creative Style; stock Creative Style effect for that id must be neutral/ignored.

## 7. Emulator checks (S1/S2 partial, FW 3.21 in QEMU)
* viewUnified2.so is mapped in im.elf (base 0x15ecc000 in that boot; RW seg = base + vaddr). The live Creative Style table
  (vaddr 0x4bc938, children 0x4bc640) **matches the static dump byte for byte**; the Style Box rows 13..18 have
  runtime-filled child pointers (0x16388960...) = their own sub-lists of 13 items.
* **Live read/write works from the guest root shell, no hook needed**: `busybox dd if=/proc/<im.elf pid>/mem bs=1 skip=$((base+vaddr))`
  reads; `dd of=/proc/<pid>/mem bs=1 seek=...` (no `conv=notrunc`, busybox 1.13 rejects it) writes. Pull results with
  `emulators/a6000/tools/a6kshell.py pull`. Row 12 ("Sepia") label word at vaddr 0x4bc82c was overwritten 0x0204e06b -> 0x0204f6d5 (PP1)
  and read back OK; im.elf kept running.
* NOT yet verified: that the Creative Style page then *shows* "PP1". Driving the emulator menu with camui was flaky
  (the arrow `down` key stopped registering, wheel `bracket_right` works, cursor wrapped unexpectedly). Reach the page
  via Fn menu or MENU > Camera tab > Creative Style and screenshot with a wait of several seconds per key.
* Restore: write 0x0204e06b back at the same address (or restart the emulator).

## 8. Real camera result (A6000 3.21, Wi-Fi telnet root shell, `mods/a6000/look/tools/menu_relabel.py`)
* `check` (read-only) matched the 3.21 layout: im.elf pid 148, viewUnified2.so base 0x1667c000, root child ptr = base+0x4bc640,
  19 children, id 97, row 12 label 0x0204e06b.
* `apply` wrote 0x0204f6d5 at vaddr 0x4bc82c via `/proc/<pid>/mem` (camera kernel allows it), readback OK.
* **Verified on screen: the Creative Style picker row now reads "PP1"** where "Sepia" was. The row's **icon is still Sepia's**:
  icons come from the +0x10 word (0x0205c8c3 for row 12; rows 0..12 use 0x0205c8b7..0x0205c8c3, i.e. 0x0205 = image
  resid space), so label (+0xc) and icon (+0x10) are independent fields. Description text is +0x14 (0x0204e8b5) and
  a second image/guide resid is +0x18 (0x0205c868).
* RAM only: nothing written to flash; reboot/battery pull restores.

## 9. Selection detection (how to know "PP1 / slot 12 is the current Creative Style")
* The CautionConfig node `cmnViewSettingNodeRootCreativeStyle` (vaddr 0x1c8e84, 40 B: vptr, subnodes, id 0x5b, props, 13 items)
  is **structure only**; unchanged between Standard and Vivid on a real camera. Not the value.
* 3-way RAM diff of libObj RW+bss (vaddr 0xfd2000..0x10e3000, camera on Standard / Vivid / Neutral, 64 KB chunks pulled
  to the host): 39 words differ, all image-pipeline noise or non-id-like (0x10df94c = 1/22/9, 0x10ab6e4 = 1/3/1). **No
  word reads 0/1/2.** The stored style is not in libObj's data area (heap / another lib / read through a call).
* Code path: `WrapperCreativeStyle::getCreativeStyle` (viewUnified2.so 0x1c0dc1) gets a singleton via `0x12e958(product)`
  (guard+static in .bss, PIC/GOT), then calls `obj->vtable[0x14](obj, 0x19, &val, ...)` (group id 0x19 = Creative Style); the
  value is mapped by 0x1c0ce2 / 0x1c0d64 (wrapper enum <-> style index, 0..13 with 0x62 special). Setter = vtable[0x3c].
  So the value is *fetched through a call*, not a plain field: reading it needs in-process code (pphook-class injected
  .so using the same group get) or a setter hook. Oein's a5000 `grpcmd_so.c` reads/writes option groups the same way.

## 10. Calling the style getter from an injected helper: what static RE says (not finished)
* `WrapperCreativeStyle::getCreativeStyle(this, product, int* styleOut, int* a, int* b, int* c, int* d, WRAPPER_CREATIVE_STYLE in, int n)`
  is *exported* from viewUnified2.so (0x1c0dc1) and used by the view code in viewUnified2.so and viewUnified4.so (callers
  viewUnified4 0x1011d0, 0x103d06 through PLT 0x3c8f8). It is not "get the current style": `in` is a view/param id mapped
  by viewUnified4 0x100c48 (ids 0x96..0x15c) to a wrapper enum, and results land in view members (e.g. `this+0x16e0`,
  `+0x174..+0x180`); the actual value is fetched by `singleton->vtable[0x14](obj, 0x19, ...)` (singleton from 0x12e958,
  `static` in viewUnified2 .bss, `0x12e950` binds `product` to it).
* To use it from an injected .so one still needs (a) the singleton address (GOT slot read in 0x12e958), (b) a valid
  `product` pointer, and (c) the `in`/`n` argument convention. None of these is validated yet, and the injector
  (`provision/pploader`) has only run in the emulator.
