<p align="center">
  <img src="docs/readme/banner.svg" alt="Cairn: from camera card to a sorted DaVinci Resolve project" width="100%">
</p>

> _A cairn is a stack of stones left on a trail by someone who walked it first, marking the way for whoever comes next._

Cairn takes the tedious part out of the start of every edit. It's two tools that work together:

- **Offload**, a Mac menu-bar app, copies your camera cards onto your drive, proves every file arrived intact, and keeps track of every shoot on every drive.
- **Cairn**, a plugin inside DaVinci Resolve Studio, takes a shoot from that drive, sorts the footage into A-roll and B-roll, colour-codes it, lays out an assembly timeline and pulls the usable moments.

Cairn never cuts your video. It does the logging and the sorting, so you can start on the creative work.

<p align="center">
  <img src="docs/readme/workflow.svg" alt="Camera card to Offload to your drive to Cairn in Resolve" width="100%">
</p>

---

## Offload: get the card onto the drive, safely

Put a card in and Offload opens from the menu bar. It works out which camera shot it, how many separate shoots are on it, and how much of it is already on your drive.

|                                 |                                                                                                                                                                                                                           |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Skips what's already copied** | Files are matched by content (exact size, plus the first and last megabyte), not by name. Re-insert a half-copied card and only the rest is copied. A card it has seen before is checked again in a fraction of a second. |
| **Proves every copy**           | Each file is checksummed as it's copied, then read back from the drive and checked again before it counts. An interrupted copy never looks finished.                                                                      |
| **Never overwrites**            | A different clip with the same name gets `C0001 (2).MP4`. If you've rotated or edited a clip on the drive, the card's untouched original goes in an `Originals/` folder beside it.                                        |
| **Splits a card into shoots**   | Clips are grouped by time gaps, with a preview of each shoot: the sharpest frame from a few clips, black bars cropped, and S-Log3 footage shown as Rec.709.                                                               |
| **Files it the way you do**     | `Client / 261013 Shoot name / Footage / A7S III`, with Photos, Project and Assets alongside. Camera folders are named after the camera that shot them.                                                                    |
| **Library of every shoot**      | Search by client, shoot, camera or year across every drive, even the ones in a drawer. Rename a shoot, sort loose footage into place, and undo a sort.                                                                    |
| **Tells you when it's safe**    | "2 of 3 shoots aren't fully on Lexar 4TB yet. Don't format." Once it's all there, eject the card from the same panel.                                                                                                     |

Offload reads cards from Sony, Canon, Fujifilm, Panasonic, Nikon, Ricoh, DJI, GoPro, iPhone and more. What each brand writes to its cards, and how Offload handles it, is in [`offload/docs/camera-cards.md`](offload/docs/camera-cards.md).

---

## Cairn: from shoot folder to a ready-to-cut project

Drop a shoot folder (or a shoot Offload just copied) onto the Cairn panel in Resolve.

1. **Ingest.** The footage is organised on disk if it needs to be, and imported into Resolve with a bin per camera.
2. **Classify.** Every clip is sorted into A-roll or B-roll by what's in the picture.
3. **Colour-code.** Clips are coloured in the media pool so you can see the split at a glance.
4. **Build the assembly timeline.** A new timeline at the footage's own resolution and frame rate, with A-roll on V1 and B-roll on V2.
5. **Pull selects.** Every clip is scored for focus, exposure and steadiness, and the strongest moments are laid out on their own timeline.

<p align="center">
  <img src="docs/readme/classify.svg" alt="Clips classified by faces and motion, coloured, and laid out on an assembly timeline" width="100%">
</p>

Classification runs entirely on your Mac. It samples frames from each clip, looks for faces and measures how much the picture moves (a locked-off interview barely does), then compares the clip with ones you've labelled before. Audio isn't used, because a live mic on the subject during B-roll would fool it. When Cairn isn't sure, it marks the clip yellow for a one-click check rather than guessing.

<p align="center">
  <img src="docs/readme/selects.svg" alt="A score line across a clip, with the high runs marked as selects" width="100%">
</p>

Selects needs no AI model: the same footage and settings always give the same selects. Pick a preset (All-Rounder, Talking Head, Social Media, Action / Sport), tune it without re-analysing, and build the selects timeline. Optionally, Cairn can learn your taste from the selects you keep, using your own Anthropic API key.

---

## What Cairn will never do

- **Change your footage.** Source media is only ever read.
- **Delete anything outright.** Tidying moves files; card leftovers go to the drive's Bin, where Undo can bring them back.
- **Send your footage anywhere.** Copying, classification and selects all run locally. The only network call is the optional taste learning, which sends number summaries of the clips you rated, never footage or file names.
- **Store your API key in plain text.** It's kept encrypted with your system keychain.

---

## Getting started

**Offload** needs macOS 14 or later and the Xcode command-line tools. **Cairn** needs [DaVinci Resolve Studio](https://www.blackmagicdesign.com/products/davinciresolve) with external scripting turned on (Preferences › System › General), Node.js, and Python 3.11 or later.

### Offload

```bash
cd offload
./bundle.sh
open ~/Applications/Offload.app
```

`bundle.sh` builds the app and installs it to `~/Applications`. It lives in the menu bar; there's no Dock icon.

### Cairn

```bash
npm install
pip install -r src/workers/requirements.txt
npm run install-plugin
```

Restart Resolve, then open **Workspace › Workflow Integrations › Cairn**.

### Tests

```bash
npm test                              # UI and orchestrator (Vitest)
pytest tests/python                   # Python workers
cd offload && swift run OffloadSelfTest   # Offload
```

---

## Project layout

| Folder              | What's in it                                                                                         |
| ------------------- | ---------------------------------------------------------------------------------------------------- |
| `offload/`          | The Offload Mac app (Swift). `OffloadCore` holds the copying, checking, sorting and catalogue logic. |
| `src/ui/`           | Cairn's panel inside Resolve (Svelte).                                                               |
| `src/orchestrator/` | The Electron side of the plugin: Resolve API, settings, and the bridge to the Python workers.        |
| `src/workers/`      | The Python workers: ingest, classify, timeline, selects.                                             |
| `specs/`            | A detailed spec for each stage.                                                                      |
| `packaging/`        | Plugin install and installer builds.                                                                 |
| `tests/`            | Vitest and pytest suites.                                                                            |

Built by Aidan Mascaro, a freelance videographer in Perth, WA.
