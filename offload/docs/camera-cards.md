# Camera cards — reference

What every camera brand writes to its cards, how Offload tells the brand and model, and
which files it keeps. Offload's rules live in `Sources/OffloadCore/Card.swift` and
`CameraInfo.swift`. **Update this file and those together.**

Researched 27 Oct 2026. Each fact is tagged:

| Tag     | Meaning                                                                                        |
| ------- | ---------------------------------------------------------------------------------------------- |
| **[V]** | Checked against an official manual, help guide or spec                                         |
| **[S]** | Checked against a secondary source (asset-management vendor docs, the PRONOM registry, forums) |
| **[U]** | Unverified: general knowledge, not yet seen on a real card                                     |

## Learning from new cards

Offload adds every new combination of camera and card layout to
`~/Library/Application Support/Offload/cards-seen.md`. Each entry records the model, the
folders, the file types it copied, and any file types it **didn't recognise**. Those
unrecognised files are not copied, and the card shows an orange warning listing them.

When a friend's camera turns up something new, ask Claude to "fold cards-seen.md into
camera-cards.md". It will:

1. Read the new entries.
2. Add the brand, folders and file types here, and to `Card.swift`.
3. Clear the **[U]** tags that the card has now confirmed.

Cards seen so far: Sony A7S III / FX3 (XAVC S in `PRIVATE/M4ROOT`), Ricoh GR III
(`DCIM/100RICOH`).

---

## How Offload reads a card

**1. Is it a card?**

It is if any of these is at the volume root: `DCIM`, `PRIVATE/M4ROOT`, `M4ROOT`,
`XDROOT`, `BPAV`, `PRIVATE/AVCHD`, `AVCHD`, `CONTENTS/CLIPS001`, `CONTENTS/VIDEO`,
`PRIVATE/PANA_GRP` or `FOLDER01`. A `*.RDM` folder or a `*.braw` file at the root also
counts. Drives that look like cards are kept out of the Library.

**2. Which camera?** Offload tries these in order:

1. Sony's per-clip XML (gives model and serial).
2. The EXIF of the first photo.
3. The model tag inside the first clip (QuickTime/MP4).
4. A brand hint from the folder names, e.g. `100CANON` means "Canon". There's no serial, so this is a last resort.

**3. What gets copied:**

| Class                          | Kept?                                  | Examples                                                                                                                                                            |
| ------------------------------ | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Media                          | yes                                    | video, photos and raw, recorder WAV/MP3                                                                                                                             |
| Per-clip sidecars              | yes, in `<Camera>/Metadata`            | Sony `M01.XML`, Canon `.CIF/.CPF/.SIF`, DJI `.SRT`, RED `.RMD`, BRAW `.sidecar`, Nikon `.NDF`, Zoom `.hprj`, iPhone `.AAE`                                          |
| Thumbnails and low-res proxies | yes, in `<Camera>/Thumbnails`          | `.THM`, Sony `THMBNL/*.JPG`, DJI `.LRF`, GoPro/Insta360 `.LRV`                                                                                                      |
| Photo edits                    | yes, in Photos                         | `.xmp .pp3 .dop .cos .on1`                                                                                                                                          |
| Card housekeeping              | no (never deleted from the card)       | Sony `MEDIAPRO.XML`, `CUEUP.XML`, `.BIN`, `.IND`, `AVF_INFO`; DPOF `.MRK`; Nikon `NC_FLLST.DAT`; Panasonic `BACKUP.TMP`; Tascam `.sys`; in-camera LUT `.cube` files |
| Unknown                        | **no, flagged on the card and logged** | anything else                                                                                                                                                       |

A sidecar is only kept when its clip is on the card. Sony's card-wide XML is skipped.

When a Sony card was copied by hand instead (Finder drag), its housekeeping ends up in the
shoot folder. Tidy's sort page lists those files under **Goes in the Bin**
(`Card.isSonyHousekeeping`): only files inside `PRIVATE/` or `M4ROOT/`, never a clip's own
XML or thumbnail. Sort moves them to the drive's Bin and removes folders that leaves empty;
Undo brings them back. Footage can't be dragged into that row.

**4. Trees that are copied whole:**

`PRIVATE/AVCHD`, `AVCHD`, `XDROOT`, `BPAV`, `CONTENTS` (Canon XF, Panasonic P2),
`PRIVATE/PANA_GRP` and `*.RDM` are copied file for file, keeping their paths under
`Footage/<Camera>/`. Editors need their index and playlist files to join clips that are
split across several files. Sort never takes these trees apart.

Sony's XAVC `M4ROOT` is **not** copied whole: each clip stands alone, and its junk is skipped.

---

## Rules for every card

### The DCF baseline [V]

Most stills cameras, phones and action cams follow the DCF standard (JEITA CP-3461B):

- `DCIM/` holds the photos.
- Folders are three digits from 100 to 999, then 5 free characters (`100MSDCF`, `100CANON`).
- Files are 4 free characters, then 4 digits from 0001 to 9999.
- A leading `_` means the photo is in Adobe RGB (`_DSC0001`, `_MG_0001`, `_DSF0001`).

Folder suffixes and file prefixes are only **hints**: most cameras let you rename them.
Confirm the camera from the metadata inside the files.

Source: https://en.wikipedia.org/wiki/Design_rule_for_Camera_File_system

### Junk the computer adds

- macOS: `.Spotlight-V100`, `.fseventsd`, `.Trashes`, `.DS_Store`, `._*`
- Windows: `System Volume Information`, `$RECYCLE.BIN`
- Android: `DCIM/.thumbnails`, `.nomedia`

Offload skips all hidden files.

### Gotchas that apply to every brand

| Gotcha                                 | Cameras                                                                                                | What to do                                                                                |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| Clips split across several files       | GoPro chapters, RED `_00n.R3D`, AVCHD `.MTS`, Canon XF, DJI, Zoom over 2 GB, FAT32 cards at 4 GB       | Never show one piece as its own shot; keep structured trees whole                         |
| Proxies                                | Sony `S03`; Nikon `.MP4` with the same name as the `.NEV`; GoPro `GL…LRV`; DJI `.LRF`; Insta360 `LRV_` | Keep the proxy beside its master, and don't count it as a clip                            |
| Duplicates on two cards                | Sony, Canon, Lumix, Fuji, Nikon dual-slot recording                                                    | Offload already de-duplicates by content                                                  |
| File counters restart                  | Every DCF camera; Fuji wraps 9999 → 9001; Lumix, OM and Fuji reset on format                           | Never assume a file name is unique within a shoot                                         |
| Same names on two cameras              | `C0001.MP4`, `DSC_0001`, `GX010001`, `ZOOM0001`                                                        | One folder per camera. Same-named files get " (2)"                                        |
| Renamed root folders and file prefixes | Sony "Cam ID + Reel#", Lumix, Fuji, OM, Ricoh, Pentax                                                  | Identify the camera from metadata, not from the names                                     |
| Model codes that aren't product names  | DJI `FC3582`, Sony `ILCE-7SM3`                                                                         | Offload names Sony bodies itself (A7S III); rename anything else once and it's remembered |

---

## Sony

**Alpha, ZV, FX3 and FX30 (XAVC S / HS / S-I)**

```
PRIVATE/M4ROOT/          (CFexpress Type A: M4ROOT/ at the root)
  CLIP/    C0001.MP4  C0001M01.XML     media + per-clip XML (model, serial, S-Log3)
  SUB/     C0001S03.MP4                proxy                                    [V]
  THMBNL/  C0001T01.JPG                thumbnail                                [S]
  GENERAL/ MEDIAPRO.XML STATUS.BIN     card database (skipped)                  [U]
PRIVATE/SONY/SONYCARD.IND              card ID database (skipped)               [U]
PRIVATE/AVCHD/…                        AVCHD mode (copied whole)                [S]
DCIM/100MSDCF/ DSC00001.JPG .ARW .HIF  stills                                   [V]
AVF_INFO/ *.BNP .INP .INT .BIN         old PlayMemories database (skipped)      [U]
MP_ROOT/100ANV01/MAH00001.MP4          legacy MP4 on older bodies               [V]
```

- The proxy file name is the clip name plus `S03`, in `SUB/`. **[V]**
- `M4ROOT` can be renamed to "Cam ID + Reel#". **[V]**
- Some bodies record X-OCN into `PRIVATE/CINEROOT/Sub` or `CINEROOT/Sub`. **[V]** Not yet handled; add it when one turns up.

**FX6, FX9, Burano and Venice (XAVC in MXF)**

```
XDROOT/ Clip/*.MXF + M01.XML, Sub/, Thmbnl/, Take/, Edit/, General/,
        MEDIAPRO.XML, CUEUP.XML, DISCMETA.XML           (copied whole)  [S]
BPAV/CLPR/<clip>/*.MP4 *.SMI *.XML   (older XDCAM EX)    (copied whole)  [S]
```

**How to tell it's Sony:** the `<Device manufacturer modelName serialNo>` element in
`M01.XML`, which is also embedded in the MP4; EXIF `Make=SONY`; the folders `M4ROOT`,
`XDROOT` or `100MSDCF`.

Sources: https://helpguide.sony.net/ilc/2630/v1/en/contents/proxy_shooting.html ·
https://helpguide.sony.net/ilc/1420/v1/en/contents/TP0000226684.html ·
https://catdv-docs.services.quantum.com/player-technologies/Camera-formats,-containers,-and-codecs.html

## Canon

**EOS stills and hybrid bodies**

```
DCIM/100CANON/  IMG_0001.JPG .CR3 (.CR2 on older bodies) .HIF   MVI_0001.MP4   _MG_0001.*   [U]
MISC/           AUTPRINT.MRK (print orders, skipped)                                         [U]
```

The R5 Mark II and C-style hybrids also write `XFVC/` (MP4), `CRM/` (Cinema RAW Light
`.CRM`) and `REEL_****` folders. **[S]**

**Cinema EOS (XF / XF-AVC), copied whole**

```
CONTENTS/CLIPS001/INDEX.MIF
  AA0001/ AA000101.MXF .CIF .CPF .XML .THM .SIF                                 [S]
```

Newer bodies (C70, C400, C80) name clips `A001C001_YYMMDDxx.MXF`. The folder layout is unconfirmed. **[U]**

- **How to tell:** EXIF or QuickTime `Make=Canon`; `.CIF` files (they hold the camera type and serial); the folders `CONTENTS/CLIPS001`, `100CANON` or `XFVC`.
- **Gotcha:** EOS bodies can't rename movie files, so two bodies always clash (`MVI_0001`). **[S]**

Sources: https://www.nationalarchives.gov.uk/PRONOM/fmt/1750 ·
https://backscatter.com/images/article/content/Canon-EOS-R5-II/Canon-EOS-R5-final-Mark-II-Specspdf.pdf

## Panasonic Lumix

```
DCIM/100_PANA/ P1000001.JPG .RW2 .MOV .MP4   (4K/6K Photo bursts are clips)    [U]
PRIVATE/AVCHD/BDMV/…   (AVCHD mode, copied whole)                              [S]
```

**Pro camcorders (HC-X, AG-CX), copied whole:**

```
PRIVATE/PANA_GRP/001RCQAM/…   letters 4–8 encode the format (R = UHD, P = MP4, Q = MOV)  [V]
                 INDEX.DAT    clip list (kept inside the tree)                          [V]
                 BACKUP.TMP                                                             [V]
```

P2 cards use `CONTENTS/VIDEO/*.MXF`, `CONTENTS/AUDIO`, `CONTENTS/CLIP/*.XML` and `LASTCLIP.TXT`. **[S]**

- **How to tell:** EXIF `Make=Panasonic`, `Model=DC-S5M2`; folder `100_PANA`; file prefix `P1`.
- Folder and file names can be changed in the camera. **[V]**
- Folder counters reset when the card is formatted. **[V]**

Sources: https://help.na.panasonic.com/answers/how-to-set-the-folder-and-file-names-lumix/ ·
https://pro-av.panasonic.net/manual/html/AG-CX350EJ(DVQP1833ZA)_E/chapter03_06_07.htm

## Fujifilm

```
DCIM/100_FUJI/ DSCF0001.JPG .RAF .HIF .MOV .MP4    _DSF0001.* (Adobe RGB)   [V prefix, U folder]
```

- **How to tell:** EXIF `Make=FUJIFILM`; folder suffix `FUJI`; file prefix `DSCF`.
- F-Log is a setting inside the MOV, not a separate file.
- Frame numbers wrap from 9999 to 9001. **[S]**

Source: https://www.manualsdir.com/manuals/849469/fujifilm-x100f.html?page=140

## Nikon

```
DCIM/100NCZ_8/ (Z bodies)   100ND850/ (DSLRs)   100NIKON/ (Coolpix)               [U]
  DSC_0001.NEF .JPG .HIF .MOV .MP4  .NEV (N-RAW)                                  [V]
  DSC_0001.MP4  proxy of an N-RAW / ProRes RAW clip (same name, .MP4)             [V]
  _DSC0001.*  (Adobe RGB)    DSC_0001.NDF dust-off data (kept)                    [V]
DCIM/NC_FLLST.DAT  file-list database (skipped)                                   [U]
```

- **How to tell:** EXIF `Make=NIKON CORPORATION`, `Model=NIKON Z 8`; folder suffix `NCZ_` or `ND`.
- **Gotcha:** the RAW clip and its MP4 proxy share a name. Both are copied; the proxy isn't a second shot. Not yet paired automatically.

Source: https://onlinemanual.nikonimglib.com/z8/en/psm_file_naming_116.html

## Ricoh GR / Pentax

```
DCIM/100RICOH/ R0000001.DNG .JPG .MOV   (folder can be date-based: 100_1018)   [S]
DCIM/1xx_PENTX/ IMGP0001.JPG .PEF .DNG .MOV                                     [U]
```

- **How to tell:** EXIF `Make=RICOH IMAGING COMPANY, LTD.`, `Model=RICOH GR III`.
- **Gotcha:** date-based folder names don't name the brand.

Source: https://www.adorama.com/col/productManuals/IRCGR3XU.pdf

## Olympus / OM System

```
DCIM/100OLYMP/ (Olympus)  100OMSYS/ (OM System)                        [U]
  P1010001.JPG .ORF .MOV    _1010001.* (Adobe RGB)    .ORI (High Res companion raw)  [S]
```

- **How to tell:** EXIF `Make=OLYMPUS CORPORATION` or `OM Digital Solutions`.
- **Gotcha:** the month/day file-name option repeats every year. **[V]**

Source: https://learning.omsystem.com/OM-1MarkII/zz_html_manual/en/file_name_238.html

## Leica

```
DCIM/100LEICA/ L1000001.DNG .JPG .MP4 .MOV        [U — needs a real card]
```

- **How to tell:** EXIF `Make=Leica Camera AG`.

## GoPro

```
DCIM/100GOPRO/
  GX010001.MP4   HEVC, chapter 01 of clip 0001       (GH = H.264, GS = 360)   [S]
  GX020001.MP4   chapter 02 of the same clip                                 [S]
  GL010001.LRV   low-res proxy        GX010001.THM   thumbnail               [S]
  GOPR0001.JPG / .GPR (raw)                                                  [U]
```

- **How to tell:** folder `100GOPRO`; the model is in the MP4 metadata (e.g. "HERO12 Black").
- **Gotchas:**
  - Chapters sort out of order alphabetically. Group by the last 4 digits, then the chapter number.
  - Chapters are about 4 GB, or about 12 GB on HERO11 and later.
  - Every GoPro starts at 0001.

Sources: https://havecamerawilltravel.com/gopro/gopro-video-lrv-thm-files/ ·
https://havecamerawilltravel.com/gopro/gopro-split-video-files/

## DJI (drones, Osmo Action, Osmo Pocket)

```
Older:  DCIM/100MEDIA/ DJI_0001.MP4 .JPG .DNG  DJI_0001.SRT (telemetry)           [S]
Newer:  DCIM/DJI_001/  DJI_20240225080944_0032_D.MP4 .LRF (proxy) .SRT            [S]
MISC/   logs and index (skipped)                                                 [U]
```

- **How to tell:** `Make=DJI`; folder `100MEDIA` or `DJI_xxx`; any `.SRT` or `.LRF` file.
- **Gotcha:** the model is often a code (`FC3582`). Rename it once and Offload remembers it.

Sources: https://mavicpilots.com/threads/folder-structure-on-sd-card.44458 ·
https://mavicpilots.com/threads/extra-files-created-on-media-card.142315

## Insta360

```
DCIM/Camera01/
  VID_20240416_213000_00_001.insv   360 video (X3: a second _10_ file for the other lens)  [V]
  LRV_20240416_213000_01_001.lrv    proxy                                                  [V]
  IMG_…_00_001.insp / .dng          photo                                                  [V]
```

- **Gotcha:** keep X3 lens pairs together and never rename them, or Insta360 Studio can't stitch them. **[S]**

Source: https://onlinemanual.insta360.com/x5/en-us/operating-tutorials/storage/fileformat

## Blackmagic (BRAW)

```
<card root>/ A001_08241530_C001.braw  .sidecar (JSON settings)  .mov (ProRes)  Proxy/   [U layout]
```

- **How to tell:** `.braw` files at the card root, with no `DCIM` folder.

Source: https://www.blackmagicdesign.com/developer/products/braw

## RED (copied whole)

```
A001_0402XX.RDM/ A001_C001_0402AB.RDC/ _001.R3D _002.R3D (≈4 GB pieces)  .RMD  .mov/.mxf   [V]
```

Never copy only `_001.R3D`, and keep the whole `.RDM` folder together. **[S]**

Source: https://docs.red.com/955-0047/MediaOperationGuide/Content/5_Eject_And_Format_Media/Clip_Naming_Convention.htm

## ARRI

```
<reel>/ A001C001_240101_R2EC.mov/.mxf    (ALEXA 35: A_0001C001_…_<p|a>.mxf)    [V]
```

These come on Codex drives, read through Codex Device Manager. Not handled yet: no folder
marker is defined. Add one when a card turns up.

Source: https://help.codex.online/content/Workflows/ALEXA_35_Workflow

## Phones

- **iPhone:** `DCIM/100APPLE/IMG_0001.HEIC .MOV .DNG`, plus `.AAE` edit files (kept). **[U]**
- **Android:** `DCIM/Camera/`. **[U]**

Phones connect over PTP or MTP, not as a drive, so Offload can't see them. Use Image Capture instead.

## Audio recorders

```
Zoom H6/H5/H4n:  FOLDER01/ZOOM0001/ ZOOM0001_LR.WAV _Tr1.WAV _BU.WAV ZOOM0001.hprj   [V]
Tascam DR-xx:    MUSIC/ *.wav  dr-1.sys (system file, skipped)                       [S]
```

- **How to tell:** the WAV `bext`/iXML fields, or the folders above.
- Offload counts WAV and MP3 files as clips and files them under the recorder's name.
- **Gotchas:**
  - Takes over 2 GB are split into several files. **[V]**
  - Tascam's `MUSIC/` folder is too generic to mark the card, so plug in a Tascam and check `cards-seen.md`.

Source: https://zoomcorp.com/documents/270/E_H6_QuickGuide_2.pdf

---

## Still to confirm on real cards

1. Does an A7-series card have `MEDIAPRO.XML` / `STATUS.BIN`?
2. Nikon Z folder suffixes.
3. Lumix default folder name, and whether any Lumix writes `PANA_GRP`.
4. Leica and Pentax folders and file prefixes.
5. The XF-AVC layout on newer Cinema EOS bodies.
6. Blackmagic proxy folder.
7. What's in the DJI and GoPro `MISC/` folders.
8. Tascam file naming.
